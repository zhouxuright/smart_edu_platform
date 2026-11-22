"""投递看板 API。

核心是状态机：不是随便改字符串，而是只允许合法的流转。
这样漏斗统计才有意义——否则用户把 rejected 改成 offer，数据就废了。
"""
import logging
from datetime import datetime, UTC
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.api.endpoints import get_current_user
from edu_app.core.database import get_db
from edu_app.models.job import JobApplication
from edu_app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)

STATUS_LABEL = {
    "draft": "待投",
    "applied": "已投递",
    "screening": "笔试/筛选",
    "interview": "面试中",
    "offer": "已拿 offer",
    "rejected": "未通过",
    "withdrawn": "已撤回",
}

# 看板列的顺序，前端直接按这个渲染
STATUS_ORDER = ["draft", "applied", "screening", "interview", "offer", "rejected", "withdrawn"]

CHANNELS = {"referral", "campus", "job_board", "company_site", "other"}

# 允许的状态迁移。刻意不允许 draft 直接跳 interview：
# 中间状态丢了，漏斗就算不出转化率。真遇到跳过流程的情况，先点「已投递」再改。
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"applied", "withdrawn"},
    "applied": {"screening", "interview", "rejected", "withdrawn"},
    "screening": {"interview", "rejected", "withdrawn"},
    "interview": {"offer", "rejected", "withdrawn"},
    "offer": {"withdrawn"},
    "rejected": {"applied"},   # 同一家公司换岗位再投
    "withdrawn": {"applied"},
}

# 终态：这些状态下不再提示跟进
CLOSED_STATUS = {"offer", "rejected", "withdrawn"}


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=80)
    position: str = Field(min_length=1, max_length=120)
    channel: str = Field(default="other", max_length=24)
    status: str = Field(default="draft", max_length=16)
    location: Optional[str] = Field(default=None, max_length=60)
    salary_range: Optional[str] = Field(default=None, max_length=40)
    jd_url: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=4000)
    next_action_at: Optional[datetime] = None


class ApplicationUpdate(BaseModel):
    company: Optional[str] = Field(default=None, min_length=1, max_length=80)
    position: Optional[str] = Field(default=None, min_length=1, max_length=120)
    channel: Optional[str] = Field(default=None, max_length=24)
    status: Optional[str] = Field(default=None, max_length=16)
    location: Optional[str] = Field(default=None, max_length=60)
    salary_range: Optional[str] = Field(default=None, max_length=40)
    jd_url: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=4000)
    next_action_at: Optional[datetime] = None


def _payload(row: JobApplication) -> dict[str, Any]:
    return {
        "id": row.id,
        "company": row.company,
        "position": row.position,
        "channel": row.channel or "other",
        "status": row.status,
        "status_label": STATUS_LABEL.get(row.status, row.status),
        "location": row.location,
        "salary_range": row.salary_range,
        "jd_url": row.jd_url,
        "notes": row.notes,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "next_action_at": row.next_action_at.isoformat() if row.next_action_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _ensure_status(value: str) -> str:
    if value not in STATUS_LABEL:
        raise HTTPException(status_code=400, detail=f"未知状态：{value}")
    return value


def _ensure_channel(value: str) -> str:
    if value not in CHANNELS:
        raise HTTPException(status_code=400, detail=f"未知渠道：{value}")
    return value


@router.get("/applications")
async def list_applications(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(JobApplication).where(JobApplication.user_id == current_user.id)
    if status:
        stmt = stmt.where(JobApplication.status == _ensure_status(status))
    stmt = stmt.order_by(JobApplication.updated_at.desc()).limit(500)
    rows = (await db.execute(stmt)).scalars().all()

    return {
        "items": [_payload(r) for r in rows],
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "channels": sorted(CHANNELS),
    }


@router.post("/applications")
async def create_application(
    payload: ApplicationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status = _ensure_status(payload.status)
    channel = _ensure_channel(payload.channel)

    row = JobApplication(
        user_id=current_user.id,
        company=payload.company.strip(),
        position=payload.position.strip(),
        channel=channel,
        status=status,
        location=payload.location,
        salary_range=payload.salary_range,
        jd_url=payload.jd_url,
        notes=payload.notes,
        # 新建时只要不是待投，就视为今天投的
        applied_at=datetime.now(UTC) if status != "draft" else None,
        next_action_at=payload.next_action_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _payload(row)


@router.patch("/applications/{app_id}")
async def update_application(
    app_id: int,
    payload: ApplicationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = await db.scalar(
        select(JobApplication).where(
            JobApplication.id == app_id, JobApplication.user_id == current_user.id
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="投递记录不存在")

    data = payload.model_dump(exclude_unset=True)

    if "status" in data and data["status"] is not None:
        new_status = _ensure_status(data["status"])
        old_status = row.status
        if new_status != old_status:
            if new_status not in TRANSITIONS.get(old_status, set()):
                raise HTTPException(
                    status_code=400,
                    detail=f"不能从「{STATUS_LABEL.get(old_status, old_status)}」直接改为「{STATUS_LABEL.get(new_status, new_status)}」",
                )
            row.status = new_status
            if new_status == "applied" and not row.applied_at:
                row.applied_at = datetime.now(UTC)

    if "channel" in data and data["channel"] is not None:
        row.channel = _ensure_channel(data["channel"])

    for field in ("company", "position", "location", "salary_range", "jd_url", "notes", "next_action_at"):
        if field in data and data[field] is not None:
            setattr(row, field, data[field])

    await db.commit()
    await db.refresh(row)
    return _payload(row)


@router.delete("/applications/{app_id}")
async def delete_application(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = await db.scalar(
        select(JobApplication).where(
            JobApplication.id == app_id, JobApplication.user_id == current_user.id
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="投递记录不存在")
    await db.delete(row)
    await db.commit()
    return {"deleted": True}


@router.get("/applications/stats")
async def application_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """漏斗统计。

    分母用「真正投出去的」而不是全部记录——草稿不该拉低转化率。
    """
    rows = (
        await db.execute(
            select(JobApplication).where(JobApplication.user_id == current_user.id)
        )
    ).scalars().all()

    by_status = {s: 0 for s in STATUS_ORDER}
    by_channel: dict[str, dict[str, int]] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        ch = r.channel or "other"
        bucket = by_channel.setdefault(ch, {"total": 0, "interview": 0, "offer": 0})
        bucket["total"] += 1
        if r.status in ("interview", "offer"):
            bucket["interview"] += 1
        if r.status == "offer":
            bucket["offer"] += 1

    submitted = len([r for r in rows if r.status != "draft"])
    reached_interview = len([r for r in rows if r.status in ("interview", "offer")])
    offers = len([r for r in rows if r.status == "offer"])

    def rate(num: int, den: int) -> Optional[float]:
        return round(num / den, 3) if den else None

    now = datetime.now(UTC)
    due_followups = len(
        [
            r
            for r in rows
            if r.next_action_at
            and r.next_action_at <= now
            and r.status not in CLOSED_STATUS
        ]
    )

    return {
        "total": len(rows),
        "submitted": submitted,
        "by_status": by_status,
        "by_channel": by_channel,
        "interview_count": reached_interview,
        "offer_count": offers,
        "interview_rate": rate(reached_interview, submitted),
        "offer_rate": rate(offers, submitted),
        "due_followups": due_followups,
    }
