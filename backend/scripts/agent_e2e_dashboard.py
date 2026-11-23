"""仪表盘聚合工具的端到端：注册新账号 -> 灌四类数据 -> 问「最该做什么」。

跑法（后端需已启动在 8000）：
    python scripts/agent_e2e_dashboard.py
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.models.job import JobApplication
from edu_app.models.learning import LearningRecord, MistakeRecord
from edu_app.models.plan import InternshipPlan, InternshipPlanTask, PlanReminder
from edu_app.models.user import User

BASE = "http://127.0.0.1:8000/api"
engine.echo = False


def call(method, path, token=None, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return error.code, {"raw": raw}


async def seed(uid: int) -> None:
    async with AsyncSessionLocal() as db:
        plan = InternshipPlan(
            user_id=uid, target_role="产品实习生", stage="打基础", weekly_hours=8, source="template",
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        for day in (1, 2, 3):
            db.add(InternshipPlanTask(
                plan_id=plan.id, day_index=day,
                title=f"第 {day} 天：拆解一份岗位 JD",
                action="拆解 JD", deliverable="JD 拆解笔记",
                estimated_minutes=45, completed=False,
            ))
        db.add(PlanReminder(plan_id=plan.id, user_id=uid, enabled=True, hour=0))
        db.add(MistakeRecord(
            user_id=uid, subject="数学", topic="导数",
            question_content="求 f(x)=x^3-3x 在 [-2,2] 上的单调区间与极值。",
            analysis_result={"difficulty": 3},
            created_at=datetime.now(UTC) - timedelta(days=3), is_resolved=0,
        ))
        when = datetime.now(UTC) - timedelta(days=12)
        db.add(JobApplication(
            user_id=uid, company="字节跳动", position="产品实习生",
            status="applied", applied_at=when, updated_at=when,
        ))
        db.add(LearningRecord(
            user_id=uid, subject="化学", topic="电化学", activity_type="quiz", score=58,
            timestamp=datetime.now(UTC) - timedelta(days=1),
        ))
        await db.commit()


async def cleanup(uid: int) -> None:
    async with AsyncSessionLocal() as db:
        plans = (await db.execute(
            select(InternshipPlan.id).where(InternshipPlan.user_id == uid)
        )).scalars().all()
        if plans:
            await db.execute(delete(PlanReminder).where(PlanReminder.plan_id.in_(plans)))
            await db.execute(delete(InternshipPlanTask).where(InternshipPlanTask.plan_id.in_(plans)))
            await db.execute(delete(InternshipPlan).where(InternshipPlan.id.in_(plans)))
        await db.execute(delete(MistakeRecord).where(MistakeRecord.user_id == uid))
        await db.execute(delete(LearningRecord).where(LearningRecord.user_id == uid))
        await db.execute(delete(JobApplication).where(JobApplication.user_id == uid))
        await db.commit()


async def main() -> int:
    email = f"dash_probe_{secrets.token_hex(4)}@example.com"
    status, user = call("POST", "/register", None, {
        "email": email, "password": "Probe-2026-Test", "full_name": "聚合探针",
    })
    if status not in (200, 201):
        raise SystemExit(f"注册失败 {status} {user}")
    uid = user["id"]
    print(f"账号 {email} (id={uid})")

    status, tok = call("POST", "/login", None, {"email": email, "password": "Probe-2026-Test"})
    token = tok["access_token"]

    await seed(uid)
    print("已灌入：计划(3 步未做，提醒已过点) + 3 天前的数学错题 + 停滞 12 天的投递 + 化学 58 分记录\n")

    status, tools = call("GET", "/agent/chat/tools?page=dashboard", token=token)
    names = [t["name"] for t in tools.get("tools", [])]
    print(f"[tools?page=dashboard] {status} -> {names}")

    for question in (
        "我现在最该做什么？为什么是它而不是别的？",
        "我投递那边怎么样？",
    ):
        status, res = call("POST", "/agent/chat", token,
                           {"message": question, "page": "dashboard"})
        print(f"\n问：{question}\n答（{status}）：\n{res.get('reply', res)}\n" + "-" * 60)

    await cleanup(uid)
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
