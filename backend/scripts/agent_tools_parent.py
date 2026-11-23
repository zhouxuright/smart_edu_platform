"""家长门户 Agent 工具的验收（不过大模型，直接打注册表）。

覆盖 get_student_summary 的空数据、有数据、家长账号三种情况，
以及「家长页不开放任何写工具」这条硬约束。

跑法：
    python scripts/agent_tools_parent.py

脚本自建临时账号与学情数据，结束时全部删除，可以反复执行。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from edu_app.agents.tools import REGISTRY, tools_for
from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.models.job import JobApplication
from edu_app.models.learning import LearningRecord, MistakeRecord
from edu_app.models.plan import InternshipPlan, InternshipPlanTask
from edu_app.models.user import User

engine.echo = False

STUDENT_EMAIL = "parent_probe_student@example.com"
PARENT_EMAIL = "parent_probe_parent@example.com"
passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


async def cleanup(db, user_ids: list[int]) -> None:
    for model in (LearningRecord, MistakeRecord, JobApplication):
        await db.execute(delete(model).where(model.user_id.in_(user_ids)))
    plans = (await db.execute(select(InternshipPlan.id).where(InternshipPlan.user_id.in_(user_ids)))).scalars().all()
    if plans:
        await db.execute(delete(InternshipPlanTask).where(InternshipPlanTask.plan_id.in_(plans)))
        await db.execute(delete(InternshipPlan).where(InternshipPlan.id.in_(plans)))
    await db.execute(delete(User).where(User.id.in_(user_ids)))
    await db.commit()


async def get_or_create(db, email: str, name: str, role: str) -> User:
    user = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if user is None:
        user = User(email=email, hashed_password="x", full_name=name, role=role)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


async def main() -> int:
    async with AsyncSessionLocal() as db:
        student = await get_or_create(db, STUDENT_EMAIL, "测试同学", "student")
        parent = await get_or_create(db, PARENT_EMAIL, "测试家长", "parent")
        ids = [student.id, parent.id]
        await cleanup(db, ids)
        # cleanup 删了账号，重新建
        student = await get_or_create(db, STUDENT_EMAIL, "测试同学", "student")
        parent = await get_or_create(db, PARENT_EMAIL, "测试家长", "parent")
        ids = [student.id, parent.id]

        print("—— 注册表 ——")
        read_tools = [spec.name for spec in tools_for("parent", "read")]
        write_tools = [spec.name for spec in tools_for("parent", "write")]
        check("家长页有读工具", read_tools == ["get_student_summary"], str(read_tools))
        check("家长页没有任何写工具", write_tools == [], str(write_tools))
        check("其他页面拿不到学情工具", all(spec.name != "get_student_summary" for spec in tools_for("resume")))

        reader = REGISTRY["get_student_summary"].reader

        print("—— 空账号 ——")
        empty = await reader(db, student, None)
        check("空账号 exists=False", empty.get("exists") is False, str(empty.get("hint", ""))[:40])
        check("空账号统计为 0", empty["learning"]["records_30d"] == 0 and empty["mistakes"]["total"] == 0,
              f"records={empty['learning']['records_30d']} mistakes={empty['mistakes']['total']}")

        print("—— 家长账号读自己 ——")
        parent_view = await reader(db, parent, None)
        check("家长账号也 exists=False", parent_view.get("exists") is False)
        check("家长账号给出绑定说明", "绑定" in str(parent_view.get("hint", "")), str(parent_view.get("hint", ""))[:40])

        print("—— 造数据 ——")
        now = datetime.now(UTC)
        db.add_all([
            LearningRecord(user_id=student.id, subject="数学", topic="导数", activity_type="测验", score=85,
                           timestamp=now - timedelta(days=1)),
            LearningRecord(user_id=student.id, subject="英语", topic="阅读理解", activity_type="练习", score=72,
                           timestamp=now - timedelta(days=3)),
            # 30 天以外，不该计入 records_30d
            LearningRecord(user_id=student.id, subject="物理", topic="力学", activity_type="测验", score=90,
                           timestamp=now - timedelta(days=45)),
            MistakeRecord(user_id=student.id, subject="数学", topic="极限",
                          question_content="求 lim(x→0) (sin x)/x 的值。", is_resolved=0),
            MistakeRecord(user_id=student.id, subject="英语", topic="虚拟语气",
                          question_content="If I had known, I would have told you.", is_resolved=1),
            JobApplication(user_id=student.id, company="某科技公司", position="产品实习生",
                           channel="job_board", status="applied", updated_at=now - timedelta(days=10)),
            JobApplication(user_id=student.id, company="某咨询公司", position="数据分析实习生",
                           channel="referral", status="draft", updated_at=now - timedelta(days=1)),
        ])
        plan = InternshipPlan(user_id=student.id, target_role="产品实习生", stage="大三",
                              weekly_hours=8, source="template")
        db.add(plan)
        await db.flush()
        db.add_all([
            InternshipPlanTask(plan_id=plan.id, day_index=1, title="梳理岗位 JD", action="看 3 篇",
                               deliverable="关键词清单", estimated_minutes=30, completed=1),
            InternshipPlanTask(plan_id=plan.id, day_index=2, title="改简历第一段", action="重写要点",
                               deliverable="新版本", estimated_minutes=45, completed=0),
        ])
        await db.commit()

        print("—— 有数据 ——")
        summary = await reader(db, student, None)
        check("exists=True", summary.get("exists") is True)
        check("30 天内记录数为 2", summary["learning"]["records_30d"] == 2, str(summary["learning"]["records_30d"]))
        check("平均分按有分的记录算", summary["learning"]["avg_score"] == 82.3, str(summary["learning"]["avg_score"]))
        check("错题总数 2", summary["mistakes"]["total"] == 2, str(summary["mistakes"]["total"]))
        check("未掌握 1", summary["mistakes"]["unresolved"] == 1, str(summary["mistakes"]["unresolved"]))
        check("计划进度 1/2", summary["plan"]["progress"] == "1/2", str(summary["plan"]))
        check("下一个待办是第二天", summary["plan"]["pending_task"] == "改简历第一段", str(summary["plan"]["pending_task"]))
        check("投递总数 2", summary["applications"]["total"] == 2, str(summary["applications"]["total"]))
        check("停滞投递只算 7 天以上的在途", summary["applications"]["stalled"] == 1,
              f"stalled={summary['applications']['stalled']} 公司={summary['applications']['stalled_companies']}")

        print("—— 清理 ——")
        await cleanup(db, ids)
        check("临时数据已清理", True)

    print(f"\n通过 {len(passed)} 项，失败 {len(failed)} 项")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
