"""Agent 运行时的会话与记忆表。

三张表各管一件事，别混：

1. `agent_sessions` —— 会话。短期记忆的容器，一个用户在一个页面可以有多条。
2. `agent_messages` —— 流水。只服务「当轮上下文」和审计，**不用于长期检索**。
   历史是流水，记忆是结论；把历史当记忆检索，三个月后模型会拿一句客套话当事实。
3. `user_memories` —— 结论。带来源、置信度、版本链、过期时间。

版本链是这张表最容易被做错的地方：学生改主意说「不想考公了」，
不是 UPDATE 掉旧值，而是新增一行、让旧行的 `superseded_by` 指向新行。
覆盖式更新能省一行代码，但永远回答不了「为什么三个月前推荐的是考公岗位」。
"""
from datetime import datetime, UTC

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Float,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from edu_app.core.database import Base


def _now():
    return datetime.now(UTC)


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    page = Column(String(40), nullable=False)
    # 首条用户消息截断，仅用于会话列表展示
    title = Column(Text, nullable=True)
    # active / archived
    status = Column(String(16), nullable=False, default="active")
    # 归档时生成，注入下一条会话的开场
    summary = Column(Text, nullable=True)
    turn_count = Column(Integer, nullable=False, default=0)
    token_total = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)
    last_active_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        # 会话列表 / 归档扫描：WHERE user_id = ? ORDER BY last_active_at DESC
        Index("ix_agent_sessions_user_active", "user_id", "last_active_at"),
        # 归档扫描：WHERE status = 'active' AND last_active_at < ?
        Index("ix_agent_sessions_status_active", "status", "last_active_at"),
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("agent_sessions.id"), nullable=False, index=True
    )
    # user / assistant
    role = Column(String(12), nullable=False)
    content = Column(Text, nullable=True)
    # 这条回复是由哪个工具/提案产生的，排障时能顺着查
    tool_name = Column(String(40), nullable=True)
    tool_args = Column(JSONB, nullable=True)
    proposal_id = Column(String(36), nullable=True)
    tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)


class UserMemory(Base):
    __tablename__ = "user_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # preference / fact / weakness / goal / constraint
    kind = Column(String(16), nullable=False)
    # 归一化的键，如 target_city、target_job。同 (用户, 类型, 键) 只有一条当前有效
    mem_key = Column(String(64), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=0.7)
    # user_stated / event / summary —— 没有来源的记忆无法判断该不该信
    source = Column(String(32), nullable=False)
    # 事件 id 或 message id，能追溯
    source_ref = Column(String(64), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), default=_now)
    # 状态类记忆必须设过期（「正在准备考研」一年后不该还在影响推荐）
    expires_at = Column(DateTime(timezone=True), nullable=True)
    # 版本链：新行 id。旧行保留，只标记被谁取代
    superseded_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        # 部分唯一索引：只约束「当前有效」的那一行，历史版本可以有多条。
        # 这是版本链能成立的前提——全量唯一索引会禁止保留历史。
        Index(
            "uq_user_memories_current",
            "user_id",
            "kind",
            "mem_key",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            sqlite_where=text("superseded_by IS NULL"),
        ),
        # 检索：WHERE user_id = ? AND superseded_by IS NULL ORDER BY confidence DESC
        Index("ix_user_memories_user_current", "user_id", "superseded_by"),
    )
