"""学情数据的访问范围校验。

从 `api/endpoints.py` 里搬出来的原因很实际：家长绑定上线后，这段逻辑要查库，
而它被 6 个地方用到（两个 websocket + 四个 HTTP）。留在 endpoints 里，
新加的 `api/parent_links.py` 就得反向 import endpoints，形成循环。
"""
from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.models.parent import ParentStudentLink
from edu_app.models.user import User

# 能通过绑定关系看别人学情的角色。student 不在里面——
# 学生只能看自己，这一点不能因为「他也填了 student_id」就放松。
PARENT_ROLES = ("parent", "teacher")


async def linked_student_ids(db: AsyncSession, parent_id: int) -> list[int]:
    """这个账号已经绑定的学生 id 列表。"""
    rows = await db.execute(
        select(ParentStudentLink.student_id).where(ParentStudentLink.parent_id == parent_id)
    )
    return [int(x) for x in rows.scalars().all()]


async def ensure_student_scope(
    db: AsyncSession,
    current_user: User,
    student_id,
    *,
    require_roles: Iterable[str] | None = None,
) -> int:
    """校验能否读取 `student_id` 的学情，能读就返回这个 id。

    规则按顺序判断：
    1. admin —— 全部可读（运营与排障需要）
    2. 本人 —— 永远可读
    3. 家长/老师 —— 只有**已绑定**才可读
    4. 其余 —— 403

    越权一律返回 403 而不是 404：这个 id 存不存在不重要，
    重要的是「你没有权限」，404 会让人误以为是数据没建出来。

    `require_roles` 用于「这个接口本身只对某些角色开放」的场景，
    比如家长门户，学生进来即使传自己的 id 也不该拿到一份「家长报告」。
    """
    if require_roles is not None:
        role = getattr(current_user, "role", "student")
        if role != "admin" and role not in tuple(require_roles):
            raise HTTPException(status_code=403, detail="这个功能只对家长账号开放")

    if student_id is None:
        raise HTTPException(status_code=400, detail="缺少 student_id")
    try:
        target_id = int(student_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="student_id 不合法")

    role = getattr(current_user, "role", "student")
    if role == "admin":
        return target_id
    if target_id == current_user.id:
        return target_id

    if role in PARENT_ROLES:
        linked = await db.scalar(
            select(ParentStudentLink.id).where(
                ParentStudentLink.parent_id == current_user.id,
                ParentStudentLink.student_id == target_id,
            )
        )
        if linked:
            return target_id
        raise HTTPException(
            status_code=403,
            detail="还没有绑定这个学生，先在家长门户输入孩子给你的邀请码",
        )

    raise HTTPException(status_code=403, detail="无权访问该学生的数据")
