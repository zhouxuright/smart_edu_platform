from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from datetime import datetime, UTC

from edu_app.core.database import Base


class InternshipPlan(Base):
    __tablename__ = "internship_plans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    target_role = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    weekly_hours = Column(Integer, nullable=False)
    source = Column(String, default="template", nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    tasks = relationship(
        "InternshipPlanTask",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="InternshipPlanTask.day_index",
    )


class InternshipPlanTask(Base):
    __tablename__ = "internship_plan_tasks"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("internship_plans.id", ondelete="CASCADE"), index=True, nullable=False)
    day_index = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    action = Column(String, nullable=False)
    deliverable = Column(String, nullable=False)
    estimated_minutes = Column(Integer, default=45, nullable=False)
    completed = Column(Boolean, default=False, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    plan = relationship("InternshipPlan", back_populates="tasks")


class PlanReminder(Base):
    __tablename__ = "plan_reminders"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("internship_plans.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    hour = Column(Integer, default=20, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class PlanEvent(Base):
    __tablename__ = "plan_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    plan_id = Column(Integer, ForeignKey("internship_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    event = Column(String, nullable=False, index=True)
    task_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)

