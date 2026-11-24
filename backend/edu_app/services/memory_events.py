"""领域事件 → 长期记忆。

为什么不让模型来抽：错题、计划完成都是**结构化事实**，数据库里已经写得清清楚楚，
让模型再总结一遍只会引入幻觉和额外成本。模型只该负责「用户说的话」这类
非结构化输入（`services/memory.py` 的 `extract_memories`）。

三条约束，改这里的时候别破坏：

1. **不 commit。** 跟随调用方的事务。业务回滚，记忆一起回滚——
   否则会出现「错题没加成，但记忆里记了一笔」。
2. **不抛异常。** 记忆是增强不是主流程。写失败只记日志，
   绝不能让「加一道错题」这个动作失败。
3. **value 要稳定。** 别把 id、时间戳、计数拼进去，否则每次都是新版本，
   版本链退化成流水账。同一知识点反复错时值相同，`upsert_memory`
   只刷新 `last_seen_at`，不会刷出一堆行。
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.services import memory

logger = logging.getLogger(__name__)


async def _remember(
    db: AsyncSession,
    user_id: int,
    kind: str,
    key: str,
    value: str,
    source_ref: str,
) -> None:
    try:
        await memory.upsert_memory(
            db,
            user_id,
            kind,
            key,
            value,
            source="event",
            confidence=0.9,
            source_ref=source_ref,
        )
    except Exception:  # noqa: BLE001 - 记忆写失败不能拖垮业务动作
        logger.exception("event memory write failed: user=%s key=%s", user_id, key)


async def mistake_added(db: AsyncSession, user_id: int, mistake) -> None:
    """收录一道错题 → 记一条「这个知识点有未解决的错题」。

    只记知识点、不记题目内容：题干进 prompt 会让上下文迅速膨胀，
    而且模型真正需要知道的是「他弱在哪」，不是「他错的是哪道题」。
    """
    subject = (getattr(mistake, "subject", "") or "").strip()[:20] or "未分类"
    topic = (getattr(mistake, "topic", "") or "").strip()[:40]

    if not topic or topic == subject:
        key = f"subject:{subject}"
        value = f"{subject}有未解决的错题"
    else:
        key = f"topic:{topic}"
        value = f"{subject}「{topic}」有未解决的错题"

    await _remember(db, user_id, "weakness", key, value, "event:mistake")


async def mistake_resolved(db: AsyncSession, user_id: int, mistake) -> None:
    """订正一道错题 → 覆盖「这个知识点还有未解决错题」那条记忆。

    走版本链而不是删除：旧行保留并指向新行，「他什么时候补上的」还查得到。
    """
    subject = (getattr(mistake, "subject", "") or "").strip()[:20] or "未分类"
    topic = (getattr(mistake, "topic", "") or "").strip()[:40]

    if not topic or topic == subject:
        key = f"subject:{subject}"
        value = f"{subject}的错题已订正"
    else:
        key = f"topic:{topic}"
        value = f"{subject}「{topic}」的错题已订正"

    await _remember(db, user_id, "weakness", key, value, "event:mistake_resolved")


async def plan_completed(db: AsyncSession, user_id: int, plan) -> None:
    """计划全部任务做完 → 记一条进度事实。

    只在**全部完成**时写，不是每完成一个任务写一次：后者会让版本链变成
    「1/5、2/5、3/5…」，既没用又占满了 8 条记忆的展示位。
    """
    title = (getattr(plan, "title", "") or "").strip()[:40] or "当前计划"
    await _remember(db, user_id, "progress", "plan:latest", f"已完成计划「{title}」", "event:plan")
