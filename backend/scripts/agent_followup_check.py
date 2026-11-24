"""验证「追问按钮」：每轮回答是否给出可点的下一句，点了能不能真的进入下一轮。

为什么单独测：按钮的文案是模型生成的，点一下就是一次真实请求。所以光看
「字段有没有返回」不够——要真的把返回的第一条追问当成用户输入发出去，
确认能拿到新一轮回答、会话没断、并且新一轮又给出了新的追问。

用法（backend 目录）：
    python scripts/agent_followup_check.py                 # 打本地
    python scripts/agent_followup_check.py https://xxx     # 打线上
"""
from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
import time
import urllib.error
import urllib.request

PASS, FAIL = 0, 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(f"{name}: {detail}")
        print(f"FAIL  {name}" + (f"  [{detail}]" if detail else ""))
    return ok


def rand(n=8):
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def call(base, method, path, token=None, body=None, timeout=180):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def call_auth(base, path, body, attempts=4):
    for i in range(attempts):
        code, data = call(base, "POST", path, body=body)
        if code != 429:
            return code, data
        time.sleep(7 * (i + 1))
    return code, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    ap.add_argument("--page", default="dashboard", help="在哪个页面测（默认仪表盘）")
    ap.add_argument("--clicks", type=int, default=3, help="连续点几次追问")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    print(f"目标：{base}  页面：{args.page}\n")

    email = f"fu_{rand()}@example.com"
    password = "Passw0rd!" + rand(6)
    code, body = call_auth(base, "/api/register", {
        "email": email, "password": password, "full_name": "追问测试", "role": "student",
    })
    if code != 200:
        print(f"注册失败：HTTP {code} {body}")
        return 1
    token = body.get("access_token") or body.get("token")
    if not token:
        code, body = call_auth(base, "/api/login", {"email": email, "password": password})
        token = body.get("access_token") or body.get("token")
    if not token:
        print(f"拿不到 token：{body}")
        return 1

    def ask(message, session_id):
        return call(base, "POST", "/api/agent/chat", token, {
            "page": args.page, "message": message,
            **({"session_id": session_id} if session_id else {}),
        })

    code, data = ask("我现在最该做什么？", None)
    if code != 200:
        print(f"首轮失败：HTTP {code} {data}")
        return 1
    session_id = data.get("session_id")
    check("首轮：接口可用", bool(data.get("reply")), f"session={str(session_id)[:8]}")

    # ---- 返回结构 ----
    followups = data.get("followups")
    check("首轮：返回 followups 字段", isinstance(followups, list), f"类型 {type(followups).__name__}")
    followups = followups if isinstance(followups, list) else []
    check("首轮：给出 2~3 条追问", 2 <= len(followups) <= 3, f"{len(followups)} 条 {followups}")

    for text in followups:
        check(
            f"追问可用：「{str(text)[:18]}」",
            isinstance(text, str) and 4 <= len(text) <= 40,
            f"长度 {len(text) if isinstance(text, str) else 'N/A'}",
        )
    check("追问无重复", len(set(followups)) == len(followups), f"{followups}")

    # 追问不能是「空话」，否则点下去等于让模型再寒暄一遍
    EMPTY = ("还需要", "还有什么", "需要我", "是否还需要", "要不要我")
    bad = [t for t in followups if any(t.startswith(e) for e in EMPTY)]
    check("追问不是寒暄空话", not bad, f"{bad}" if bad else "干净")

    # ---- 真的点下去：连续点，每一轮都要能进 ----
    print()
    current = followups
    for click in range(1, args.clicks + 1):
        if not current:
            check(f"第 {click} 次点击：有可点的追问", False, "上一轮没给追问，链条断了")
            break
        text = current[0]
        print(f"      第 {click} 次点击 → 「{text}」")
        code, data = ask(text, session_id)
        if code != 200:
            check(f"第 {click} 次点击：进入下一轮", False, f"HTTP {code} {str(data)[:100]}")
            break
        check(
            f"第 {click} 次点击：拿到新一轮回答",
            bool(str(data.get("reply") or "").strip()),
            str(data.get("reply"))[:50],
        )
        check(
            f"第 {click} 次点击：会话没断",
            data.get("session_id") == session_id,
            f"{str(data.get('session_id'))[:8]}",
        )
        current = data.get("followups") or []
        check(
            f"第 {click} 次点击：又给出新追问",
            2 <= len(current) <= 3,
            f"{len(current)} 条",
        )

    # ---- 追问不落库：刷新后不该出现一堆过期按钮 ----
    code, data = call(base, "GET", f"/api/agent/chat/history?session_id={session_id}", token)
    if code == 200:
        items = data.get("messages") or []
        with_fu = [m for m in items if m.get("followups")]
        check("刷新后历史里没有残留追问按钮", not with_fu, f"{len(with_fu)} 条带 followups")
        check("刷新后对话条数正确", len(items) >= 2, f"{len(items)} 条")

    # ---- 脏数据清洗：直接把畸形 followups 塞给清洗函数 ----
    try:
        sys.path.insert(0, ".")
        from edu_app.api.agent_chat import _clean_followups

        dirty = [
            "1. 帮我看下错题",
            "「帮我改简历」",
            "- 这周进度怎么样",
            "",
            None,
            "重复的",
            "重复的",
            "x" * 80,
            "帮我看下错题",
        ]
        cleaned = _clean_followups(dirty)
        check("清洗：去掉编号与引号", "帮我看下错题" in cleaned, f"{cleaned}")
        check("清洗：丢掉空串/None", all(isinstance(c, str) and c for c in cleaned), f"{cleaned}")
        check("清洗：去重", len(set(cleaned)) == len(cleaned), f"{cleaned}")
        check("清洗：丢掉超长", all(len(c) <= 40 for c in cleaned), f"{cleaned}")
        check("清洗：最多 3 条", len(cleaned) <= 3, f"{len(cleaned)} 条")
        check("清洗：非 list 输入返回空", _clean_followups("不是数组") == [], "已兜底")
    except Exception as e:  # noqa: BLE001
        check("清洗：可直接导入测试", False, f"{e}")

    print(f"\n通过 {PASS}，失败 {FAIL}")
    if FAILED:
        print("\n失败项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
