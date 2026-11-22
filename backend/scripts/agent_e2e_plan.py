"""计划页 Agent 验收：读计划 → 勾任务 → 确认落库 → 漏斗埋点也在。

跑法（后端需已启动在 8000）：
    python scripts/agent_e2e_plan.py

演示账号没有计划时会先调生成接口造一份（生成后保留，属于合理的演示数据）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from edu_app.core.database import AsyncSessionLocal, engine
from edu_app.models.plan import PlanEvent

engine.echo = False
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

BASE = "http://127.0.0.1:8000/api"
EMAIL = "demo@example.com"
PASSWORD = "Demo1234!"

passed, failed = [], []


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return error.code, {"raw": raw}


def check(name: str, ok: bool, detail: str = ""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {detail}" if detail else ""))


async def count_events(task_id: int) -> dict[str, int]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(PlanEvent.event).where(PlanEvent.task_id == task_id))
        ).scalars().all()
    out: dict[str, int] = {}
    for name in rows:
        out[name] = out.get(name, 0) + 1
    return out


async def main() -> int:
    status, data = call("POST", "/login", None, {"email": EMAIL, "password": PASSWORD})
    token = data["access_token"]
    check("登录成功", status == 200)

    status, current = call("GET", "/internship-plans/current", token)
    plan = current.get("plan")
    if not plan:
        status, generated = call("POST", "/internship-plans/generate", token, {
            "target_role": "产品实习生", "stage": "大三", "weekly_hours": 8,
        })
        plan = generated.get("plan")
        print(f"  已生成计划（source={plan.get('source') if plan else '失败'}）")
    check("取到计划", bool(plan), plan.get("target_role") if plan else "无")
    if not plan:
        return 1

    tasks = plan["tasks"]
    check("计划有 7 天任务", len(tasks) == 7, f"{len(tasks)} 条")

    status, tools = call("GET", "/agent/chat/tools?page=plan", token)
    names = [t["name"] for t in tools.get("tools", [])]
    check("计划页工具已注册", "get_plan" in names and "toggle_task" in names, str(names))
    check("计划页拿不到简历工具", "update_bullets" not in names)

    # ---- 只读问答
    status, answer = call("POST", "/agent/chat", token, {
        "page": "plan",
        "message": "我今天该做哪一步？",
    })
    check("问答返回 200", status == 200, f"status={status}")
    if status == 200:
        check("读工具已执行", "get_plan" in (answer.get("context_used") or []),
              str(answer.get("context_used")))
        check("问答不动手", answer.get("proposal") is None)
        print("  AI:", (answer.get("reply") or "")[:140])

    # ---- 勾任务：挑一条当前未完成的
    target = next((t for t in tasks if not t["completed"]), tasks[0])
    before_count = sum(1 for t in tasks if t["completed"])
    # 同一个任务可能被前几轮跑过，所以看增量而不是绝对值
    events_before = await count_events(target["id"])
    print("  目标任务:", f"第 {target['day_index']} 天 · {target['title']}")

    status, ok_resp = call("POST", "/agent/chat", token, {
        "page": "plan",
        "message": f"第 {target['day_index']} 天那个任务我已经做完了，帮我标成完成",
    })
    proposal = ok_resp.get("proposal") if status == 200 else None
    check("生成勾选提案", isinstance(proposal, dict),
          str(ok_resp.get("notice") or ok_resp.get("reply"))[:100])
    if not isinstance(proposal, dict):
        print(json.dumps(ok_resp, ensure_ascii=False)[:400])
        return 1

    preview = proposal.get("preview") or {}
    check("提案是状态流转", preview.get("kind") == "status",
          f"{preview.get('from_label')} -> {preview.get('to_label')}")
    check("提案指向正确任务", str(target["day_index"]) in str(preview.get("title")),
          str(preview.get("title")))

    status, applied = call("POST", "/agent/chat/confirm", token,
                           {"proposal_id": proposal["id"], "accept": True})
    check("确认执行成功", status == 200 and applied.get("status") == "applied", str(applied)[:160])

    status, after = call("GET", "/internship-plans/current", token)
    after_tasks = after["plan"]["tasks"]
    hit = next((t for t in after_tasks if t["id"] == target["id"]), {})
    check("任务已标记完成", hit.get("completed") is True, f"completed={hit.get('completed')}")
    check("完成数 +1", after["plan"]["completed_count"] == before_count + 1,
          f"{before_count} -> {after['plan']['completed_count']}")
    check("next_task 已顺延", after["plan"]["next_task"]["id"] != target["id"],
          str(after["plan"]["next_task"]["title"])[:40])

    # ---- 漏斗埋点：这是抽 plan_store 的全部理由，必须验
    events_after = await count_events(target["id"])
    check("写了 task_completed 埋点",
          events_after.get("task_completed", 0) == events_before.get("task_completed", 0) + 1,
          f"{events_before.get('task_completed', 0)} -> {events_after.get('task_completed', 0)}")

    # ---- 重复勾选应被拒
    status, again = call("POST", "/agent/chat", token, {
        "page": "plan",
        "message": f"第 {target['day_index']} 天那个任务再帮我标成完成",
    })
    check("重复勾选被拦下", status == 200 and again.get("proposal") is None,
          str(again.get("notice") or "")[:80])

    # ---- 反向：标回未完成
    status, undo = call("POST", "/agent/chat", token, {
        "page": "plan",
        "message": f"第 {target['day_index']} 天那个任务我其实还没做完，改回未完成",
    })
    undo_proposal = undo.get("proposal") if status == 200 else None
    if isinstance(undo_proposal, dict):
        status, undone = call("POST", "/agent/chat/confirm", token,
                              {"proposal_id": undo_proposal["id"], "accept": True})
        status, final = call("GET", "/internship-plans/current", token)
        back = next((t for t in final["plan"]["tasks"] if t["id"] == target["id"]), {})
        check("能改回未完成", undone.get("status") == "applied" and back.get("completed") is False,
              f"completed={back.get('completed')}")
    else:
        check("能改回未完成", False, str(undo.get("notice") or undo.get("reply"))[:80])

    print()
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
