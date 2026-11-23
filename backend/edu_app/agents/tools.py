"""页面级 Agent 工具注册表。

设计要点：
- 工具按页面授权（`pages`），简历页的会话拿不到投递页的写工具，反之亦然；
- 读工具由服务端在调模型之前执行，把结果作为上下文喂进去——模型不需要「申请」读权限，
  但也不可能读到别人或别的页面的数据；
- 写工具不直接执行，先产出提案（preview），用户确认后才落库。

为什么不用 OpenAI 原生 function calling 跑多轮：
一次规划 + 一次校验足够覆盖「改一条要点」「推进一个状态」这类原子动作，
而原生工具循环要处理流式工具增量、并行调用、参数截断，出错时的表现很难向用户解释。
注册表保留了工具声明，后续要升级成原生调用，改 `agent_chat` 的调度层即可，工具本身不用动。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# 状态机与状态文案只有一份实现，Agent 必须复用，不能自己维护一套
from edu_app.api.applications import STATUS_LABEL, TRANSITIONS
from edu_app.models.job import JobApplication
from edu_app.models.learning import LearningRecord, MistakeRecord, StudentProfile
from edu_app.models.plan import InternshipPlan, InternshipPlanTask
from edu_app.models.resume import Resume
from edu_app.models.user import User
from edu_app.services.ebbinghaus import get_due_reviews_for_user
from edu_app.services.llm_text import strip_inline_markers
from edu_app.services.plan_store import (
    current_plan as load_current_plan,
    reminder_for as load_reminder,
    set_task_completed,
)
from edu_app.services.resume_store import clone, snapshot

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """工具参数或前置条件不合法，message 直接展示给用户。"""


@dataclass
class ToolSpec:
    name: str
    kind: str  # read / write
    pages: tuple[str, ...]
    description: str
    params: str
    # 读工具：返回喂给模型的上下文
    reader: Callable[..., Awaitable[dict]] | None = None
    # 写工具：校验并返回执行上下文（含 ORM 对象，只在内存里用）
    validate: Callable[..., Awaitable[dict]] | None = None
    # 写工具：把上下文渲染成给用户看的提案卡片
    preview: Callable[[dict], dict] | None = None
    # 写工具：真正执行
    apply: Callable[..., Awaitable[dict]] | None = None


REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    REGISTRY[spec.name] = spec
    return spec


def tools_for(page: str, kind: str | None = None) -> list[ToolSpec]:
    return [
        spec
        for spec in REGISTRY.values()
        if page in spec.pages and (kind is None or spec.kind == kind)
    ]


# ------------------------------------------------------------------ 公共读取

async def _load_resume(db: AsyncSession, user: User, page_id: Any) -> Resume | None:
    query = select(Resume).where(Resume.user_id == user.id)
    try:
        rid = int(page_id) if page_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        rid = None
    if rid:
        query = query.where(Resume.id == rid)
    else:
        # 没指定就用最近编辑的那份，和简历工作台默认打开的是同一份
        query = query.order_by(Resume.updated_at.desc())
    return (await db.execute(query)).scalars().first()


def _clip(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


# ------------------------------------------------------------------ 读工具

async def _read_resume(db: AsyncSession, user: User, page_id: Any) -> dict:
    resume = await _load_resume(db, user, page_id)
    if not resume:
        return {"exists": False, "hint": "这个账号还没有简历，先让用户创建或上传一份"}

    data = resume.data or {}
    basics = data.get("basics") or {}

    def rows(section: str, name_key: str) -> list[dict]:
        out = []
        for index, item in enumerate((data.get(section) or [])[:5]):
            if not isinstance(item, dict):
                continue
            bullets = item.get("bullets") if section != "education" else item.get("highlights")
            out.append({
                "index": index,
                "name": _clip(item.get(name_key), 60),
                "role": _clip(item.get("role") or item.get("major"), 60),
                "time": f"{_clip(item.get('start'), 12)}-{_clip(item.get('end'), 12)}",
                "bullets": [_clip(b, 120) for b in (bullets or [])][:5],
            })
        return out

    return {
        "exists": True,
        "resume_id": resume.id,
        "title": resume.title,
        "target_role": resume.target_role,
        "version": resume.version,
        "basics": {key: _clip(basics.get(key), 200) for key in
                   ("name", "phone", "email", "city", "github", "summary")},
        "education": rows("education", "school"),
        "experience": rows("experience", "company"),
        "projects": rows("projects", "name"),
        "skills": [_clip(s, 40) for s in (data.get("skills") or [])][:12],
        "awards": [_clip(s, 80) for s in (data.get("awards") or [])][:8],
    }


async def _read_applications(db: AsyncSession, user: User, page_id: Any) -> dict:
    rows = (
        await db.execute(
            select(JobApplication)
            .where(JobApplication.user_id == user.id)
            .order_by(JobApplication.updated_at.desc())
            .limit(10)
        )
    ).scalars().all()

    now = datetime.now(UTC)
    items, counts = [], {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
        items.append({
            "id": row.id,
            "company": _clip(row.company, 60),
            "position": _clip(row.position, 80),
            "status": row.status,
            "status_label": STATUS_LABEL.get(row.status, row.status),
            # 允许从当前状态往哪走，模型据此提建议，不用自己猜
            "can_move_to": sorted(TRANSITIONS.get(row.status, set())),
            "applied_at": row.applied_at.date().isoformat() if row.applied_at else None,
            "days_since_update": (now - row.updated_at).days if row.updated_at else None,
            "next_action_at": row.next_action_at.date().isoformat() if row.next_action_at else None,
            "notes": _clip(row.notes, 120),
        })
    return {"total": len(rows), "by_status": counts, "items": items}


# ------------------------------------------------------------------ 写工具：简历要点

_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•·]|\d+[.、)])\s*")


def _clean_bullets(raw: Any, limit: int = 6, max_len: int = 120) -> list[str]:
    if not isinstance(raw, list):
        raise ToolError("要点必须是字符串数组")
    cleaned = []
    for item in raw[:limit]:
        line = strip_inline_markers(str(item or ""))
        line = _BULLET_PREFIX.sub("", line).strip()
        if line:
            cleaned.append(line[:max_len])
    if not cleaned:
        raise ToolError("改写后的内容是空的")
    return cleaned


async def _validate_update_bullets(db: AsyncSession, user: User, args: dict) -> dict:
    resume = await _load_resume(db, user, args.get("resume_id"))
    if not resume:
        raise ToolError("找不到这份简历")

    section = str(args.get("section") or "").strip()
    if section not in ("experience", "projects"):
        raise ToolError("只能改写实习或项目经历里的要点")

    items = (resume.data or {}).get(section) or []
    index = args.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
        raise ToolError("指定的经历条目不存在")

    return {
        "resume": resume,
        "section": section,
        "index": index,
        "bullets": _clean_bullets(args.get("bullets")),
    }


def _preview_update_bullets(ctx: dict) -> dict:
    resume, section, index = ctx["resume"], ctx["section"], ctx["index"]
    item = ((resume.data or {}).get(section) or [])[index] or {}
    label = _clip(item.get("company") or item.get("name"), 40) or f"第 {index + 1} 段"
    before = item.get("bullets") or []
    return {
        "kind": "bullets",
        "title": f"改写「{label}」的要点",
        "before": [_clip(b, 120) for b in before],
        "after": ctx["bullets"],
        "note": "改写会存一个版本，随时可以在「版本」里回退",
    }


async def _apply_update_bullets(db: AsyncSession, user: User, args: dict) -> dict:
    # 确认时重新校验一次：从生成提案到用户点确认之间，简历可能已经被改过
    ctx = await _validate_update_bullets(db, user, args)
    resume, section, index = ctx["resume"], ctx["section"], ctx["index"]

    data = clone(resume.data)
    items = data.get(section) or []
    if index >= len(items):
        raise ToolError("这条经历已经被删除了，请重新生成")
    items[index]["bullets"] = ctx["bullets"]
    data[section] = items

    resume.data = data
    await snapshot(db, resume, "AI 改写要点")
    await db.commit()
    return {"resume_id": resume.id, "version": resume.version, "section": section, "index": index}


# ------------------------------------------------------------------ 写工具：新增项目

# 和 resumes._normalize 的 items[:10] 保持一致，否则第 11 段会被静默丢弃
MAX_PROJECTS = 10


def _period(start: str, end: str) -> str:
    if not start and not end:
        return ""
    return f"{start or '?'}-{end or '至今'}"


async def _validate_add_project(db: AsyncSession, user: User, args: dict) -> dict:
    resume = await _load_resume(db, user, args.get("resume_id"))
    if not resume:
        raise ToolError("找不到这份简历")

    projects = (resume.data or {}).get("projects") or []
    if len(projects) >= MAX_PROJECTS:
        raise ToolError(f"项目已经有 {MAX_PROJECTS} 段了，先删掉一段再加")

    name = _clip(args.get("name"), 60)
    if len(name) < 2:
        raise ToolError("项目名称太短，写清楚做了什么")

    return {
        "resume": resume,
        "item": {
            "name": name,
            "role": _clip(args.get("role"), 60),
            "start": _clip(args.get("start"), 12),
            "end": _clip(args.get("end"), 12),
            "bullets": _clean_bullets(args.get("bullets")),
        },
    }


def _preview_add_project(ctx: dict) -> dict:
    item = ctx["item"]
    meta = " · ".join(part for part in (item["role"], _period(item["start"], item["end"])) if part)
    return {
        "kind": "item",
        "title": f"新增项目「{item['name']}」",
        "meta": meta,
        "bullets": item["bullets"],
        "note": "新增会存一个版本，随时可以在「版本」里回退",
    }


async def _apply_add_project(db: AsyncSession, user: User, args: dict) -> dict:
    ctx = await _validate_add_project(db, user, args)
    resume = ctx["resume"]

    data = clone(resume.data)
    projects = data.get("projects") or []
    if len(projects) >= MAX_PROJECTS:
        raise ToolError("项目已经满了，请先删掉一段")
    projects.append(ctx["item"])
    data["projects"] = projects

    resume.data = data
    await snapshot(db, resume, "AI 新增项目")
    await db.commit()
    return {
        "resume_id": resume.id,
        "version": resume.version,
        "name": ctx["item"]["name"],
        "project_count": len(projects),
    }


# ------------------------------------------------------------------ 写工具：整块润色

# 只开放「模型能整段重写」的字段：要点走 update_bullets，
# 公司/学校/时间这些事实性字段不交给模型改，避免它顺手编一个任职时间。
POLISH_SECTIONS = {
    "summary": "一句话自我介绍",
    "skills": "技能",
    "awards": "荣誉奖项",
}


async def _validate_polish_section(db: AsyncSession, user: User, args: dict) -> dict:
    resume = await _load_resume(db, user, args.get("resume_id"))
    if not resume:
        raise ToolError("找不到这份简历")

    section = str(args.get("section") or "").strip()
    if section not in POLISH_SECTIONS:
        raise ToolError("只能润色「一句话自我介绍」「技能」「荣誉奖项」")

    data = resume.data or {}
    if section == "summary":
        before = _clip((data.get("basics") or {}).get("summary"), 600)
        after = strip_inline_markers(str(args.get("text") or "")).strip()[:600]
        if len(after) < 10:
            raise ToolError("改写后太短了，至少写一句完整的话")
        return {"resume": resume, "section": section, "before": before, "after": after}

    if section == "skills":
        before = [_clip(s, 40) for s in (data.get("skills") or [])]
        after = _clean_bullets(args.get("items"), limit=20, max_len=40)
        return {"resume": resume, "section": section, "before": before, "after": after}

    before = [_clip(s, 120) for s in (data.get("awards") or [])]
    after = _clean_bullets(args.get("items"), limit=12)
    return {"resume": resume, "section": section, "before": before, "after": after}


def _preview_polish_section(ctx: dict) -> dict:
    label = POLISH_SECTIONS[ctx["section"]]
    if ctx["section"] == "summary":
        return {
            "kind": "text",
            "title": f"润色「{label}」",
            "before": ctx["before"],
            "after": ctx["after"],
            "note": "润色会存一个版本，随时可以在「版本」里回退",
        }
    return {
        "kind": "bullets",
        "title": f"润色「{label}」",
        "before": ctx["before"],
        "after": ctx["after"],
        "note": "润色会存一个版本，随时可以在「版本」里回退",
    }


async def _apply_polish_section(db: AsyncSession, user: User, args: dict) -> dict:
    ctx = await _validate_polish_section(db, user, args)
    resume = ctx["resume"]

    data = clone(resume.data)
    if ctx["section"] == "summary":
        basics = data.get("basics") or {}
        basics["summary"] = ctx["after"]
        data["basics"] = basics
    else:
        data[ctx["section"]] = ctx["after"]

    resume.data = data
    await snapshot(db, resume, f"AI 润色{POLISH_SECTIONS[ctx['section']]}")
    await db.commit()
    return {"resume_id": resume.id, "version": resume.version, "section": ctx["section"]}


# ------------------------------------------------------------------ 写工具：投递状态

async def _validate_advance_application(db: AsyncSession, user: User, args: dict) -> dict:
    try:
        application_id = int(args.get("application_id"))
    except (TypeError, ValueError):
        raise ToolError("缺少投递记录 id")

    row = (
        await db.execute(
            select(JobApplication).where(
                JobApplication.id == application_id,
                JobApplication.user_id == user.id,
            )
        )
    ).scalars().first()
    if not row:
        raise ToolError("找不到这条投递记录")

    target = str(args.get("status") or "").strip()
    if target not in STATUS_LABEL:
        raise ToolError("目标状态不合法")
    if target not in TRANSITIONS.get(row.status, set()):
        raise ToolError(
            f"「{STATUS_LABEL.get(row.status, row.status)}」不能直接改成「{STATUS_LABEL[target]}」"
        )
    return {"app": row, "target": target}


def _preview_advance_application(ctx: dict) -> dict:
    row, target = ctx["app"], ctx["target"]
    return {
        "kind": "status",
        "title": f"{row.company} · {row.position}",
        "from": row.status,
        "from_label": STATUS_LABEL.get(row.status, row.status),
        "to": target,
        "to_label": STATUS_LABEL.get(target, target),
    }


async def _apply_advance_application(db: AsyncSession, user: User, args: dict) -> dict:
    ctx = await _validate_advance_application(db, user, args)
    row, target = ctx["app"], ctx["target"]
    previous = row.status
    row.status = target
    if target == "applied" and not row.applied_at:
        row.applied_at = datetime.now(UTC)
    await db.commit()
    return {
        "application_id": row.id,
        "from": previous,
        "status": row.status,
        "status_label": STATUS_LABEL.get(row.status, row.status),
    }


# ------------------------------------------------------------------ 读工具：计划

async def _read_plan(db: AsyncSession, user: User, page_id: Any) -> dict:
    plan = await load_current_plan(db, user.id)
    if not plan:
        return {"exists": False, "hint": "这个账号还没有实习计划，先让用户在「我的计划」里生成一份"}

    tasks = sorted(plan.tasks, key=lambda item: item.day_index)
    pending = next((item for item in tasks if not item.completed), None)
    reminder = await load_reminder(db, plan.id)
    enabled = bool(reminder.enabled) if reminder else False
    hour = reminder.hour if reminder else 20
    done = sum(1 for item in tasks if item.completed)

    return {
        "exists": True,
        "plan_id": plan.id,
        "target_role": _clip(plan.target_role, 60),
        "stage": _clip(plan.stage, 40),
        "weekly_hours": plan.weekly_hours,
        "progress": f"{done}/{len(tasks)}",
        "next_task": (
            {"id": pending.id, "day_index": pending.day_index, "title": _clip(pending.title, 60)}
            if pending else None
        ),
        "reminder": {
            "enabled": enabled,
            "hour": hour,
            # 和前端横幅同一套判断：过了提醒点且还有未完成的任务才算到期
            "due": bool(enabled and pending and datetime.now().hour >= hour),
        },
        "tasks": [
            {
                "id": item.id,
                "day_index": item.day_index,
                "title": _clip(item.title, 60),
                "deliverable": _clip(item.deliverable, 80),
                "estimated_minutes": item.estimated_minutes,
                "completed": bool(item.completed),
            }
            for item in tasks
        ],
    }


# ------------------------------------------------------------------ 写工具：勾选任务

async def _validate_toggle_task(db: AsyncSession, user: User, args: dict) -> dict:
    try:
        task_id = int(args.get("task_id"))
    except (TypeError, ValueError):
        raise ToolError("缺少任务 id")

    completed = args.get("completed")
    if not isinstance(completed, bool):
        raise ToolError("completed 只能是 true 或 false")

    row = (
        await db.execute(
            select(InternshipPlanTask)
            .join(InternshipPlan, InternshipPlanTask.plan_id == InternshipPlan.id)
            .where(
                InternshipPlanTask.id == task_id,
                InternshipPlan.user_id == user.id,
            )
        )
    ).scalars().first()
    if not row:
        raise ToolError("找不到这条任务")

    if bool(row.completed) == completed:
        raise ToolError(f"这条任务已经是「{'已完成' if completed else '未完成'}」了")

    return {"task": row, "completed": completed}


def _preview_toggle_task(ctx: dict) -> dict:
    task, target = ctx["task"], ctx["completed"]
    return {
        "kind": "status",
        "title": f"第 {task.day_index} 天 · {_clip(task.title, 40)}",
        "from": "done" if task.completed else "pending",
        "from_label": "已完成" if task.completed else "未完成",
        "to": "done" if target else "pending",
        "to_label": "已完成" if target else "未完成",
        "note": "勾选会记进计划进度，和手动勾选完全一样",
    }


async def _apply_toggle_task(db: AsyncSession, user: User, args: dict) -> dict:
    ctx = await _validate_toggle_task(db, user, args)
    # 走共享实现：漏斗埋点（task_completed / first_task_completed / plan_completed）不能漏
    payload = await set_task_completed(db, user.id, ctx["task"], ctx["completed"])
    return {
        "task_id": payload["id"],
        "day_index": payload["day_index"],
        "completed": payload["completed"],
    }


# ------------------------------------------------------------------ 读工具：错题本

# 和前端 MistakeBook 的学科下拉保持一致。不在这里收口的话，「数序」「數學」
# 这种写法会各算一科，统计和筛选全乱。
SUBJECTS = ("数学", "物理", "化学", "英语", "语文", "历史", "地理", "生物", "政治")

MAX_MISTAKE_CHARS = 2000


async def _read_mistakes(db: AsyncSession, user: User, page_id: Any) -> dict:
    rows = (
        await db.execute(
            select(MistakeRecord)
            .where(MistakeRecord.user_id == user.id)
            .order_by(MistakeRecord.created_at.desc())
            .limit(30)
        )
    ).scalars().all()

    if not rows:
        return {"exists": False, "hint": "错题本还是空的，用户可以直接把题干粘进来让你收录"}

    unresolved = [row for row in rows if not row.is_resolved]
    by_subject: dict[str, int] = {}
    for row in rows:
        key = row.subject or "未分类"
        by_subject[key] = by_subject.get(key, 0) + 1

    # 复习优先级交给已有的艾宾浩斯实现：那边按 analysis_result 里的难度估稳定性，
    # 在这里重算一遍只会出现两套标准
    due = await get_due_reviews_for_user(db, user.id, max_items=5)

    return {
        "exists": True,
        "total": len(rows),
        "unresolved": len(unresolved),
        "resolved": len(rows) - len(unresolved),
        "by_subject": by_subject,
        "due_review": [
            {
                "mistake_id": item["mistake_id"],
                "subject": item["subject"],
                "topic": item["topic"],
                "retention_percent": int(item["retention_rate"] * 100),
                "reason": item["recommendation_reason"],
            }
            for item in due
        ],
        "recent": [
            {
                "id": row.id,
                "subject": row.subject,
                "topic": row.topic,
                "content": _clip(row.question_content, 80),
                "resolved": bool(row.is_resolved),
                "created_at": row.created_at.isoformat()[:10] if row.created_at else None,
            }
            for row in rows[:8]
        ],
    }


# ------------------------------------------------------------------ 写工具：收录错题

async def _validate_add_mistake(db: AsyncSession, user: User, args: dict) -> dict:
    content = str(args.get("question_content") or "").strip()
    if len(content) < 5:
        raise ToolError("题干太短了，至少要有 5 个字")
    if len(content) > MAX_MISTAKE_CHARS:
        raise ToolError(f"题干有 {len(content)} 字，超过 {MAX_MISTAKE_CHARS} 字，请拆成几道再收录")

    subject = str(args.get("subject") or "").strip()
    if subject not in SUBJECTS:
        raise ToolError(f"科目只能是：{'、'.join(SUBJECTS)}")

    topic = str(args.get("topic") or "").strip()[:40] or None

    # 同一道题重复收录会让复习清单里出现两条一样的
    duplicate = await db.scalar(
        select(MistakeRecord.id)
        .where(
            MistakeRecord.user_id == user.id,
            MistakeRecord.subject == subject,
            MistakeRecord.question_content == content,
        )
        .limit(1)
    )
    if duplicate:
        raise ToolError(f"这道题已经在错题本里了（#{duplicate}），不用重复收录")

    return {"subject": subject, "topic": topic, "content": content}


def _preview_add_mistake(ctx: dict) -> dict:
    return {
        "kind": "item",
        "title": f"收录错题「{ctx['topic'] or ctx['subject']}」",
        "meta": ctx["subject"],
        "bullets": [_clip(ctx["content"], 120)],
        "note": "收录后可在错题详情里让 AI 分析错因，分析出的难度会决定它的复习优先级",
    }


async def _apply_add_mistake(db: AsyncSession, user: User, args: dict) -> dict:
    ctx = await _validate_add_mistake(db, user, args)
    row = MistakeRecord(
        user_id=user.id,
        subject=ctx["subject"],
        topic=ctx["topic"],
        question_content=ctx["content"],
        is_resolved=0,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"mistake_id": row.id, "subject": row.subject, "topic": row.topic}


# ------------------------------------------------------------------ 写工具：标记掌握

async def _validate_resolve_mistake(db: AsyncSession, user: User, args: dict) -> dict:
    try:
        mistake_id = int(args.get("mistake_id"))
    except (TypeError, ValueError):
        raise ToolError("缺少错题 id")

    resolved = args.get("resolved")
    if not isinstance(resolved, bool):
        raise ToolError("resolved 只能是 true 或 false")

    row = await db.scalar(
        select(MistakeRecord).where(
            MistakeRecord.id == mistake_id,
            MistakeRecord.user_id == user.id,
        )
    )
    if not row:
        raise ToolError("找不到这道错题")

    if bool(row.is_resolved) == resolved:
        raise ToolError(f"这道题已经是「{'已掌握' if resolved else '待巩固'}」了")

    return {"mistake": row, "resolved": resolved}


def _preview_resolve_mistake(ctx: dict) -> dict:
    row, target = ctx["mistake"], ctx["resolved"]
    return {
        "kind": "status",
        "title": f"{row.subject} · {_clip(row.topic or row.question_content, 40)}",
        "from": "resolved" if row.is_resolved else "unresolved",
        "from_label": "已掌握" if row.is_resolved else "待巩固",
        "to": "resolved" if target else "unresolved",
        "to_label": "已掌握" if target else "待巩固",
        "note": "标记已掌握后会从复习清单里移除，随时可以改回来",
    }


async def _apply_resolve_mistake(db: AsyncSession, user: User, args: dict) -> dict:
    ctx = await _validate_resolve_mistake(db, user, args)
    row = ctx["mistake"]
    row.is_resolved = 1 if ctx["resolved"] else 0
    await db.commit()
    return {"mistake_id": row.id, "resolved": bool(row.is_resolved)}


# ------------------------------------------------------------------ 读工具：学情概览

# 投递停在这个状态超过这么多天，就认为需要跟进
STALLED_DAYS = 7
STALLED_STATUSES = ("applied", "screening", "interview")


async def _read_student_summary(db: AsyncSession, user: User, page_id: Any) -> dict:
    """家长页用的学情聚合。

    权限沿用 `ensure_student_scope` 的规则：除管理员外只能看本人。
    家长与学生的绑定关系尚未建模，所以这里刻意不开放 student_id 参数——
    多一个参数就多一个越权入口。等绑定表建好再一起放开。

    这也是家长页唯一能做的事：读。不提供任何写工具——家长不应该能改孩子的数据。
    """
    now = datetime.now(UTC)
    since = now - timedelta(days=30)

    profile = await db.scalar(select(StudentProfile).where(StudentProfile.user_id == user.id))

    records = (
        await db.execute(
            select(LearningRecord)
            .where(LearningRecord.user_id == user.id)
            .order_by(LearningRecord.timestamp.desc())
            .limit(50)
        )
    ).scalars().all()
    recent = [row for row in records if row.timestamp and row.timestamp >= since]
    scores = [row.score for row in records if row.score is not None]

    mistakes_total = await db.scalar(
        select(func.count()).select_from(MistakeRecord).where(MistakeRecord.user_id == user.id)
    ) or 0
    mistakes_open = await db.scalar(
        select(func.count()).select_from(MistakeRecord).where(
            MistakeRecord.user_id == user.id,
            MistakeRecord.is_resolved == 0,
        )
    ) or 0
    # 待复习数量直接问艾宾浩斯，和错题本页看到的数字必须一致
    due = await get_due_reviews_for_user(db, user.id, max_items=50)

    plan = await load_current_plan(db, user.id)
    plan_info = None
    if plan:
        tasks = plan.tasks
        done = sum(1 for task in tasks if task.completed)
        pending = next((task for task in tasks if not task.completed), None)
        plan_info = {
            "target_role": _clip(plan.target_role, 40),
            "progress": f"{done}/{len(tasks)}",
            "pending_task": _clip(pending.title, 40) if pending else None,
        }

    apps = (
        await db.execute(select(JobApplication).where(JobApplication.user_id == user.id))
    ).scalars().all()
    stalled = [
        app
        for app in apps
        if app.status in STALLED_STATUSES and app.updated_at and (now - app.updated_at).days >= STALLED_DAYS
    ]

    summary = {
        "student": {
            "name": _clip(user.full_name, 30) or "这位同学",
            "role": getattr(user, "role", "student"),
            "grade": _clip(profile.grade, 20) if profile else None,
            "learning_style": _clip(profile.learning_style, 20) if profile else None,
        },
        "learning": {
            "records_30d": len(recent),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "recent": [
                {
                    "subject": row.subject,
                    "topic": _clip(row.topic, 30),
                    "activity": row.activity_type,
                    "score": row.score,
                    "date": row.timestamp.isoformat()[:10] if row.timestamp else None,
                }
                for row in records[:5]
            ],
        },
        "mistakes": {
            "total": int(mistakes_total),
            "unresolved": int(mistakes_open),
            "due_review": len(due),
        },
        "plan": plan_info,
        "applications": {
            "total": len(apps),
            "stalled": len(stalled),
            "stalled_companies": [_clip(app.company, 20) for app in stalled[:3]],
        },
    }

    empty = not recent and not mistakes_total and not plan and not apps
    summary["exists"] = not empty
    if empty:
        if getattr(user, "role", "student") == "parent":
            # 别让模型对着空数据编故事，把真实原因交给它转述
            summary["hint"] = (
                "家长与学生的绑定关系还没建立，家长账号只能看到自己名下的数据，"
                "所以读不到孩子的学情。可以建议用户改用学生账号登录。"
            )
        else:
            summary["hint"] = "这个账号还没有学习记录、错题、实习计划或投递数据。"

    return summary


# ------------------------------------------------------------------ 注册

register(ToolSpec(
    name="get_resume",
    kind="read",
    pages=("resume",),
    description="读取当前简历全文：基本信息、教育、实习、项目、技能，每段经历带 index 与要点",
    params="无参数",
    reader=_read_resume,
))

register(ToolSpec(
    name="update_bullets",
    kind="write",
    pages=("resume",),
    description="改写某段实习或项目经历下的要点列表",
    params='{"section":"experience|projects","index":0,"bullets":["..."]}',
    validate=_validate_update_bullets,
    preview=_preview_update_bullets,
    apply=_apply_update_bullets,
))

register(ToolSpec(
    name="add_project",
    kind="write",
    pages=("resume",),
    description="给简历新增一段项目经历",
    params='{"name":"项目名","role":"担任角色","start":"2026.03","end":"2026.06","bullets":["..."]}',
    validate=_validate_add_project,
    preview=_preview_add_project,
    apply=_apply_add_project,
))

register(ToolSpec(
    name="polish_section",
    kind="write",
    pages=("resume",),
    description="整块润色简历里的一段文字：一句话自我介绍（传 text）、技能或荣誉（传 items）",
    params='{"section":"summary|skills|awards","text":"仅 summary","items":["仅 skills/awards"]}',
    validate=_validate_polish_section,
    preview=_preview_polish_section,
    apply=_apply_polish_section,
))

register(ToolSpec(
    name="list_applications",
    kind="read",
    pages=("applications",),
    description="读取最近的投递记录：公司、岗位、当前状态、可流转状态、停滞天数",
    params="无参数",
    reader=_read_applications,
))

register(ToolSpec(
    name="advance_application",
    kind="write",
    pages=("applications",),
    description="把一条投递推进到下一个状态（只能走状态机允许的路径）",
    params='{"application_id":1,"status":"screening"}',
    validate=_validate_advance_application,
    preview=_preview_advance_application,
    apply=_apply_advance_application,
))

register(ToolSpec(
    name="get_plan",
    kind="read",
    pages=("plan",),
    description="读取当前实习计划：目标岗位、阶段、进度、7 天任务（带 id 与完成状态）、提醒设置",
    params="无参数",
    reader=_read_plan,
))

register(ToolSpec(
    name="toggle_task",
    kind="write",
    pages=("plan",),
    description="把某一天的任务标记为已完成或未完成",
    params='{"task_id":3,"completed":true}',
    validate=_validate_toggle_task,
    preview=_preview_toggle_task,
    apply=_apply_toggle_task,
))

register(ToolSpec(
    name="list_mistakes",
    kind="read",
    pages=("mistake-book",),
    description="读取错题本：总数、按学科分布、待复习清单（带记忆保留率）、最近收录的错题",
    params="无参数",
    reader=_read_mistakes,
))

register(ToolSpec(
    name="add_mistake",
    kind="write",
    pages=("mistake-book",),
    description="把一道题收录进错题本，学科必须是固定九科之一",
    params='{"subject":"数学","topic":"极限","question_content":"题干原文"}',
    validate=_validate_add_mistake,
    preview=_preview_add_mistake,
    apply=_apply_add_mistake,
))

register(ToolSpec(
    name="resolve_mistake",
    kind="write",
    pages=("mistake-book",),
    description="把一道错题标记为已掌握，或改回待巩固",
    params='{"mistake_id":7,"resolved":true}',
    validate=_validate_resolve_mistake,
    preview=_preview_resolve_mistake,
    apply=_apply_resolve_mistake,
))

register(ToolSpec(
    name="get_student_summary",
    kind="read",
    pages=("parent",),
    description="读取当前账号的学情概览：近 30 天学习记录与平均分、错题与待复习数、实习计划进度、投递停滞情况",
    params="无参数",
    reader=_read_student_summary,
))
