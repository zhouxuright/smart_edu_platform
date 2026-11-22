"""计划读写的共用逻辑。

为什么要单独抽出来：Agent 的 `toggle_task` 和 HTTP 的 `PATCH /internship-plans/tasks/{id}`
必须是同一条路径。`task_completed` / `first_task_completed` / `plan_completed` 这三个事件
是试用漏斗的唯一数据源，两套实现会让统计悄悄失真——而且失真之后没人会发现。
"""
from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from edu_app.models.plan import InternshipPlan, InternshipPlanTask, PlanEvent, PlanReminder


def task_payload(task: InternshipPlanTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "day_index": task.day_index,
        "title": task.title,
        "action": task.action,
        "deliverable": task.deliverable,
        "estimated_minutes": task.estimated_minutes,
        "completed": bool(task.completed),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


async def current_plan(db: AsyncSession, user_id: int) -> Optional[InternshipPlan]:
    result = await db.execute(
        select(InternshipPlan)
        .options(selectinload(InternshipPlan.tasks))
        .where(InternshipPlan.user_id == user_id)
        .order_by(InternshipPlan.created_at.desc(), InternshipPlan.id.desc())
    )
    return result.scalars().first()


async def reminder_for(db: AsyncSession, plan_id: int) -> Optional[PlanReminder]:
    result = await db.execute(select(PlanReminder).where(PlanReminder.plan_id == plan_id))
    return result.scalars().first()


async def log_event(
    db: AsyncSession,
    user_id: int,
    event: str,
    plan_id: Optional[int] = None,
    task_id: Optional[int] = None,
) -> None:
    db.add(PlanEvent(user_id=user_id, plan_id=plan_id, event=event, task_id=task_id))


async def set_task_completed(
    db: AsyncSession, user_id: int, task: InternshipPlanTask, completed: bool
) -> dict[str, Any]:
    """改完成状态并补记漏斗事件。调用方负责最终 commit（这里只 commit 一次以保证事件落库）。"""
    marking_done = completed and not task.completed
    task.completed = completed
    task.completed_at = datetime.now(UTC) if completed else None

    if marking_done:
        siblings = await db.execute(
            select(InternshipPlanTask).where(InternshipPlanTask.plan_id == task.plan_id)
        )
        all_tasks = siblings.scalars().all()
        await log_event(db, user_id, "task_completed", task.plan_id, task.id)
        if all(item.completed for item in all_tasks):
            await log_event(db, user_id, "plan_completed", task.plan_id)
        elif all(item.id == task.id or not item.completed for item in all_tasks):
            await log_event(db, user_id, "first_task_completed", task.plan_id, task.id)

    await db.commit()
    await db.refresh(task)
    return task_payload(task)
