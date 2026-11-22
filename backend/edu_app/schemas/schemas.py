from pydantic import BaseModel, EmailStr, ConfigDict
from typing import List, Optional, Any, Literal

# 用户相关模式
class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    role: str = "student"
    tenant_id: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(UserBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# 智能体请求模式
class MentorRequest(BaseModel):
    message: str
    history: List[dict] # [{'role': 'user', 'content': '...'}, ...] 历史消息
    dataset_id: Optional[str] = None
    mode: Literal['study', 'career'] = 'study'

class KnowledgeRequest(BaseModel):
    subject: str
    topic: str

class AssessmentRequest(BaseModel):
    student_id: int
    dataset_id: Optional[str] = None

class CollaborativeRequest(BaseModel):
    topic: str
    history: List[dict] # [{'user': 'Alice', 'content': '...'}, ...] 讨论历史

class ParentReportRequest(BaseModel):
    student_id: int
    dataset_id: Optional[str] = None

# RAG 相关模式
class RAGIngest(BaseModel):
    title: str
    content: str
    url: Optional[str] = None

class RAGSearch(BaseModel):
    query: str
    top_k: Optional[int] = 5
    alpha: Optional[float] = 0.7
    dataset_id: Optional[str] = None

class OrchestrateRequest(BaseModel):
    dataset_id: Optional[str] = None

# 学习记录相关模式
class LearningRecordCreate(BaseModel):
    subject: str
    topic: str
    activity_type: str
    score: Optional[float] = None
    timestamp: Optional[str] = None
    details: Optional[Any] = None

# 错题本相关模式
class MistakeCreate(BaseModel):
    subject: str
    topic: Optional[str] = None
    question_content: str
    image_url: Optional[str] = None

class MistakeAnalyzeRequest(BaseModel):
    mistake_id: int

class MistakeResponse(BaseModel):
    id: int
    user_id: int
    subject: str
    topic: Optional[str] = None
    question_content: str
    image_url: Optional[str] = None
    analysis_result: Optional[Any] = None
    created_at: Any
    is_resolved: int
    model_config = ConfigDict(from_attributes=True)

class KnowledgeExtractionRequest(BaseModel):
    text: str
    subject: Optional[str] = None

class KnowledgeExtractionResponse(BaseModel):
    main_subject: str
    knowledge_points: List[dict]
    cross_disciplinary_links: List[dict]

# V2.0.0 SaaS 扩展模式
class TenantCreate(BaseModel):
    id: str
    name: str
    plan_type: Optional[str] = "basic"
    max_students: Optional[int] = 100

class TenantResponse(BaseModel):
    id: str
    name: str
    plan_type: str
    max_students: int
    tokens_used_current_month: float
    token_quota_monthly: float
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class SubscriptionOrderCreate(BaseModel):
    tenant_id: str
    amount: float
    order_type: str  # plan_subscription / token_topup
    payment_channel: Optional[str] = "wechat_pay"

# V3.0.0 智能闭环与记忆模式
class EbbinghausReviewQuery(BaseModel):
    student_id: Optional[int] = None
    max_items: Optional[int] = 5

class GraphRAGQuery(BaseModel):
    subject: str
    query_keywords: List[str]

# V4.0.0 模型路由模式
class RouterQueryRequest(BaseModel):
    query: str
    task_type: Optional[str] = None


