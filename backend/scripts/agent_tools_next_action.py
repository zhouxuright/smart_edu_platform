"""仪表盘页 Agent 工具的验收（不过大模型，直接打注册表）。

覆盖 get_next_action 的：空账号、只有计划、计划到期、错题该复习、投递停滞、
薄弱知识点、四项同时存在时的排序、全部完成时的 all_clear，以及概览数字与
页面级读工具是否一致。

跑法：
    python scripts/agent_tools_next_action.py

脚本自建临时账号与数据，结束时清理业务数据（保留账号，避免撞审计外键）。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from edu_app.agents.tools import (
    REGISTRY,
    _read_mistakes,
    _read_next_action,
    tools_for,
)
from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.models.job import JobApplication
from edu_app.models.learning import LearningRecord, MistakeRecord
from edu_app.models.plan import InternshipPlan, InternshipPlanTask, PlanReminder
from edu_app.models.user import User

engine.echo = False

EMAIL = "next_action_probe@example.com"
passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


async def cleanup(db, uid: int) -> None:
    plans = (
        await db.execute(select(InternshipPlan.id).where(InternshipPlan.user_id == uid))
    ).scalars().all()
    if plans:
        await db.execute(delete(PlanReminder).where(PlanReminder.plan_id.in_(plans)))
        await db.execute(
            delete(InternshipPlanTask).where(InternshipPlanTask.plan_id.in_(plans))
        )
        await db.execute(delete(InternshipPlan).where(InternshipPlan.id.in_(plans)))
    await db.execute(delete(MistakeRecord).where(MistakeRecord.user_id == uid))
    await db.execute(delete(LearningRecord).where(LearningRecord.user_id == uid))
    await db.execute(delete(JobApplication).where(JobApplication.user_id == uid))
    await db.commit()


async def seed_plan(db, uid: int, *, completed_all: bool = False, reminder_hour: int | None = None) -> dict:
    plan = InternshipPlan(
        user_id=uid, target_role="产品实习生", stage="打基础", weekly_hours=8, source="template",
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)

    tasks = []
    for day in (1, 2, 3):
        task = InternshipPlanTask(
            plan_id=plan.id,
            day_index=day,
            title=f"第 {day} 天：拆解岗位 JD",
            action="拆解一份 JD",
            deliverable="一份 JD 拆解笔记",
            estimated_minutes=45,
            completed=completed_all,
            completed_at=datetime.now(UTC) if completed_all else None,
        )
        db.add(task)
        tasks.append(task)
    await db.commit()
    for task in tasks:
        await db.refresh(task)

    if reminder_hour is not None:
        db.add(PlanReminder(plan_id=plan.id, user_id=uid, enabled=True, hour=reminder_hour))
        await db.commit()

    return {"plan_id": plan.id, "tasks": [t.id for t in tasks]}


async def seed_mistake(db, uid: int, *, subject: str, topic: str, days_old: float,
                       resolved: bool = False) -> int:
    row = MistakeRecord(
        user_id=uid,
        subject=subject,
        topic=topic,
        question_content="求函数在区间上的单调性并说明理由。",
        analysis_result={"difficulty": 3},
        created_at=datetime.now(UTC) - timedelta(days=days_old),
        is_resolved=1 if resolved else 0,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row.id


async def seed_application(db, uid: int, *, company: str, status: str, days_old: int) -> int:
    when = datetime.now(UTC) - timedelta(days=days_old)
    row = JobApplication(
        user_id=uid,
        company=company,
        position="产品实习生",
        status=status,
        applied_at=when,
        updated_at=when,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row.id


async def seed_low_score(db, uid: int, *, subject: str, topic: str, score: int) -> None:
    db.add(LearningRecord(
        user_id=uid,
        subject=subject,
        topic=topic,
        activity_type="quiz",
        score=score,
        timestamp=datetime.now(UTC) - timedelta(days=1),
    ))
    await db.commit()


async def main() -> int:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalars().first()
        if user is None:
            user = User(email=EMAIL, hashed_password="x", full_name="下一步探针", role="student")
            db.add(user)
            await db.commit()
            await db.refresh(user)
        uid = user.id

        # ---------------------------------------------------------- 注册表
        print("—— 注册表 ——")
        read_tools = [spec.name for spec in tools_for("dashboard", "read")]
        write_tools = [spec.name for spec in tools_for("dashboard", "write")]
        check("仪表盘页有读工具", read_tools == ["get_next_action"], str(read_tools))
        check("仪表盘页没有写工具", write_tools == [], str(write_tools))
        check(
            "其他页面拿不到聚合工具",
            all(spec.name != "get_next_action" for spec in tools_for("mistake-book")),
        )
        check("注册表总数 14", len(REGISTRY) == 14, str(len(REGISTRY)))

        # ---------------------------------------------------------- 空账号
        print("\n—— 空账号 ——")
        await cleanup(db, uid)
        res = await _read_next_action(db, user, None)
        check("空账号 exists=False", res["exists"] is False)
        check("空账号 all_clear=False", res["all_clear"] is False)
        check("空账号提示去建计划", "我的计划" in res.get("hint", ""), res.get("hint", "")[:40])

        # ---------------------------------------------------------- 只有计划待办
        print("\n—— 只有计划待办 ——")
        seeded = await seed_plan(db, uid)
        res = await _read_next_action(db, user, None)
        action = res["next_action"]
        check("识别为计划任务", action["kind"] == "plan_task", action["kind"])
        check("未到提醒点优先级 50", action["priority"] == 50, str(action["priority"]))
        check("指向计划页", action["href"] == "/plan/internship", action["href"])
        check("带可用 task_id", action["target"]["task_id"] == seeded["tasks"][0], str(action["target"]))
        check("没有备选（只有一项）", res["alternatives"] == [], str(res["alternatives"]))
        check("概览显示待办 3 步", res["overview"]["plan"]["pending"] == 3, str(res["overview"]["plan"]))

        # ---------------------------------------------------------- 计划到期
        print("\n—— 计划到期（提醒时间已过）——")
        await cleanup(db, uid)
        await seed_plan(db, uid, reminder_hour=0)  # hour=0 保证「已过提醒点」
        res = await _read_next_action(db, user, None)
        check("到期优先级 90", res["next_action"]["priority"] == 90, str(res["next_action"]["priority"]))
        check("原因提到提醒已过", "提醒时间已过" in res["next_action"]["why"], res["next_action"]["why"])

        # ---------------------------------------------------------- 错题该复习
        print("\n—— 错题该复习 ——")
        await cleanup(db, uid)
        # 3 天前的保留率约 20%，1 天前约 59%——前者更该先复习
        old_id = await seed_mistake(db, uid, subject="数学", topic="导数", days_old=3)
        await seed_mistake(db, uid, subject="物理", topic="动量守恒", days_old=1)
        res = await _read_next_action(db, user, None)
        action = res["next_action"]
        check("识别为复习错题", action["kind"] == "review_mistake", action["kind"])
        check("优先级 80", action["priority"] == 80, str(action["priority"]))
        check("挑保留率最低的那道", action["target"]["mistake_id"] == old_id, str(action["target"]))
        check("原因带保留率", "20%" in action["why"], action["why"][:40])
        check("统计到 2 道待复习", action["target"]["due_count"] == 2, str(action["target"]))

        print("\n—— 刚收录的错题不该被催复习 ——")
        await cleanup(db, uid)
        await seed_mistake(db, uid, subject="英语", topic="虚拟语气", days_old=0)
        res = await _read_next_action(db, user, None)
        # 刚录的题还没到遗忘窗口，不该进「复习」队列；但它确实是未掌握的薄弱点，
        # 所以会以 priority 60 的「补薄弱点」出现——这是对的，不是重复催。
        check("新错题不进复习队列", res["next_action"]["kind"] == "close_weak_point",
              res["next_action"]["kind"])
        check("以薄弱点身份出现", res["next_action"]["priority"] == 60,
              str(res["next_action"]["priority"]))
        check("有数据时不给 all_clear", "all_clear" not in res)

        # ---------------------------------------------------------- 投递停滞
        print("\n—— 投递停滞 ——")
        await cleanup(db, uid)
        stale_id = await seed_application(db, uid, company="字节跳动", status="applied", days_old=12)
        await seed_application(db, uid, company="刚投的公司", status="applied", days_old=1)
        res = await _read_next_action(db, user, None)
        action = res["next_action"]
        check("识别为跟进投递", action["kind"] == "follow_up_application", action["kind"])
        check("优先级 70", action["priority"] == 70, str(action["priority"]))
        check("挑停得最久的那家", action["target"]["application_id"] == stale_id, str(action["target"]))
        check("给出可流转状态", bool(action["target"]["can_move_to"]), str(action["target"]))
        check("原因带停滞天数", "12 天" in action["why"], action["why"])
        check("只统计停滞 1 家", action["target"]["stalled_total"] == 1, str(action["target"]))

        print("\n—— 已结束的投递不算停滞 ——")
        await cleanup(db, uid)
        await seed_application(db, uid, company="已拒公司", status="rejected", days_old=30)
        res = await _read_next_action(db, user, None)
        check("rejected 不触发跟进", res["exists"] is False, str(res.get("next_action")))

        # ---------------------------------------------------------- 薄弱知识点
        print("\n—— 薄弱知识点 ——")
        await cleanup(db, uid)
        await seed_low_score(db, uid, subject="化学", topic="电化学", score=58)
        res = await _read_next_action(db, user, None)
        action = res["next_action"]
        check("识别为补薄弱点", action["kind"] == "close_weak_point", action["kind"])
        check("优先级 60", action["priority"] == 60, str(action["priority"]))
        check("指向知识图谱页", action["href"] == "/knowledge", action["href"])
        check("带科目与主题", action["target"]["topic"] == "电化学", str(action["target"]))

        # ---------------------------------------------------------- 四项同场排序
        print("\n—— 四项同时存在时的排序 ——")
        await cleanup(db, uid)
        await seed_plan(db, uid, reminder_hour=0)
        await seed_mistake(db, uid, subject="数学", topic="导数", days_old=3)
        await seed_application(db, uid, company="字节跳动", status="applied", days_old=12)
        await seed_low_score(db, uid, subject="化学", topic="电化学", score=58)

        res = await _read_next_action(db, user, None)
        order = [res["next_action"]["kind"]] + [item["kind"] for item in res["alternatives"]]
        check(
            "排序：计划到期 > 复习 > 停滞 > 薄弱点",
            order == ["plan_task", "review_mistake", "follow_up_application"],
            str(order),
        )
        check("备选只给 2 项", len(res["alternatives"]) == 2, str(len(res["alternatives"])))
        check("附排序规则说明", "90" in res["rule"], res["rule"])

        print("\n—— 概览与页面级工具口径一致 ——")
        page_mistakes = await _read_mistakes(db, user, None)
        check(
            "未掌握错题数一致",
            res["overview"]["mistakes"]["unresolved"] == page_mistakes["unresolved"],
            f'{res["overview"]["mistakes"]["unresolved"]} vs {page_mistakes["unresolved"]}',
        )
        check(
            "待复习数一致",
            res["overview"]["mistakes"]["due_review"] == len(page_mistakes["due_review"]),
            f'{res["overview"]["mistakes"]["due_review"]} vs {len(page_mistakes["due_review"])}',
        )

        # ---------------------------------------------------------- 全部完成
        print("\n—— 全部完成（不是没数据）——")
        await cleanup(db, uid)
        await seed_plan(db, uid, completed_all=True)
        await seed_mistake(db, uid, subject="数学", topic="导数", days_old=3, resolved=True)
        await seed_application(db, uid, company="已发 offer", status="offer", days_old=20)
        res = await _read_next_action(db, user, None)
        check("没有到期动作", res["exists"] is False)
        check("判定为完成状态", res["all_clear"] is True, str(res.get("all_clear")))
        check("提示是完成不是没数据", "完成状态" in res.get("hint", ""), res.get("hint", "")[:40])
        check("概览仍给出计划进度", res["overview"]["plan"]["progress"] == "3/3", str(res["overview"]["plan"]))

        await cleanup(db, uid)

    await engine.dispose()
    print(f"\n通过 {len(passed)}，失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
