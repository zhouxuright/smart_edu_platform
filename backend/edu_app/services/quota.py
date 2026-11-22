"""大模型调用配额。

动机：之前所有调模型的接口都没有限制，只要拿到 token 就能无限刷。
这里按「用户 + 自然日」计数，超限直接 429。

口径：
- 管理员不限（便于运营和排查）。
- 只有真正会触发模型推理的接口才计数；检索本身走本地 BGE-M3，不计。
- 先查后写会有轻微并发超发，但成本影响可忽略，换来的是实现简单、无锁。

注意：这里刻意用独立的 AsyncSessionLocal，而不是复用请求的 db。
配额计数需要 commit，如果共用 session 会让路由里的 ORM 对象过期，
后续访问属性会触发同步 IO，在异步上下文里直接抛 MissingGreenlet。
"""
import logging
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from edu_app.core.config import settings
from edu_app.core.database import AsyncSessionLocal
from edu_app.models.learning import LlmUsage
from edu_app.models.user import User

logger = logging.getLogger(__name__)

DEFAULT_DAILY_QUOTA = 30


def daily_limit() -> int:
    try:
        return max(1, int(getattr(settings, "LLM_DAILY_QUOTA", DEFAULT_DAILY_QUOTA)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_QUOTA


def _is_admin(user: User) -> bool:
    return getattr(user, "role", "student") == "admin"


async def quota_status(user: User) -> dict:
    if _is_admin(user):
        return {"unlimited": True, "used": 0, "limit": None, "remaining": None}
    day = date.today()
    async with AsyncSessionLocal() as db:
        used = await db.scalar(
            select(LlmUsage.count).where(LlmUsage.user_id == user.id, LlmUsage.day == day)
        )
    used = int(used or 0)
    limit = daily_limit()
    return {
        "unlimited": False,
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
    }


async def consume_quota(user: User, cost: int = 1) -> dict:
    """检查并扣减一次额度。超限抛 429。返回扣减后的状态。"""
    if _is_admin(user):
        return {"unlimited": True, "used": 0, "limit": None, "remaining": None}

    day = date.today()
    limit = daily_limit()

    async with AsyncSessionLocal() as db:
        used = await db.scalar(
            select(LlmUsage.count).where(LlmUsage.user_id == user.id, LlmUsage.day == day)
        )
        used = int(used or 0)

        if used + cost > limit:
            raise HTTPException(
                status_code=429,
                detail=f"今日 AI 额度已用完（{used}/{limit}），明天 0 点重置。",
            )

        # 原子自增：并发下不会互相覆盖计数
        stmt = pg_insert(LlmUsage).values(user_id=user.id, day=day, count=cost)
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "day"],
            set_={"count": LlmUsage.__table__.c.count + cost},
        )
        try:
            await db.execute(stmt)
            await db.commit()
        except Exception as exc:
            # 计数失败不应该拦住正常业务，但要留痕
            await db.rollback()
            logger.warning("记录 AI 额度失败：%s", exc)
            return {"unlimited": False, "used": used, "limit": limit, "remaining": max(0, limit - used)}

    return {"unlimited": False, "used": used + cost, "limit": limit, "remaining": max(0, limit - used - cost)}
