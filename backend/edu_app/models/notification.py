"""站内通知。

之前的「第二天提醒」只存了用户偏好（plan_reminders.enabled/hour），
没有任何发送通道——用户不主动打开页面就永远收不到。
这里补上落点：通知表 + 定时扫描，用户下次进来就能在铃铛里看到。
"""
from datetime import datetime, UTC

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from edu_app.core.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    type = Column(String(32), default="system", nullable=False, index=True)
    title = Column(String(160), nullable=False)
    body = Column(Text, nullable=True)
    link = Column(String(200), nullable=True)

    # 幂等键：同一条提醒（如同一计划的同一天）只写一次。
    # 定时任务可能重跑、进程可能重启，靠唯一约束兜底比靠内存标记可靠。
    dedupe_key = Column(String(120), unique=True, index=True, nullable=False)

    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)

    user = relationship("User")
