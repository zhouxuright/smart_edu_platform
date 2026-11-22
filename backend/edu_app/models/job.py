"""投递记录（ATS-lite）。

求职者最痛的不是「不知道怎么写简历」，而是「投了三十家，忘了每家到哪一步」。
这张表就是解决这件事：一条记录 = 一次投递，状态机描述它现在走到哪。
"""
from datetime import datetime, UTC

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from edu_app.core.database import Base


class JobApplication(Base):
    __tablename__ = "job_applications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    company = Column(String(80), nullable=False)
    position = Column(String(120), nullable=False)
    # 渠道用来回答「哪个渠道真的有效」，是后续推荐策略的数据基础
    channel = Column(String(24), default="other", nullable=False)  # referral/campus/job_board/company_site/other
    status = Column(String(16), default="draft", nullable=False, index=True)

    location = Column(String(60), nullable=True)
    salary_range = Column(String(40), nullable=True)
    jd_url = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)

    applied_at = Column(DateTime(timezone=True), nullable=True)
    # 下次该跟进的时间。列表按它排序，避免「投完就忘」
    next_action_at = Column(DateTime(timezone=True), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user = relationship("User")
