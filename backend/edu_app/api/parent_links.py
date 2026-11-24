"""家长-学生绑定：邀请码、绑定、解绑、查看授权关系。

为什么邀请码由**学生**生成：家长门户展示的是学生的学情，授权方应该是学生本人。
如果让家长填学生邮箱就能绑，任何知道邮箱的人都能看这个学生的成绩和错题。

三个安全细节：
1. 邀请码一次性 —— 用 `UPDATE ... WHERE used_by IS NULL` 抢占，影响 0 行就是被人用过了。
   先查再改在并发下会漏。
2. 学生可以随时撤销授权 —— `DELETE /parent/viewers/{parent_id}`。
   授权不该是单向门。
3. 绑定成功也不等于能读所有东西 —— 真正的鉴权在 `services/scope.py`，
   这里只负责建立关系。
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.core.database import get_db
from edu_app.models.parent import ParentInvite, ParentStudentLink
from edu_app.models.user import User
from edu_app.api.endpoints import get_current_user

router = APIRouter()

# 邀请码有效期。7 天够家长从容操作，又不至于让一个泄露的码长期有效
INVITE_TTL = timedelta(days=7)
# 去掉 0/O/1/I 这些容易抄错的字符——这个码是要口头或截图传的
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class InviteCreate(BaseModel):
    # 目前只有家长，留个字段是为了以后区分监护人/老师
    relation: str = "parent"


class LinkCreate(BaseModel):
    code: str
    relation: str = "parent"


def _now() -> datetime:
    return datetime.now(UTC)


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 存回来的是 naive，比较前补上 UTC，否则 TypeError。"""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# 学生侧：生成邀请码、查看谁在看我的数据、撤销授权
# ---------------------------------------------------------------------------

@router.post("/parent/invite")
async def create_invite(
    payload: InviteCreate | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """学生生成一个邀请码给家长。旧码不主动作废，各自独立过期。"""
    now = _now()
    # 碰撞概率极低（32^6 ≈ 10 亿），但唯一约束在，撞了就重试一次
    for _ in range(5):
        code = _new_code()
        exists = await db.scalar(select(ParentInvite.id).where(ParentInvite.code == code))
        if not exists:
            break
    else:
        raise HTTPException(status_code=500, detail="邀请码生成失败，请重试")

    invite = ParentInvite(
        code=code,
        student_id=current_user.id,
        created_at=now,
        expires_at=now + INVITE_TTL,
    )
    db.add(invite)
    await db.commit()

    return {
        "code": code,
        "expires_at": invite.expires_at.isoformat(),
        "expires_in_days": INVITE_TTL.days,
    }


@router.get("/parent/viewers")
async def list_viewers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """谁在看我的学情。学生随时可以核对，这是隐私的基本要求。"""
    rows = (
        await db.execute(
            select(ParentStudentLink, User)
            .join(User, User.id == ParentStudentLink.parent_id)
            .where(ParentStudentLink.student_id == current_user.id)
            .order_by(ParentStudentLink.id)
        )
    ).all()
    return {
        "items": [
            {
                "parent_id": link.parent_id,
                "name": user.full_name or user.email,
                "email": user.email,
                "relation": link.relation,
                "created_at": link.created_at.isoformat() if link.created_at else None,
            }
            for link, user in rows
        ]
    }


@router.delete("/parent/viewers/{parent_id}")
async def revoke_viewer(
    parent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """学生撤销某个家长的访问权。"""
    result = await db.execute(
        delete(ParentStudentLink).where(
            ParentStudentLink.student_id == current_user.id,
            ParentStudentLink.parent_id == parent_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="没有这条授权记录")
    return {"revoked": True}


# ---------------------------------------------------------------------------
# 家长侧：用码绑定、看孩子列表、解绑
# ---------------------------------------------------------------------------

@router.post("/parent/link")
async def bind_student(
    payload: LinkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = getattr(current_user, "role", "student")
    if role not in ("parent", "teacher"):
        raise HTTPException(
            status_code=403,
            detail="只有家长账号可以绑定学生；学生账号请直接使用自己的学情",
        )

    code = (payload.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="请填写邀请码")

    invite = (
        await db.execute(select(ParentInvite).where(ParentInvite.code == code))
    ).scalars().first()
    if not invite:
        raise HTTPException(status_code=404, detail="邀请码不存在，请让孩子重新生成")

    if invite.student_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能绑定自己")

    # 过期检查放在抢占之前：过期码不该被「用掉」，
    # 否则用户拿着过期码点一次，码就被消费了，再生成还得等
    expires_at = _aware(invite.expires_at)
    if expires_at and expires_at < _now():
        raise HTTPException(status_code=400, detail="邀请码已过期，请让孩子重新生成")

    # 抢占：只有 used_by 还是空的时候才算用掉。并发下先查再改会双双成功
    claimed = await db.execute(
        update(ParentInvite)
        .where(ParentInvite.id == invite.id, ParentInvite.used_by.is_(None))
        .values(used_by=current_user.id, used_at=_now())
    )
    if claimed.rowcount == 0:
        await db.rollback()
        raise HTTPException(status_code=409, detail="这个邀请码已经被使用过了")

    existing = (
        await db.execute(
            select(ParentStudentLink).where(
                ParentStudentLink.parent_id == current_user.id,
                ParentStudentLink.student_id == invite.student_id,
            )
        )
    ).scalars().first()
    if existing:
        await db.commit()
        return {"linked": True, "already_bound": True, "student_id": invite.student_id}

    link = ParentStudentLink(
        parent_id=current_user.id,
        student_id=invite.student_id,
        relation=(payload.relation or "parent")[:20],
        created_at=_now(),
    )
    db.add(link)
    try:
        await db.commit()
    except IntegrityError:
        # 唯一约束兜底：两个请求同时绑同一个人，第二个走到这里
        await db.rollback()
        return {"linked": True, "already_bound": True, "student_id": invite.student_id}

    return {"linked": True, "already_bound": False, "student_id": invite.student_id}


@router.get("/parent/children")
async def list_children(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        await db.execute(
            select(ParentStudentLink, User)
            .join(User, User.id == ParentStudentLink.student_id)
            .where(ParentStudentLink.parent_id == current_user.id)
            .order_by(ParentStudentLink.id)
        )
    ).all()
    return {
        "items": [
            {
                "student_id": link.student_id,
                "name": user.full_name or user.email,
                "email": user.email,
                "relation": link.relation,
                "created_at": link.created_at.isoformat() if link.created_at else None,
            }
            for link, user in rows
        ]
    }


@router.delete("/parent/children/{student_id}")
async def unbind_student(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        delete(ParentStudentLink).where(
            ParentStudentLink.parent_id == current_user.id,
            ParentStudentLink.student_id == student_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="没有绑定这个学生")
    return {"unlinked": True}
