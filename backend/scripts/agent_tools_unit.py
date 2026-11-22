"""新增工具的直接验收（不过大模型，直接打注册表）。

覆盖 add_project / polish_section / get_plan / toggle_task 的
validate → preview → apply 三段，以及各种拒绝路径。

跑法（后端可开着，脚本自己连库）：
    python scripts/agent_tools_unit.py

脚本会在结束时把演示简历与计划恢复到跑之前的状态，可以反复执行。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from edu_app.agents.tools import REGISTRY, ToolError
from edu_app.api.internship_plans import _template_tasks
from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.models.plan import InternshipPlan, InternshipPlanTask
from edu_app.models.resume import Resume
from edu_app.models.user import User
from edu_app.services.plan_store import current_plan

# 引擎是 echo=True 建的，SQL 会把断言结果冲散；这个脚本只看 PASS/FAIL
engine.echo = False

EMAIL = "demo@example.com"
passed, failed = [], []


def check(name: str, ok: bool, detail: str = ""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


async def expect_error(name: str, coro, keyword: str = ""):
    try:
        await coro
    except ToolError as error:
        ok = keyword in str(error) if keyword else True
        check(name, ok, str(error)[:60])
        return
    except Exception as error:  # noqa: BLE001
        check(name, False, f"抛了非 ToolError: {type(error).__name__}: {error}")
        return
    check(name, False, "本该报错却通过了")


async def main() -> int:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalars().first()
        if not user:
            print(f"找不到账号 {EMAIL}")
            return 1
        resume = (
            await db.execute(select(Resume).where(Resume.user_id == user.id).order_by(Resume.updated_at.desc()))
        ).scalars().first()
        if not resume:
            print("演示账号没有简历")
            return 1

        plan = await current_plan(db, user.id)
        original_data = json.loads(json.dumps(resume.data or {}))
        original_task_state = None
        seeded_plan_id = None
        if not plan:
            # 演示账号没有计划时，用同一套模板函数造一份，跑完再删掉
            plan = InternshipPlan(
                user_id=user.id, target_role="产品实习生", stage="大三",
                weekly_hours=8, source="template",
            )
            db.add(plan)
            await db.flush()
            for row in _template_tasks("产品实习生", 8, "大三"):
                db.add(InternshipPlanTask(plan_id=plan.id, **row))
            await db.commit()
            # 重新走一次带 selectinload 的查询，否则 plan.tasks 会在异步上下文里触发懒加载
            plan = await current_plan(db, user.id)
            seeded_plan_id = plan.id
            print("  （已临时造一份计划用于验收）")
        if plan:
            tasks = sorted(plan.tasks, key=lambda t: t.day_index)
            original_task_state = {t.id: bool(t.completed) for t in tasks}

        rid = resume.id
        add = REGISTRY["add_project"]
        polish = REGISTRY["polish_section"]
        toggle = REGISTRY["toggle_task"]
        read_plan = REGISTRY["get_plan"]

        print("—— add_project ——")
        await expect_error("名称为空被拒", add.validate(db, user, {"resume_id": rid, "name": "x", "bullets": ["a"]}), "名称")
        await expect_error("没有要点被拒", add.validate(db, user, {"resume_id": rid, "name": "数据看板", "bullets": []}), "空的")
        await expect_error("越权简历被拒", add.validate(db, user, {"resume_id": 999999, "name": "数据看板", "bullets": ["a"]}), "找不到")

        args = {
            "resume_id": rid,
            "name": "校园二手书交易小程序",
            "role": "产品负责人",
            "start": "2025.09",
            "end": "2025.12",
            "bullets": [
                "- 访谈 12 位同学，把「卖书麻烦」拆成 3 个具体场景",
                "2. 上线 4 周促成 186 笔交易，日活 90",
            ],
        }
        ctx = await add.validate(db, user, args)
        preview = add.preview(ctx)
        check("预览是 item 类型", preview["kind"] == "item", preview["kind"])
        check("预览带角色与时间", preview["meta"] == "产品负责人 · 2025.09-2025.12", preview["meta"])
        check("要点去掉了列表前缀",
              preview["bullets"][0].startswith("访谈") and preview["bullets"][1].startswith("上线"),
              json.dumps(preview["bullets"], ensure_ascii=False)[:80])

        before_count = len((resume.data or {}).get("projects") or [])
        before_version = resume.version
        result = await add.apply(db, user, args)
        await db.refresh(resume)
        after_projects = (resume.data or {}).get("projects") or []
        check("项目已追加", len(after_projects) == before_count + 1, f"{before_count} -> {len(after_projects)}")
        check("写入内容正确", after_projects[-1]["name"] == "校园二手书交易小程序")
        check("版本号递增", result["version"] > before_version, f"{before_version} -> {result['version']}")

        print("—— polish_section ——")
        await expect_error("不支持的 section 被拒",
                           polish.validate(db, user, {"resume_id": rid, "section": "experience", "text": "x"}), "只能润色")
        await expect_error("summary 太短被拒",
                           polish.validate(db, user, {"resume_id": rid, "section": "summary", "text": "很短"}), "太短")

        summary_args = {
            "resume_id": rid,
            "section": "summary",
            "text": "信息管理与信息系统专业本科生，做过 SaaS 工单模块的产品实习，擅长把模糊需求拆成可落地的方案。",
        }
        ctx = await polish.validate(db, user, summary_args)
        pv = polish.preview(ctx)
        check("summary 预览是 text 类型", pv["kind"] == "text")
        check("summary 预览带改前原文", bool(pv["before"]), pv["before"][:40])
        await polish.apply(db, user, summary_args)
        await db.refresh(resume)
        check("summary 已写入",
              (resume.data or {})["basics"]["summary"].startswith("信息管理与信息系统专业本科生"))

        skills_args = {
            "resume_id": rid,
            "section": "skills",
            "items": ["需求分析", "PRD 撰写", "Axure 原型", "SQL 取数", "数据看板"],
        }
        ctx = await polish.validate(db, user, skills_args)
        pv = polish.preview(ctx)
        check("skills 预览是 bullets 类型", pv["kind"] == "bullets", f"{len(pv['before'])} -> {len(pv['after'])} 条")
        await polish.apply(db, user, skills_args)
        await db.refresh(resume)
        check("skills 已写入", (resume.data or {})["skills"][:2] == ["需求分析", "PRD 撰写"])

        awards_args = {"resume_id": rid, "section": "awards", "items": ["校级一等奖学金（2024）", "挑战杯省赛二等奖"]}
        await polish.apply(db, user, awards_args)
        await db.refresh(resume)
        check("awards 已写入", len((resume.data or {}).get("awards") or []) == 2)

        print("—— get_plan ——")
        read = await read_plan.reader(db, user, None)
        if not plan:
            check("无计划时给提示", read.get("exists") is False and "还没有" in read.get("hint", ""), str(read)[:60])
        else:
            check("读到计划", read.get("exists") is True, f"{read.get('target_role')} {read.get('progress')}")
            check("任务带 id 与完成状态",
                  all("id" in t and "completed" in t for t in read.get("tasks", [])),
                  f"{len(read.get('tasks', []))} 条")
            check("有 next_task 字段", "next_task" in read)

        print("—— toggle_task ——")
        if not plan:
            print("  （演示账号没有计划，跳过）")
        else:
            task = sorted(plan.tasks, key=lambda t: t.day_index)[0]
            target = not bool(task.completed)
            await expect_error("task_id 缺失被拒", toggle.validate(db, user, {"completed": True}), "任务 id")
            await expect_error("completed 类型不对被拒",
                               toggle.validate(db, user, {"task_id": task.id, "completed": "yes"}), "true 或 false")
            await expect_error("越权任务被拒",
                               toggle.validate(db, user, {"task_id": 999999, "completed": True}), "找不到")

            ctx = await toggle.validate(db, user, {"task_id": task.id, "completed": target})
            pv = toggle.preview(ctx)
            check("预览是 status 类型", pv["kind"] == "status", f"{pv['from_label']} -> {pv['to_label']}")

            await toggle.apply(db, user, {"task_id": task.id, "completed": target})
            await db.refresh(task)
            check("完成状态已落库", bool(task.completed) == target, f"completed={task.completed}")

            await expect_error("重复设置同一状态被拒",
                               toggle.validate(db, user, {"task_id": task.id, "completed": target}), "已经是")

            # 还原
            await toggle.apply(db, user, {"task_id": task.id, "completed": not target})
            await db.refresh(task)
            check("已还原到初始状态", bool(task.completed) == (not target))

        # 还原简历正文（版本号不回退，历史留档本来就不该删）
        resume.data = original_data
        await db.commit()
        print("  （演示简历正文已还原）")

        if seeded_plan_id:
            await db.execute(delete(InternshipPlan).where(InternshipPlan.id == seeded_plan_id))
            await db.commit()
            print("  （临时计划已删除）")

    print()
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
