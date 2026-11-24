"""可恢复的 Agent 工作流状态。

两张表的分工：

- `agent_runs` —— 一次流程的当前状态。`state` 存跨步骤的中间结果，
  `current_step` 指向下一步，`version` 是乐观锁。
- `agent_steps` —— 每一步的留痕。`(run_id, step)` 唯一，
  这是「重复调用不产生重复副作用」的物理保证，不是靠代码判断。

为什么不把状态塞进内存：进程重启、部署滚动、平台杀实例都会丢。
状态落库之后，恢复只需要知道「下一步是哪个」，不需要重放前面的步骤。
"""
from datetime import datetime, UTC

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from edu_app.core.database import Base


def _now():
    return datetime.now(UTC)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # jd_match / mock_interview / ...，与 flows 注册表里的 kind 对应
    kind = Column(String(40), nullable=False)
    # running / awaiting_human / succeeded / failed / cancelled
    status = Column(String(20), nullable=False, default="running")
    current_step = Column(String(40), nullable=True)
    state = Column(JSONB, nullable=False, default=dict)
    # 乐观锁：两个标签页同时点「继续」，只有一个能把 version 推进
    version = Column(Integer, nullable=False, default=0)
    # 子流程：多 Agent 场景下串起来用，P1 暂不产生
    parent_run_id = Column(String(36), nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now)
    # 超时未完成的流程不该永久占着列表；清理任务按这个字段扫
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_agent_runs_user_created", "user_id", "created_at"),
        Index("ix_agent_runs_status_updated", "status", "updated_at"),
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String(36), ForeignKey("agent_runs.id"), nullable=False, index=True
    )
    step = Column(String(40), nullable=False)
    # running / succeeded / failed
    status = Column(String(16), nullable=False, default="running")
    input_data = Column(JSONB, nullable=True)
    output_data = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), default=_now)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "step", name="uq_agent_steps_run_step"),
    )
