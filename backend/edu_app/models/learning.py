from sqlalchemy import Column, Date, Integer, String, ForeignKey, JSON, DateTime, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from edu_app.core.database import Base
from datetime import datetime, UTC

class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    grade = Column(String)
    interests = Column(String) # 以逗号分隔或使用 JSON 存储
    learning_style = Column(String) # 学习风格（如：视觉、听觉等）
    
    user = relationship("edu_app.models.user.User")

class LearningRecord(Base):
    __tablename__ = "learning_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    subject = Column(String)
    topic = Column(String)
    activity_type = Column(String) # 活动类型（测验、阅读、讨论）
    score = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    details = Column(JSON, nullable=True)

class MistakeRecord(Base):
    __tablename__ = "mistake_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    subject = Column(String)
    topic = Column(String, nullable=True)
    question_content = Column(String)  # The text of the question
    image_url = Column(String, nullable=True) # Optional image URL
    analysis_result = Column(JSON) # Analysis from AI (cause, solution, knowledge points)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    is_resolved = Column(Integer, default=0) # 0: unresolved, 1: resolved

class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String, index=True)
    name = Column(String, index=True)
    description = Column(String)
    prerequisites = Column(JSON) # 前置节点（ID 或名称）列表
    related_nodes = Column(JSON)

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    content = Column(String)
    source_url = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    owner_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("edu_app.models.user.User")
    tenant_id = Column(String, index=True)

class DocumentEmbedding(Base):
    __tablename__ = "document_embeddings"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"))
    vector = Column(JSON)  # 存储向量（list[float]）
    document = relationship("Document")

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    tenant_id = Column(String, index=True, nullable=True)
    method = Column(String)
    path = Column(String, index=True)
    status_code = Column(Integer)
    duration_ms = Column(Float)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class DocumentChunk(Base):
    """文档分块：检索的最小单位。

    一篇文档切成若干块，每块单独存向量。检索命中到块级，
    回答时才能只带相关段落，而不是整篇截断。
    """
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    seq = Column(Integer)          # 块序号，从 0 开始
    content = Column(String)       # 块正文
    char_start = Column(Integer)   # 在原文中的起止位置，便于回溯
    char_end = Column(Integer)
    vector = Column(JSON)          # 该块的向量
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    document = relationship("Document")


class UiEvent(Base):
    """前端行为埋点：页面浏览与关键动作。

    与 PlanEvent 分开存放是有意的：PlanEvent 专门记试用漏斗（同一事件每天只记一次），
    这里记通用 UI 行为、不做去重，用来回答「用户实际走了哪些页面、卡在哪一步」。
    两张表混在一起会让漏斗统计被页面浏览污染。
    """
    __tablename__ = "ui_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    event = Column(String, index=True)          # 如 page_view / plan_generate_click
    path = Column(String, nullable=True)        # 触发时的路由
    meta = Column(JSON, nullable=True)          # 附加信息，保持轻量
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class LlmUsage(Base):
    """按用户按天统计大模型调用次数与 token 消耗。

    没有这张表之前，所有调模型的接口都是敞开的——一个脚本就能把额度刷光。
    用 (user_id, day) 唯一约束 + 原子自增，避免「先查后写」的竞态。

    两个口径分别服务于不同目的，不要混用：
    - `count` 是**次数**，用于每日额度限制（超限 429），由 quota.py 维护；
    - `prompt_tokens` / `completion_tokens` 是**成本**，用于核算花了多少钱，
      由 llm_usage.py 维护。

    只记次数不记 token 时，无法回答「这个用户花了我多少钱」——
    一次长简历润色和一句短问答都算 1 次，成本可能差十倍。
    """
    __tablename__ = "llm_usage"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_llm_usage_user_day"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    day = Column(Date, nullable=False, index=True)
    count = Column(Integer, default=0, nullable=False)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
