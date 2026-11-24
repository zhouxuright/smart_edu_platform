"""家长与学生的绑定关系。

为什么需要单独两张表：在这之前 `ensure_student_scope` 只允许「本人或 admin」，
注释里写着「家长与学生的绑定关系尚未建模」。结果是家长登录后看到的是
**自己的**空数据，页面却写着「孩子的学习报告」——静默错误比报错更糟。

设计上的两个选择：

1. **用邀请码，不用邮箱直绑。** 家长只要能填出孩子的邮箱就能绑定，
   等于任何知道邮箱的人都能看这个学生的学情。邀请码由学生生成、一次性、
   有有效期，学生才是授权方。
2. **绑定关系单向存储。** 只存 (parent_id, student_id)，不存「谁批准了谁」，
   因为授权凭证就是邀请码本身（`used_by` 记录了是谁用掉的）。
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from edu_app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ParentStudentLink(Base):
    """一条绑定：某个家长可以看某个学生。"""

    __tablename__ = "parent_student_links"
    __table_args__ = (
        # 同一个家长对同一个学生只该有一条，重复绑定在 DB 层就挡掉，
        # 不要靠应用层「先查再插」——并发下必漏。
        UniqueConstraint("parent_id", "student_id", name="uq_parent_student_link"),
    )

    id = Column(Integer, primary_key=True)
    parent_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 关系称谓：parent / guardian / teacher。只用于展示，不参与鉴权
    relation = Column(String(20), nullable=False, default="parent")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ParentInvite(Base):
    """学生生成的邀请码。

    一次性：`used_by` 一旦写上就作废，不能反复用来绑多个家长——
    否则一个码泄露就等于把学情公开了。
    """

    __tablename__ = "parent_invites"

    id = Column(Integer, primary_key=True)
    code = Column(String(16), unique=True, nullable=False, index=True)
    student_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
