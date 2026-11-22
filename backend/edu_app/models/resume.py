"""简历工作台：结构化简历 + 版本快照。

为什么用 JSON 存正文而不是给每个字段开一列：
简历的段落数量天然不定（几段实习、几个项目都随人），拆成
education/experience 关联表会让「保存一次」变成十几次写入和级联更新，
而这一层不需要按字段做 SQL 查询——真正要检索的是「目标岗位」「标题」，
这两项单独开列即可。

版本用整份快照存：简历的改动经常是「整体重写第 2 段」，
存 diff 反而在恢复时要做链式回放，容易出错。快照直白、恢复只需一条 UPDATE。
"""
from datetime import datetime, UTC

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from edu_app.core.database import Base


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    title = Column(String(80), nullable=False, default="我的简历")
    target_role = Column(String(80), nullable=True)
    # 结构化正文：basics / education / experience / projects / skills / awards
    data = Column(JSONB, nullable=False, default=dict)

    # 当前版本号，等于最新一条 ResumeVersion.version
    version = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user = relationship("User")
    versions = relationship(
        "ResumeVersion",
        back_populates="resume",
        cascade="all, delete-orphan",
        order_by="ResumeVersion.version.desc()",
    )
    attachments = relationship(
        "ResumeAttachment",
        back_populates="resume",
        cascade="all, delete-orphan",
        order_by="ResumeAttachment.created_at.desc()",
    )


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id = Column(Integer, primary_key=True, index=True)
    resume_id = Column(
        Integer, ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version = Column(Integer, nullable=False)
    note = Column(String(120), nullable=True)
    data = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    resume = relationship("Resume", back_populates="versions")


class ResumeAttachment(Base):
    """上传的附件简历（PDF / DOCX / 图片以外的文本稿）。

    为什么单独开表而不是塞进 resumes.data：
    附件是二进制文件，要能原样下载、要能按文件去重、要能一个简历挂多份
    （旧版、不同岗位版本），这些都是关系型字段干的事，JSONB 里放不了。

    为什么存全文 text：
    简历通常 1-3 页、几千字，存下来「AI 解析导入」就不必再读磁盘、再解析一次；
    超过上限的正文会被截断并在 parse_error 里说明，不会假装解析完整。
    """

    __tablename__ = "resume_attachments"
    __table_args__ = (
        # 同一份简历里重复上传同一个文件（内容相同）没有意义，靠唯一约束挡住
        UniqueConstraint("resume_id", "sha256", name="uq_resume_attachment_digest"),
    )

    id = Column(Integer, primary_key=True, index=True)
    resume_id = Column(
        Integer, ForeignKey("resumes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # 冗余存一份 user_id：下载/删除时不必 join resumes 就能做归属校验
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    filename = Column(String(200), nullable=False)
    stored_name = Column(String(120), nullable=False)
    ext = Column(String(10), nullable=False, default="")
    mime = Column(String(100), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=False, index=True)

    chars = Column(Integer, nullable=True)
    pages = Column(Integer, nullable=True)
    text = Column(Text, nullable=True)
    parse_error = Column(String(200), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    resume = relationship("Resume", back_populates="attachments")
