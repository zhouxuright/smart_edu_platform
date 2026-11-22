"""Agent 提案（写操作的待确认记录）。

为什么要把提案落库，而不是让前端拿着参数回头再发一次：
1. 前端传来的参数不可信——用户改一下 `resume_id` 就能写到别人的简历上；
2. 落库才有审计：谁在什么时候让 AI 改了什么、最后有没有执行；
3. 确认时服务端重新校验一次，模型给的参数过期（比如状态已被改）能挡住。
"""
from datetime import datetime, UTC

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from edu_app.core.database import Base


class AgentProposal(Base):
    __tablename__ = "agent_proposals"

    # uuid 字符串：前端只拿得到一个不透明 id，猜不出别的提案
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    page = Column(String(40), nullable=False)
    tool = Column(String(40), nullable=False)
    args = Column(JSONB, nullable=False, default=dict)
    # 给用户看的一句话 + 结构化的前后对比
    summary = Column(Text, nullable=True)
    preview = Column(JSONB, nullable=True)

    # pending / applied / rejected / expired
    status = Column(String(16), nullable=False, default="pending", index=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    applied_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
