"""Agent 骨架端到端验收：登录 -> 读上下文 -> 生成提案 -> 确认执行 -> 校验落库。

跑法（后端需已启动在 8000）：
    python scripts/agent_e2e.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

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


def login() -> str:
    status, data = call("POST", "/login", None, {"email": EMAIL, "password": PASSWORD})
    if status != 200:
        raise SystemExit(f"登录失败: {status} {data}")
    return data["access_token"]


def main() -> int:
    token = login()
    check("登录成功", bool(token))

    status, tools = call("GET", "/agent/chat/tools?page=resume", token)
    names = [t["name"] for t in tools.get("tools", [])]
    check("简历页工具已注册", status == 200 and "update_bullets" in names, str(names))
    check("简历页拿不到投递工具", "advance_application" not in names)

    status, other = call("GET", "/agent/chat/tools?page=applications", token)
    other_names = [t["name"] for t in other.get("tools", [])]
    check("投递页工具已注册", "advance_application" in other_names, str(other_names))

    status, resumes = call("GET", "/resumes", token)
    items = resumes if isinstance(resumes, list) else resumes.get("items", [])
    check("取到简历列表", status == 200 and len(items) > 0, f"{len(items)} 份")
    if not items:
        return 1
    rid = items[0]["id"]
    status, resume = call("GET", f"/resumes/{rid}", token)
    sections = resume.get("data") or {}
    exp = sections.get("experience") or []
    check("简历里有实习经历", len(exp) > 0, f"{len(exp)} 段")
    if not exp:
        return 1
    before_bullets = exp[0].get("bullets") or []
    before_version = resume.get("version") or 0
    print("  改写前要点:", json.dumps(before_bullets, ensure_ascii=False))

    # ---- 只读问答：不该产生提案
    status, answer = call("POST", "/agent/chat", token, {
        "page": "resume",
        "page_id": rid,
        "message": "我这份简历现在最该补的是什么？",
    })
    check("问答返回 200", status == 200, f"status={status}")
    if status == 200:
        check("问答没有越权动手", answer.get("proposal") is None, str(answer.get("proposal"))[:80])
        check("问答带上了上下文", "get_resume" in (answer.get("context_used") or []),
              str(answer.get("context_used")))
        print("  AI:", (answer.get("reply") or "")[:120])

    # ---- 写操作：应产出提案而不是直接改
    status, proposal_resp = call("POST", "/agent/chat", token, {
        "page": "resume",
        "page_id": rid,
        "message": "第一段实习的要点帮我改写得更有结果感，保留原来的数字",
    })
    check("改写请求返回 200", status == 200, f"status={status}")
    if status != 200:
        print("  ", json.dumps(proposal_resp, ensure_ascii=False)[:300])
        return 1
    print("  AI:", (proposal_resp.get("reply") or "")[:120])
    proposal = proposal_resp.get("proposal")
    check("生成了提案", isinstance(proposal, dict), str(proposal_resp.get("notice")))
    if not proposal:
        return 1

    preview = proposal.get("preview") or {}
    check("提案含前后对比", "before" in preview and "after" in preview,
          json.dumps(preview, ensure_ascii=False)[:160])
    print("  提案:", proposal.get("summary"))
    print("  after:", json.dumps(preview.get("after"), ensure_ascii=False)[:200])

    status, after_chat = call("GET", f"/resumes/{rid}", token)
    still = ((after_chat.get("data") or {}).get("experience") or [{}])[0].get("bullets") or []
    check("未确认前简历没被改", still == before_bullets)

    # ---- 拒绝路径
    status, rejected = call("POST", "/agent/chat/confirm", token,
                            {"proposal_id": proposal["id"], "accept": False})
    check("拒绝提案成功", status == 200 and rejected.get("status") == "rejected", str(rejected))
    status, again = call("POST", "/agent/chat/confirm", token,
                         {"proposal_id": proposal["id"], "accept": True})
    check("已处理的提案不能再执行", status == 409, f"status={status}")

    # ---- 重新生成并确认执行
    status, proposal_resp2 = call("POST", "/agent/chat", token, {
        "page": "resume",
        "page_id": rid,
        "message": "再改一次第一段实习的要点，突出量化结果",
    })
    proposal2 = proposal_resp2.get("proposal")
    check("再次生成提案", isinstance(proposal2, dict))
    if not proposal2:
        return 1

    status, applied = call("POST", "/agent/chat/confirm", token,
                           {"proposal_id": proposal2["id"], "accept": True})
    check("确认执行成功", status == 200 and applied.get("status") == "applied", str(applied)[:200])

    status, after = call("GET", f"/resumes/{rid}", token)
    new_bullets = ((after.get("data") or {}).get("experience") or [{}])[0].get("bullets") or []
    check("要点已落库", new_bullets == (proposal2.get("preview") or {}).get("after"),
          json.dumps(new_bullets, ensure_ascii=False)[:160])
    check("版本号已增加", (after.get("version") or 0) > before_version,
          f"{before_version} -> {after.get('version')}")

    status, versions = call("GET", f"/resumes/{rid}/versions", token)
    rows = versions if isinstance(versions, list) else versions.get("items", [])
    notes = [v.get("note") for v in rows][:3]
    check("留了版本快照", any("AI 改写" in (n or "") for n in notes), str(notes))

    print()
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
