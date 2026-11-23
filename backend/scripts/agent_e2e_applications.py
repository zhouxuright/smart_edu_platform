"""Agent 投递工具验收：读上下文 + 推进状态 + 状态机拦截。

跑法（后端需已启动在 8000）：
    python scripts/agent_e2e_applications.py
"""
from __future__ import annotations

import json
import secrets
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


def main() -> int:
    status, data = call("POST", "/login", None, {"email": EMAIL, "password": PASSWORD})
    token = data["access_token"]
    check("登录成功", status == 200)

    status, apps = call("GET", "/applications", token)
    items = apps if isinstance(apps, list) else apps.get("items", [])
    if not items:
        # 演示账号没数据，先造两条，否则整条链路无从验证
        for company, position, st in (
            ("星河科技", "产品实习生", "applied"),
            ("蓝湖数据", "数据产品实习生", "draft"),
        ):
            call("POST", "/applications", token,
                 {"company": company, "position": position, "channel": "campus", "status": st})
        status, apps = call("GET", "/applications", token)
        items = apps if isinstance(apps, list) else apps.get("items", [])
        print("  已补 2 条演示投递记录")
    check("取到投递记录", status == 200 and len(items) > 0, f"{len(items)} 条")
    if not items:
        return 1

    # 自己造一条「已投递」的探针记录来测状态机。
    # 原来是从演示账号里挑一条 applied/screening/interview 的，但只要上一次跑过，
    # 那条就被推进到 interview 了——而 interview -> offer 是合法路径，
    # 「非法跳级」这条断言必然误报。探针记录用完即删，不污染演示数据。
    probe_company = f"探针公司{secrets.token_hex(2)}"
    status, probe = call("POST", "/applications", token, {
        "company": probe_company,
        "position": "状态机探针",
        "channel": "campus",
        "status": "applied",
    })
    if status not in (200, 201) or not isinstance(probe, dict) or not probe.get("id"):
        print(f"  探针投递创建失败：{status} {probe}")
        return 1
    probe_id = probe["id"]
    target = probe
    print("  目标记录:", target.get("company"), target.get("position"), "状态:", target.get("status"))

    # 读上下文
    status, answer = call("POST", "/agent/chat", token, {
        "page": "applications",
        "message": "我现在投递情况怎么样，哪条最该跟进？",
    })
    check("问答返回 200", status == 200, f"status={status}")
    if status == 200:
        check("读工具已执行", "list_applications" in (answer.get("context_used") or []),
              str(answer.get("context_used")))
        check("问答不动手", answer.get("proposal") is None)
        print("  AI:", (answer.get("reply") or "")[:140])

    # 状态机拦截：让模型尝试非法跳级，服务端必须拒绝提案
    status, bad = call("POST", "/agent/chat", token, {
        "page": "applications",
        "message": f"把「{target.get('company')}」这条投递直接改成 offer 状态",
    })
    notice = (bad.get("notice") or "") if status == 200 else ""
    check("非法跳级被拦下", status == 200 and bad.get("proposal") is None, notice[:80])
    if notice:
        print("  拦截原因:", notice)

    # 正常推进：让模型走状态机允许的路径
    status, ok_resp = call("POST", "/agent/chat", token, {
        "page": "applications",
        "message": f"把「{target.get('company')}」这条投递推进到下一步",
    })
    proposal = ok_resp.get("proposal") if status == 200 else None
    check("生成推进提案", isinstance(proposal, dict),
          str(ok_resp.get("notice") or ok_resp)[:120])
    if not isinstance(proposal, dict):
        print(json.dumps(ok_resp, ensure_ascii=False)[:400])
        return 1
    preview = proposal.get("preview") or {}
    print("  提案:", preview.get("title"), preview.get("from_label"), "->", preview.get("to_label"))

    status, applied = call("POST", "/agent/chat/confirm", token,
                           {"proposal_id": proposal["id"], "accept": True})
    check("确认推进成功", status == 200 and applied.get("status") == "applied", str(applied)[:200])

    status, apps2 = call("GET", "/applications", token)
    items2 = apps2 if isinstance(apps2, list) else apps2.get("items", [])
    now = next((a for a in items2 if a.get("id") == target.get("id")), {})
    check("状态已落库", now.get("status") == preview.get("to"),
          f"{target.get('status')} -> {now.get('status')}")

    # 跨页越权：简历页的会话不该能推进投递
    status, cross = call("POST", "/agent/chat", token, {
        "page": "resume",
        "message": f"把投递记录 {target.get('id')} 推进到 screening",
    })
    cross_proposal = (cross.get("proposal") or {}) if status == 200 else {}
    check("跨页写工具被隔离", cross_proposal.get("tool") != "advance_application",
          str(cross_proposal.get("tool")))

    # 探针记录用完即删，别留在演示账号里
    del_status, _ = call("DELETE", f"/applications/{probe_id}", token)
    check("探针记录已清理", del_status in (200, 204), f"HTTP {del_status}")

    print()
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
