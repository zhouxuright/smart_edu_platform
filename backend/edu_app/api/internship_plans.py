import json
import logging
import re
from datetime import datetime, timedelta, UTC
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from edu_app.api.endpoints import get_current_user, require_llm_quota
from edu_app.core.config import settings
from edu_app.core.database import get_db
from edu_app.models.plan import InternshipPlan, InternshipPlanTask, PlanEvent, PlanReminder
from edu_app.models.user import User
# 计划读写与漏斗埋点只有一份实现，Agent 工具也走这里
from edu_app.services.plan_store import (
    current_plan as _current_plan,
    log_event as _log_event,
    reminder_for as _reminder_for,
    set_task_completed,
    task_payload as _task_payload,
)
from edu_app.services.llm_gate import llm_client

router = APIRouter()
logger = logging.getLogger(__name__)


class GeneratePlanRequest(BaseModel):
    target_role: str = Field(min_length=2, max_length=60)
    stage: str = Field(min_length=1, max_length=40)
    weekly_hours: int = Field(ge=1, le=40)
    replace: bool = False


class TaskUpdate(BaseModel):
    completed: Optional[bool] = None
    title: Optional[str] = Field(default=None, min_length=2, max_length=60)
    action: Optional[str] = Field(default=None, min_length=2, max_length=300)
    deliverable: Optional[str] = Field(default=None, min_length=1, max_length=160)
    estimated_minutes: Optional[int] = Field(default=None, ge=10, le=240)


class ReminderUpdate(BaseModel):
    enabled: bool
    hour: int = Field(ge=0, le=23)


class EventCreate(BaseModel):
    event: str = Field(min_length=2, max_length=40)
    plan_id: Optional[int] = None


ALLOWED_EVENTS = {"plan_opened"}


def _plan_payload(plan: InternshipPlan, reminder: Optional[PlanReminder] = None) -> dict[str, Any]:
    tasks = sorted(plan.tasks, key=lambda item: item.day_index)
    pending = next((task for task in tasks if not task.completed), None)
    enabled = bool(reminder.enabled) if reminder else False
    hour = reminder.hour if reminder else 20
    now = datetime.now()
    return {
        "id": plan.id,
        "target_role": plan.target_role,
        "stage": plan.stage,
        "weekly_hours": plan.weekly_hours,
        "source": plan.source,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "completed_count": sum(1 for task in tasks if task.completed),
        "tasks": [_task_payload(task) for task in tasks],
        "next_task": _task_payload(pending) if pending else None,
        "reminder": {
            "enabled": enabled,
            "hour": hour,
            "due": bool(enabled and pending and now.hour >= hour),
        },
    }


def _minutes_per_task(weekly_hours: int) -> int:
    raw = round(weekly_hours * 60 / 7 / 5) * 5
    return max(20, min(120, raw))


def _template_tasks(target_role: str, weekly_hours: int, stage: str) -> list[dict[str, Any]]:
    minutes = _minutes_per_task(weekly_hours)
    role = target_role.strip()
    return [
        {
            "day_index": 1,
            "title": f"锁定「{role}」的岗位标准",
            "action": f"找 3 个真实的{role}实习岗位描述，把重复出现的要求记成一张清单，标出你已经具备和还缺的部分。",
            "deliverable": "一张岗位要求清单：3 个岗位来源、5 条共性要求、2 条能力缺口。",
            "estimated_minutes": minutes,
        },
        {
            "day_index": 2,
            "title": "盘出能写进简历的经历",
            "action": "从课程项目、社团、竞赛、兼职里挑 2 段经历，按「背景—我做了什么—结果」各写 3 行，尽量补上数字。",
            "deliverable": "2 段经历素材，每段都有具体动作和可核对的结果。",
            "estimated_minutes": minutes,
        },
        {
            "day_index": 3,
            "title": "写出第一版一页简历",
            "action": f"按第 1 天的清单排序，把最相关的经历放前面；用{role}岗位会用的词描述能力，删掉与岗位无关的内容。",
            "deliverable": "一页 PDF 简历初稿，包含联系方式、教育背景、2 段经历、技能。",
            "estimated_minutes": minutes,
        },
        {
            "day_index": 4,
            "title": "补一个能展示的小作品",
            "action": f"针对清单里最大的能力缺口，做一个 2 小时内能完成的小产出，例如一份竞品拆解、一个原型页面或一段数据分析。",
            "deliverable": "一个可展示的作品链接或文件，并能用 3 句话说明你的思路。",
            "estimated_minutes": minutes,
        },
        {
            "day_index": 5,
            "title": "投出第一批 5 个岗位",
            "action": "只投匹配度高的岗位。每份投递附一句针对该岗位的说明，并把公司、岗位、时间、渠道记进投递表。",
            "deliverable": "5 条投递记录，其中至少 2 条有定制化说明。",
            "estimated_minutes": minutes,
        },
        {
            "day_index": 6,
            "title": "准备 60 秒自我介绍",
            "action": "写一段 60 秒自我介绍，再准备 2 个用 STAR 讲清楚的行为面试故事，出声练两遍。",
            "deliverable": "60 秒自我介绍稿 + 2 个面试故事，能不看稿讲完。",
            "estimated_minutes": minutes,
        },
        {
            "day_index": 7,
            "title": "复盘并安排下一周",
            "action": "统计投递回复和面试邀约，找出简历或方向上的问题；给下一周定 5 个具体动作和截止时间。",
            "deliverable": "一页复盘：本周数据、一个问题、下周 5 个动作。",
            "estimated_minutes": minutes,
        },
    ]


def _parse_tasks(raw: str) -> list[dict[str, Any]]:
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("no json array")
    items = json.loads(text[start : end + 1])
    if not isinstance(items, list) or len(items) < 7:
        raise ValueError("expected seven tasks")
    tasks = []
    for index, item in enumerate(items[:7], start=1):
        if not isinstance(item, dict):
            raise ValueError("invalid task")
        title = str(item.get("title", "")).strip()[:60]
        action = str(item.get("action", "")).strip()[:300]
        deliverable = str(item.get("deliverable", "")).strip()[:160]
        if len(title) < 2 or len(action) < 2 or not deliverable:
            raise ValueError("incomplete task")
        minutes = item.get("estimated_minutes", 45)
        minutes = minutes if isinstance(minutes, int) else 45
        tasks.append({
            "day_index": index,
            "title": title,
            "action": action,
            "deliverable": deliverable,
            "estimated_minutes": max(10, min(240, minutes)),
        })
    return tasks


async def _generate_with_model(target_role: str, stage: str, weekly_hours: int) -> Optional[list[dict[str, Any]]]:
    base = (settings.LLM_API_BASE or "").strip()
    key = (settings.LLM_API_KEY or "").strip()
    if getattr(settings, "LLM_DISABLE", False) or not base or not key or key.lower() == "dummy":
        return None
    if base.startswith("http://localhost") or base.startswith("http://127.0.0.1"):
        return None
    model_name = getattr(settings, "LLM_MODEL", None) or ("deepseek-chat" if "deepseek.com" in base else "gpt-4o")
    prompt = (
        "你是一名大学生求职教练。请为一位学生制定 7 天找第一份实习的计划。\n"
        f"目标岗位：{target_role}\n当前阶段：{stage}\n每周可投入：{weekly_hours} 小时\n"
        "要求：每天恰好一个任务，任务必须具体、能在当天完成，并给出可检查的交付物；"
        "不要编造公司招聘信息或截止日期；不要承诺录用。\n"
        "只输出 JSON 数组，不要解释，不要 Markdown。每项格式："
        '{"title":"不超过20字","action":"具体做法，不超过120字","deliverable":"当天可检查的交付物","estimated_minutes":45}'
    )
    try:
        async with llm_client(httpx.Timeout(30.0), base) as client:
            response = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model_name,
                    "temperature": 0.4,
                    "max_tokens": 1400,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            content = (response.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return _parse_tasks(content)
    except Exception as error:
        logger.warning("Internship plan model generation failed: %s", type(error).__name__)
        return None


@router.get("/internship-plans/current")
async def get_current_plan(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    plan = await _current_plan(db, current_user.id)
    if not plan:
        return {"plan": None}
    reminder = await _reminder_for(db, plan.id)
    return {"plan": _plan_payload(plan, reminder)}


@router.post("/internship-plans/events")
async def record_event(
    request: EventCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if request.event not in ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="不支持的事件")
    local_now = datetime.now().astimezone()
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    existing = await db.execute(
        select(PlanEvent.id).where(
            PlanEvent.user_id == current_user.id,
            PlanEvent.event == request.event,
            PlanEvent.created_at >= day_start,
        )
    )
    if existing.first():
        return {"recorded": False}
    await _log_event(db, current_user.id, request.event, request.plan_id)
    await db.commit()
    return {"recorded": True}


@router.put("/internship-plans/reminder")
async def update_reminder(
    request: ReminderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plan = await _current_plan(db, current_user.id)
    if not plan:
        raise HTTPException(status_code=404, detail="还没有计划")
    reminder = await _reminder_for(db, plan.id)
    if reminder:
        reminder.enabled = request.enabled
        reminder.hour = request.hour
    else:
        reminder = PlanReminder(plan_id=plan.id, user_id=current_user.id, enabled=request.enabled, hour=request.hour)
        db.add(reminder)
    if request.enabled:
        await _log_event(db, current_user.id, "reminder_enabled", plan.id)
    await db.commit()
    await db.refresh(reminder)
    return {"reminder": {"enabled": bool(reminder.enabled), "hour": reminder.hour}}


@router.post("/internship-plans/generate")
async def generate_plan(
    request: GeneratePlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _quota: bool = Depends(require_llm_quota),
):
    existing = await _current_plan(db, current_user.id)
    if existing and not request.replace:
        raise HTTPException(status_code=409, detail="已有一份进行中的计划")

    tasks = await _generate_with_model(request.target_role.strip(), request.stage.strip(), request.weekly_hours)
    source = "model"
    if not tasks:
        tasks = _template_tasks(request.target_role, request.weekly_hours, request.stage)
        source = "template"

    if existing:
        await db.execute(delete(PlanReminder).where(PlanReminder.plan_id == existing.id))
        await db.delete(existing)
        await db.flush()

    plan = InternshipPlan(
        user_id=current_user.id,
        target_role=request.target_role.strip(),
        stage=request.stage.strip(),
        weekly_hours=request.weekly_hours,
        source=source,
    )
    plan.tasks = [InternshipPlanTask(**task) for task in tasks]
    db.add(plan)
    await db.flush()
    await _log_event(db, current_user.id, "plan_generated", plan.id)
    await db.commit()
    await db.refresh(plan, attribute_names=["tasks"])
    return {"plan": _plan_payload(plan)}


@router.patch("/internship-plans/tasks/{task_id}")
async def update_task(
    task_id: int,
    request: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(InternshipPlanTask)
        .join(InternshipPlan, InternshipPlanTask.plan_id == InternshipPlan.id)
        .where(InternshipPlanTask.id == task_id, InternshipPlan.user_id == current_user.id)
    )
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    changes = request.model_dump(exclude_unset=True)
    if "completed" in changes:
        # 完成状态走共享实现：漏斗埋点必须和 Agent 的 toggle_task 一致
        await set_task_completed(db, current_user.id, task, bool(changes["completed"]))
    for field in ("title", "action", "deliverable", "estimated_minutes"):
        if field in changes and changes[field] is not None:
            setattr(task, field, changes[field])

    await db.commit()
    await db.refresh(task)
    return {"task": _task_payload(task)}
