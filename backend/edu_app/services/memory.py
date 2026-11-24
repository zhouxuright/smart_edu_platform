"""会话记忆与长期记忆。

分工要说清，否则很快就会退化成「把聊天记录全塞进 prompt」：

- **短期**（`agent_sessions` + `agent_messages`）：只服务当轮上下文。
  取最近 6 轮、每轮截 200 字，和原来的行为一致，只是从浏览器搬到了服务端。
- **长期**（`user_memories`）：只存结论，带来源和过期，检索走精确键不走向量。
- **归档**：会话闲置 30 分钟或满 20 轮，一次模型调用产出摘要 + 0~3 条记忆。
  这是唯一「每轮之外」的模型调用，成本可算。

为什么长期记忆不用向量检索：八条结构化结论比二十段相似历史更准，
而且不会把正打算拆掉的本地 embedding 依赖又绑回来。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.agents.base import get_llm
from edu_app.core.config import settings
from edu_app.core.database import AsyncSessionLocal
from edu_app.models.memory import AgentMessage, AgentSession, UserMemory
from edu_app.models.user import User
from edu_app.services.llm_gate import llm_slot

logger = logging.getLogger(__name__)

# 与前端原来的截断口径保持一致：6 轮、每轮 200 字
MAX_HISTORY_TURNS = 6
HISTORY_CHAR_LIMIT = 200

# 归档触发：闲置 30 分钟，或累计 20 轮
ARCHIVE_IDLE = timedelta(minutes=30)
ARCHIVE_TURNS = 20
# 每轮最多归档几个会话，防止积压时一次性打满模型额度
ARCHIVE_BATCH = 5
# 归档循环间隔
ARCHIVE_INTERVAL = 600

MEMORY_LIMIT = 8
# `progress` 由领域事件写入（计划完成），不走模型抽取
MEMORY_KINDS = {"preference", "fact", "weakness", "goal", "constraint", "progress"}

# 只有这几类话才触发「抽取记忆」的额外模型调用。不是每轮都抽——
# 每轮都抽等于把对话成本翻倍，而且抽出来的多半是噪声。
MEMORY_TRIGGER = re.compile(
    r"(记住|记一下|记下|请记|帮我记|以后都|以后别|我不喜欢|我更喜欢|我想考|我打算|我的目标)"
)

# 后台任务必须持有强引用，否则可能在执行途中被 GC 回收
_PENDING: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now(UTC)


def _llm_ready() -> bool:
    base = (settings.LLM_API_BASE or "").strip()
    key = (settings.LLM_API_KEY or "").strip()
    return bool(
        not getattr(settings, "LLM_DISABLE", False)
        and base
        and key
        and key.lower() != "dummy"
    )


def _parse_json_object(text: str) -> dict | None:
    """模型偶尔会套一层 ```json 或加一句解释，取第一个大括号块就够了。"""
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

def new_session_id() -> str:
    return str(uuid.uuid4())


async def get_or_create_session(
    db: AsyncSession, user: User, page: str, session_id: str | None = None
) -> AgentSession:
    """按 id 取会话；取不到就新建。

    别人的 session_id 一律当作不存在——不报错、不提示，直接开新会话。
    报「无权访问」等于告诉对方这个 id 是真实存在的。
    """
    if session_id:
        row = (
            await db.execute(
                select(AgentSession).where(
                    AgentSession.id == session_id,
                    AgentSession.user_id == user.id,
                )
            )
        ).scalars().first()
        if row:
            return row

    session = AgentSession(
        id=new_session_id(),
        user_id=user.id,
        page=page,
        status="active",
        turn_count=0,
        token_total=0,
        created_at=_now(),
        last_active_at=_now(),
    )
    db.add(session)
    await db.flush()
    return session


async def load_history(
    db: AsyncSession, session_id: str, limit: int = MAX_HISTORY_TURNS * 2
) -> list[dict[str, str]]:
    """取最近若干条消息，按时间正序返回，喂给 `_history_block`。"""
    rows = (
        await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {"role": row.role, "content": row.content}
        for row in reversed(rows)
        if row.content
    ]


async def append_turn(
    db: AsyncSession,
    session: AgentSession,
    user_text: str,
    reply: str,
    *,
    proposal_id: str | None = None,
    tool_name: str | None = None,
    tokens: int = 0,
) -> None:
    """一问一答落两条。只在一轮成功结束后调用——失败的一轮不留半条记录。"""
    now = _now()
    db.add(
        AgentMessage(
            session_id=session.id, role="user", content=user_text, created_at=now
        )
    )
    db.add(
        AgentMessage(
            session_id=session.id,
            role="assistant",
            content=reply,
            proposal_id=proposal_id,
            tool_name=tool_name,
            tokens=int(tokens or 0),
            created_at=now,
        )
    )
    session.turn_count = (session.turn_count or 0) + 1
    session.token_total = (session.token_total or 0) + int(tokens or 0)
    session.last_active_at = now
    if not session.title:
        session.title = user_text[:40]


async def load_session_messages(
    db: AsyncSession, session_id: str, limit: int = 100
) -> list[AgentMessage]:
    return (
        await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.id.asc())
            .limit(limit)
        )
    ).scalars().all()


# ---------------------------------------------------------------------------
# 长期记忆
# ---------------------------------------------------------------------------

def normalize_key(key: Any) -> str:
    """键必须稳定，否则同一个意思会散成十个键，检索时一个都命不中。

    **必须保留中文。** 原来的 `[^a-zA-Z0-9_]` 会把「立体几何」整段删掉，
    归一化后只剩 `topic`——所有中文知识点会塌缩成同一个键，
    等于把不同知识点合并成一条记忆，而且互相覆盖。`\\w` 在 Python 3
    里默认按 Unicode 匹配，正好能留下中文。
    """
    raw = re.sub(r"[^\w]+", "_", str(key or "").strip().lower(), flags=re.UNICODE)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return (raw or "note")[:64]


async def upsert_memory(
    db: AsyncSession,
    user_id: int,
    kind: str,
    key: str,
    value: str,
    *,
    source: str,
    confidence: float = 0.7,
    source_ref: str | None = None,
    expires_at: datetime | None = None,
) -> UserMemory | None:
    """写入一条记忆。同键同值只刷新时间，值变了走版本链。

    返回「当前有效」的那一行；空值直接忽略（模型偶尔会抽出一条空的）。
    """
    value = (value or "").strip()[:500]
    if not value:
        return None
    kind = kind if kind in MEMORY_KINDS else "fact"
    key = normalize_key(key)
    now = _now()

    current = (
        await db.execute(
            select(UserMemory).where(
                UserMemory.user_id == user_id,
                UserMemory.kind == kind,
                UserMemory.mem_key == key,
                UserMemory.superseded_by.is_(None),
            )
        )
    ).scalars().first()

    if current and (current.value or "").strip() == value:
        current.last_seen_at = now
        current.confidence = max(float(current.confidence or 0), float(confidence))
        return current

    row = UserMemory(
        user_id=user_id,
        kind=kind,
        mem_key=key,
        value=value,
        confidence=float(confidence),
        source=source,
        source_ref=source_ref,
        last_seen_at=now,
        expires_at=expires_at,
        created_at=now,
    )
    try:
        # 顺序不能反：先让旧行退出「当前有效」集合，再插新行。
        # 反过来会直接撞上部分唯一索引——索引在 INSERT 那一刻就会检查，
        # 而「插入后再更新旧行」的写法救不了它。
        # -1 只是事务内的占位值，插入成功后立刻改成真实 id；失败则整个 savepoint 回滚。
        async with db.begin_nested():
            if current:
                current.superseded_by = -1
                await db.flush()
            db.add(row)
            await db.flush()
            if current:
                current.superseded_by = row.id
                await db.flush()
    except IntegrityError:
        logger.info("memory upsert conflict: user=%s key=%s", user_id, key)
        return None
    return row


async def render_memories(db: AsyncSession, user_id: int) -> str:
    """取当前有效记忆，拼成 prompt 里的一段。

    不走向量检索：八条结论足够，且不会引入 embedding 依赖。
    """
    rows = (
        await db.execute(
            select(UserMemory)
            .where(
                UserMemory.user_id == user_id,
                UserMemory.superseded_by.is_(None),
                or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > _now()),
            )
            .order_by(UserMemory.confidence.desc(), UserMemory.last_seen_at.desc())
            .limit(MEMORY_LIMIT)
        )
    ).scalars().all()
    if not rows:
        return ""

    lines = [f"- [{row.kind}] {row.mem_key}：{row.value}" for row in rows]
    return (
        "关于这个用户的长期记忆（来自他过去的原话或系统事件，可作背景，"
        "不要当成他这一轮刚说的话）：\n" + "\n".join(lines)
    )


async def render_previous_summary(
    db: AsyncSession, user_id: int, page: str, exclude_session_id: str | None = None
) -> str:
    """上一条已归档会话的摘要。

    这是「跨会话」最直接的体现：新会话开场就知道上次聊到哪了。
    只取一条，避免把历史摘要堆成上下文债务。
    """
    stmt = select(AgentSession).where(
        AgentSession.user_id == user_id,
        AgentSession.page == page,
        AgentSession.status == "archived",
        AgentSession.summary.is_not(None),
    )
    if exclude_session_id:
        stmt = stmt.where(AgentSession.id != exclude_session_id)
    row = (
        await db.execute(stmt.order_by(AgentSession.last_active_at.desc()).limit(1))
    ).scalars().first()
    if not row or not (row.summary or "").strip():
        return ""
    return f"上一次在这个页面的对话摘要（供衔接，不要照搬）：\n{row.summary.strip()}"


# ---------------------------------------------------------------------------
# 记忆抽取（只在触发词命中时跑一次）
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """你是信息抽取器。从用户这句话里抽取「跨会话仍然成立」的信息。

只输出一个 JSON 对象，不要解释：
{"memories":[{"kind":"preference|fact|weakness|goal|constraint","key":"英文短键","value":"一句中文结论"}]}

规则：
1. 只抽用户明确表达、且以后仍然有用的信息。寒暄、一次性的问题、情绪，一律不抽。
2. key 用稳定的英文短语，同一含义必须用同一个 key（如目标城市用 target_city）。
3. value 要具体，保留原文的数字与名词，不要改写成立场。
4. 没有可记的就返回 {"memories":[]}。宁少勿多。
"""


def looks_like_memory_request(text: str) -> bool:
    return bool(MEMORY_TRIGGER.search(text or ""))


async def extract_memories(
    db: AsyncSession, user_id: int, text: str, source_ref: str | None = None
) -> list[UserMemory]:
    """一次小模型调用，把用户这句话抽成结构化记忆。"""
    if not _llm_ready():
        return []
    try:
        llm = get_llm(temperature=0, max_tokens=300)
        async with llm_slot():
            result = await llm.ainvoke(
                [
                    SystemMessage(content=EXTRACT_PROMPT),
                    HumanMessage(content=f"用户说：{text[:800]}"),
                ]
            )
        content = getattr(result, "content", "")
        if isinstance(content, list):
            content = "".join(str(part) for part in content)
        payload = _parse_json_object(str(content or ""))
    except Exception:  # noqa: BLE001 - 抽取失败不能影响对话本身
        logger.exception("memory extract failed: user=%s", user_id)
        return []

    items = (payload or {}).get("memories")
    if not isinstance(items, list):
        return []

    written: list[UserMemory] = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        row = await upsert_memory(
            db,
            user_id,
            str(item.get("kind") or "fact"),
            str(item.get("key") or "note"),
            str(item.get("value") or ""),
            source="user_stated",
            confidence=0.9,
            source_ref=source_ref,
        )
        if row:
            written.append(row)
    return written


def schedule_extraction(user_id: int, text: str, source_ref: str | None = None) -> None:
    """把抽取放到后台，不拖慢对话响应。

    用独立 session：请求的 session 在响应返回后就关了。
    """
    async def _run():
        try:
            async with AsyncSessionLocal() as db:
                await extract_memories(db, user_id, text, source_ref)
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("scheduled memory extract failed: user=%s", user_id)

    task = asyncio.create_task(_run())
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


# ---------------------------------------------------------------------------
# 归档
# ---------------------------------------------------------------------------

SUMMARY_PROMPT = """把这段对话压缩成可复用的记录。

只输出一个 JSON 对象，不要解释：
{"summary":"2~3 句中文摘要：用户想做什么、结论是什么、还有什么没做",
 "memories":[{"kind":"preference|fact|weakness|goal|constraint","key":"英文短键","value":"一句中文结论"}]}

规则：
1. 摘要写给「下一个接手的人」看，不要写「用户问了问题」这种废话。
2. memories 只保留跨会话仍然成立的结论，最多 3 条，没有就空数组。
3. 不确定的信息不要写进去。
"""


async def _summarize(messages: list[AgentMessage]) -> tuple[str | None, list[dict]]:
    if not _llm_ready() or not messages:
        return None, []
    transcript = []
    for row in messages[-30:]:
        who = "用户" if row.role == "user" else "助手"
        text = (row.content or "").strip()[:300]
        if text:
            transcript.append(f"{who}：{text}")
    if not transcript:
        return None, []

    try:
        llm = get_llm(temperature=0, max_tokens=500)
        async with llm_slot():
            result = await llm.ainvoke(
                [
                    SystemMessage(content=SUMMARY_PROMPT),
                    HumanMessage(content="\n".join(transcript)),
                ]
            )
        content = getattr(result, "content", "")
        if isinstance(content, list):
            content = "".join(str(part) for part in content)
        payload = _parse_json_object(str(content or ""))
    except Exception:  # noqa: BLE001
        logger.exception("session summarize failed")
        return None, []

    if not payload:
        return None, []
    summary = str(payload.get("summary") or "").strip() or None
    memories = payload.get("memories")
    return summary, memories if isinstance(memories, list) else []


async def archive_session(db: AsyncSession, session: AgentSession) -> bool:
    """归档单个会话，返回是否成功。

    摘要失败不归档，但把 `last_active_at` 推到当前时间——
    相当于「30 分钟后再试」，否则每轮循环都会重试同一个失败会话，白烧额度。
    单独拆出来是为了能对指定会话手动归档（排障、运营补数据都用得上）。
    """
    messages = await load_session_messages(db, session.id)
    summary, memories = await _summarize(messages)
    if not summary:
        session.last_active_at = _now()
        return False

    session.summary = summary
    session.status = "archived"
    for item in memories[:3]:
        if not isinstance(item, dict):
            continue
        await upsert_memory(
            db,
            session.user_id,
            str(item.get("kind") or "fact"),
            str(item.get("key") or "note"),
            str(item.get("value") or ""),
            source="summary",
            confidence=0.7,
            source_ref=session.id,
        )
    return True


async def archive_stale_sessions(db: AsyncSession, limit: int = ARCHIVE_BATCH) -> int:
    """把闲置或够长的会话归档，返回归档条数。"""
    cutoff = _now() - ARCHIVE_IDLE
    rows = (
        await db.execute(
            select(AgentSession)
            .where(
                AgentSession.status == "active",
                or_(
                    AgentSession.last_active_at < cutoff,
                    AgentSession.turn_count >= ARCHIVE_TURNS,
                ),
            )
            .order_by(AgentSession.last_active_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    archived = 0
    for session in rows:
        if await archive_session(db, session):
            archived += 1

    await db.commit()
    if archived:
        logger.info("archived %d agent session(s)", archived)
    return archived


async def archive_loop(stop_event: asyncio.Event) -> None:
    """复用 reminder_loop 的模式：asyncio 循环 + 幂等，不引 broker。"""
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as db:
                await archive_stale_sessions(db)
        except Exception:  # noqa: BLE001 - 归档失败不该让进程退出
            logger.exception("archive loop iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=ARCHIVE_INTERVAL)
        except asyncio.TimeoutError:
            pass
