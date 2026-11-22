from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime, UTC
from edu_app.core.database import Base

class Tenant(Base):
    """
    租户/机构模型 (V2.0.0 多租户 SaaS)
    """
    __tablename__ = "tenants"

    id = Column(String, primary_key=True, index=True)  # 例如 "org_pku_edu" 或 UUID
    name = Column(String, nullable=False)
    plan_type = Column(String, default="basic")  # basic, professional, enterprise
    max_students = Column(Integer, default=100)
    token_quota_monthly = Column(Float, default=1000000.0)  # 月度 Token 配额
    tokens_used_current_month = Column(Float, default=0.0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

class SubscriptionOrder(Base):
    """
    SaaS 订阅与充值订单表
    """
    __tablename__ = "subscription_orders"

    id = Column(String, primary_key=True, index=True)  # 订单号
    tenant_id = Column(String, ForeignKey("tenants.id"), index=True)
    amount = Column(Float, nullable=False)  # 订单金额 (元)
    order_type = Column(String, nullable=False)  # "plan_subscription", "token_topup"
    payment_status = Column(String, default="pending")  # pending, paid, failed, refunded
    payment_channel = Column(String, nullable=True)  # "wechat_pay", "alipay"
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
