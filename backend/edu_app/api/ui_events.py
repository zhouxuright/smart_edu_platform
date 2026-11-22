"""前端行为埋点接口。

只做两件事：写入一条事件、给运营看一份聚合。刻意保持轻量——
埋点不该成为页面加载的负担，所以前端是 fire-and-forget，失败也不打扰用户。
"""
import logging
import re
from datetime import datetime, timedelta, UTC

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.core.database import get_db
from edu_app.models.learning import UiEvent
from edu_app.models.user import User
from edu_app.api.endpoints import get_current_user

router = APIRouter()

EVENT_RE = re.compile(r"^[a-z0-9_.:]{1,48}$")
MAX_EVENTS_PER_DAY = 500


class UiEventCreate(BaseModel):
    event: str = Field(..., max_length=48)
    path: str | None = Field(default=None, max_length=200)
    meta: dict | None = None


@router.post("/events/ui")
async def create_ui_event(
    payload: UiEventCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not EVENT_RE.match(payload.event or ""):
        raise HTTPException(status_code=400, detail="事件名不合法")
    if payload.meta is not None and len(payload.meta) > 20:
        raise HTTPException(status_code=400, detail="附加信息过多")

    user_id = current_user.id
    day_start = datetime.now(UTC) - timedelta(hours=24)
    used = await db.scalar(
        select(func.count(UiEvent.id)).where(
            UiEvent.user_id == user_id, UiEvent.created_at >= day_start
        )
    )
    # 防止前端死循环或恶意刷量把表打爆
    if (used or 0) >= MAX_EVENTS_PER_DAY:
        return {"recorded": False, "reason": "daily_limit"}

    db.add(
        UiEvent(
            user_id=user_id,
            event=payload.event,
            path=(payload.path or "")[:200] or None,
            meta=payload.meta,
        )
    )
    try:
        await db.commit()
    except Exception as exc:  # 埋点失败不影响业务，只记日志
        await db.rollback()
        logging.warning("记录 UI 事件失败: %s", exc)
        return {"recorded": False}
    return {"recorded": True}


@router.get("/events/ui/summary")
async def ui_event_summary(
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """最近 N 天的页面浏览与动作次数。管理员看全量，学生只看自己的。"""
    if not (1 <= days <= 90):
        raise HTTPException(status_code=400, detail="days 超出范围")
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(UiEvent.event, UiEvent.path, func.count(UiEvent.id).label("n"))
        .where(UiEvent.created_at >= since)
        .group_by(UiEvent.event, UiEvent.path)
        .order_by(func.count(UiEvent.id).desc())
    )
    if getattr(current_user, "role", "student") != "admin":
        stmt = stmt.where(UiEvent.user_id == current_user.id)
    rows = (await db.execute(stmt.limit(200))).all()
    return {
        "days": days,
        "items": [
            {"event": r[0], "path": r[1], "count": int(r[2])}
            for r in rows
        ],
    }
