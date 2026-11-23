"""知识图谱页 Agent 工具的验收（不过大模型，直接打注册表）。

覆盖 get_weak_points 的空数据、权重排序、前置知识关联、科目统计，
以及「知识点库没有数据时降级但不报错」。

跑法：
    python scripts/agent_tools_weak_points.py

脚本自建临时账号与学习数据，结束时清理业务数据（保留账号，避免撞审计外键）。
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
from edu_app.models.learning import LearningRecord, MistakeRecord
from edu_app.models.user import User

engine.echo = False

EMAIL = "weak_probe@example.com"
passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


async def cleanup(db, uid: int) -> None:
    await db.execute(delete(LearningRecord).where(LearningRecord.user_id == uid))
    await db.execute(delete(MistakeRecord).where(MistakeRecord.user_id == uid))
    await db.commit()


async def main() -> int:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalars().first()
        if user is None:
            user = User(email=EMAIL, hashed_password="x", full_name="薄弱点探针", role="student")
            db.add(user)
            await db.commit()
            await db.refresh(user)
        uid = user.id
        await cleanup(db, uid)

        print("—— 注册表 ——")
        read_tools = [spec.name for spec in tools_for("knowledge", "read")]
        write_tools = [spec.name for spec in tools_for("knowledge", "write")]
        check("知识图谱页有读工具", read_tools == ["get_weak_points"], str(read_tools))
        check("知识图谱页没有写工具", write_tools == [], str(write_tools))
        check("其他页面拿不到薄弱点工具", all(spec.name != "get_weak_points" for spec in tools_for("resume")))

        reader = REGISTRY["get_weak_points"].reader

        print("—— 空数据 ——")
        empty = await reader(db, user, None)
        check("空数据 exists=False", empty.get("exists") is False, str(empty.get("hint", ""))[:40])

        print("—— 造数据 ——")
        now = datetime.now(UTC)
        db.add_all([
            # 数学 / 导数：2 道错题 + 1 次 58 分 → 权重 2*2 + 1*1.5 = 5.5
            MistakeRecord(user_id=uid, subject="数学", topic="导数",
                          question_content="求 f(x)=x^3-3x 的单调区间。", is_resolved=0),
            MistakeRecord(user_id=uid, subject="数学", topic="导数",
                          question_content="求曲线 y=x^2 在 x=1 处的切线方程。", is_resolved=0),
            LearningRecord(user_id=uid, subject="数学", topic="导数", activity_type="测验", score=58.0,
                           timestamp=now - timedelta(days=1)),
            # 数学 / 极限：1 道错题 → 权重 2.0
            MistakeRecord(user_id=uid, subject="数学", topic="极限",
                          question_content="求 lim(x→0)(sin x)/x。", is_resolved=0),
            # 英语 / 阅读：1 道错题 + 2 次低分 → 权重 2 + 3 = 5.0
            MistakeRecord(user_id=uid, subject="英语", topic="阅读",
                          question_content="What is the author's attitude? A. neutral B. critical", is_resolved=0),
            LearningRecord(user_id=uid, subject="英语", topic="阅读", activity_type="练习", score=65.0,
                           timestamp=now - timedelta(days=2)),
            LearningRecord(user_id=uid, subject="英语", topic="阅读", activity_type="练习", score=62.0,
                           timestamp=now - timedelta(days=5)),
            # 已掌握的错题不该计入
            MistakeRecord(user_id=uid, subject="数学", topic="导数",
                          question_content="已经会了的题。", is_resolved=1),
            # 高分记录不该计入
            LearningRecord(user_id=uid, subject="数学", topic="导数", activity_type="测验", score=92.0,
                           timestamp=now - timedelta(days=3)),
        ])
        await db.commit()

        print("—— 权重与排序 ——")
        result = await reader(db, user, None)
        check("有数据 exists=True", result.get("exists") is True)
        points = result.get("weak_points", [])
        check("返回 3 个薄弱点", len(points) == 3, str([(p["subject"], p["topic"]) for p in points]))

        first = points[0] if points else {}
        check("第一是数学导数", (first.get("subject"), first.get("topic")) == ("数学", "导数"),
              f"{first.get('subject')}/{first.get('topic')}")
        check("权重为 5.5（2 错题 + 1 低分）", first.get("weight") == 5.5, str(first.get("weight")))
        check("原因写明错题数与最低分", "2 道错题" in first.get("reason", "") and "58" in first.get("reason", ""),
              first.get("reason", ""))
        check("已掌握错题与高分记录未计入", "3 道错题" not in first.get("reason", ""), first.get("reason", ""))

        second = points[1] if len(points) > 1 else {}
        check("第二是英语阅读（5.0）", (second.get("subject"), second.get("weight")) == ("英语", 5.0),
              f"{second.get('subject')}/{second.get('weight')}")
        check("英语没有知识点节点时前置为空", second.get("prerequisites") is None, str(second.get("prerequisites")))

        third = points[2] if len(points) > 2 else {}
        check("第三是数学极限（2.0）", (third.get("subject"), third.get("weight")) == ("数学", 2.0),
              f"{third.get('subject')}/{third.get('weight')}")

        print("—— 前置知识关联 ——")
        check("导数匹配到「导数及其应用」", first.get("description") is not None,
              str(first.get("description", ""))[:30])
        check("带出前置知识链", first.get("prerequisites") == ["函数概念", "函数的性质"],
              str(first.get("prerequisites")))
        check("知识点库状态可读", result.get("knowledge_library", {}).get("nodes", 0) > 0,
              str(result.get("knowledge_library")))
        # 数学有节点、英语没有：要明确指出缺哪一科，而不是笼统说「库没数据」
        lib = result.get("knowledge_library", {})
        check("指出缺失节点的是英语", lib.get("missing_subjects") == ["英语"], str(lib.get("missing_subjects")))

        print("—— 科目统计 ——")
        stats = result.get("subject_stats", {})
        check("数学统计正确", stats.get("数学") == {"points": 2, "mistakes": 3, "low_scores": 1},
              str(stats.get("数学")))
        check("英语统计正确", stats.get("英语") == {"points": 1, "mistakes": 1, "low_scores": 2},
              str(stats.get("英语")))
        check("说明权重口径", "错题" in result.get("criteria", ""), result.get("criteria", "")[:40])

        print("—— 清理 ——")
        await cleanup(db, uid)
        check("业务数据已清理", True)

    print(f"\n通过 {len(passed)} 项，失败 {len(failed)} 项")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
