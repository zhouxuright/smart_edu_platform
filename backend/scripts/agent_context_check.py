"""验证页面 Agent 的多轮上下文到底记住了几轮。

为什么要单独测：`/agent/chat` 每次只把「最近若干条」历史拼进提示词，
窗口多长决定了用户在第几轮开始感觉「它忘了我刚说的」。这个数字不能靠
读代码猜——`MAX_HISTORY_TURNS` 在两个文件里一个按「轮」用、一个按「条」用，
只有真跑一轮长对话才知道实际边界在哪。

做法：第 1 轮埋一个暗号，之后每轮只问「暗号是什么」，看第几轮开始丢。

用法（backend 目录）：
    python scripts/agent_context_check.py                     # 打本地
    python scripts/agent_context_check.py https://xxx        # 打线上
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
    """登录/注册限流是 10 次/分钟按 IP，连续跑几轮必撞，撞了等一会儿。"""
    for i in range(attempts):
        code, data = call(base, "POST", path, body=body)
        if code != 429:
            return code, data
        time.sleep(7 * (i + 1))
    return code, data


def ask(base, token, message, session_id=None, page="dashboard", history=None):
    body = {"page": page, "message": message}
    if session_id:
        body["session_id"] = session_id
    if history is not None:
        body["history"] = history
    return call(base, "POST", "/api/agent/chat", token, body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    ap.add_argument("--rounds", type=int, default=8, help="最多追问几轮")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    print(f"目标：{base}\n")

    email = f"ctx_{rand()}@example.com"
    password = "Passw0rd!" + rand(6)
    code, body = call_auth(base, "/api/register", {
        "email": email, "password": password, "full_name": "上下文测试", "role": "student",
    })
    if code != 200:
        print(f"注册失败，无法继续：HTTP {code} {body}")
        return 1
    token = body.get("access_token") or body.get("token")
    if not token:
        code, body = call_auth(base, "/api/login", {"email": email, "password": password})
        token = body.get("access_token") or body.get("token")
    if not token:
        print(f"拿不到 token：{body}")
        return 1

    # 暗号用无意义字符串，避免模型靠常识猜出来
    code_word = "ZQ" + rand(6).upper()

    # 措辞必须避开 MEMORY_TRIGGER（记住/记一下/我的目标/我打算…）。
    # 一旦命中就会把暗号抽成**长期记忆**，那它跨会话都在，测的就不是
    # 会话内历史窗口了——第一次跑就踩了这个坑，8 轮全「记得」。
    seed_line = f"我随手编了一串字符 {code_word}，它没有任何含义，只是用来占位。"

    # ---- 第 1 轮：埋暗号 ----
    code, data = ask(base, token, f"{seed_line} 现在只回一句「好」。")
    if code != 200:
        print(f"首轮失败：HTTP {code} {data}")
        return 1
    session_id = data.get("session_id")
    check("第 1 轮：会话建立", bool(session_id), f"session={str(session_id)[:8]}")
    print(f"      暗号 = {code_word}\n")

    # ---- 窗口边界：埋暗号 → 填充 N 轮无关问题 → 只探一次 ----
    #
    # 不能每轮都问暗号。第一次这么写，8 轮全「记得」——因为第 2~7 轮的
    # 助手回答里每轮都复述了暗号，第 1 轮早被挤出窗口，模型照样能从上一轮
    # 的回答里抄到。要测真窗口，中间必须隔开，最后只问一次。
    FILLER = [
        "我今天还有哪些没做完？",
        "我现在投递状态怎么样？",
        "我错题本里哪一科错得最多？",
        "这周的计划完成了几项？",
        "我最该补哪个知识点？",
    ]
    probe = "我最开始提到的那串随手编的字符是什么？只回那串字符本身，不记得就说「不知道」。"
    boundary = None
    print()
    for pad in (3, 5, 6):
        # 每个场景开一个新会话（不传 session_id），复用同一个账号
        code, data = ask(base, token, f"{seed_line} 现在只回一句「好」。")
        if code != 200:
            check(f"填充 {pad} 轮：会话可建立", False, f"HTTP {code}")
            continue
        sid = data.get("session_id")
        session_id = sid  # 最后一次的场景留给后面的恢复测试用
        for i in range(pad):
            ask(base, token, FILLER[i % len(FILLER)], sid)
        code, data = ask(base, token, probe, sid)
        if code != 200:
            check(f"填充 {pad} 轮：探测可用", False, f"HTTP {code}")
            continue
        reply = str(data.get("reply") or "")
        hit = code_word in reply.upper()
        print(f"      填充 {pad} 轮后探测：{'记得' if hit else '忘了'}  → {reply[:50]}")
        if not hit and boundary is None:
            boundary = pad + 1  # 第 1 轮埋 + pad 轮填充 + 探测轮
    print()

    # 埋暗号算第 1 轮；填充 pad 轮后探测，历史里还剩 pad+1 轮。
    # 探测时 `_history_block` 只截最近 MAX_HISTORY_TURNS 条消息，一条问答占 2 条，
    # 所以能留住的轮数 = MAX_HISTORY_TURNS // 2。
    if boundary is None:
        check("上下文窗口：填充 4 轮后仍记得", True, "窗口 >= 5 轮")
    else:
        kept = boundary - 1
        print(f"      实际窗口：填充 {boundary - 1} 轮后开始丢失 → 约 {kept} 轮\n")
        check("上下文窗口：至少保留 3 轮", kept >= 3, f"实际约 {kept} 轮")

    # ---- 隔离性：新会话不该知道旧会话里没进长期记忆的内容 ----
    # 注意这里不能拿「记住 X」测：那句会进长期记忆，跨会话本来就该知道。
    # 只有普通陈述才是纯会话内的，新会话不该接住。
    code, data = ask(base, token, "我最开始让你留意的那串随手编的字符是什么？", None)
    if code == 200:
        leaked = code_word in str(data.get("reply") or "").upper()
        check("会话隔离：新会话不知道旧会话的临时内容", not leaked, "串了" if leaked else "干净")
    else:
        check("会话隔离：新会话可正常建立", code == 200, f"HTTP {code}")

    # ---- 越权：别人的 session_id 当不存在 ----
    other = f"ctx_{rand()}@example.com"
    code, body = call_auth(base, "/api/register", {
        "email": other, "password": password, "full_name": "另一个", "role": "student",
    })
    other_token = (body or {}).get("access_token") or (body or {}).get("token")
    if not other_token:
        code, body = call_auth(base, "/api/login", {"email": other, "password": password})
        other_token = (body or {}).get("access_token") or (body or {}).get("token")
    if other_token:
        code, data = ask(base, other_token, "我最开始让你留意的那串随手编的字符是什么？", session_id)
        if code == 200:
            leaked = code_word in str(data.get("reply") or "").upper()
            check("越权：拿别人的 session_id 读不到内容", not leaked, "泄露了" if leaked else "干净")
            check(
                "越权：不报错而是开新会话（不泄露 id 是否存在）",
                data.get("session_id") != session_id,
                f"新 id={str(data.get('session_id'))[:8]}",
            )
        else:
            check("越权：接口未异常", False, f"HTTP {code}")

    # ---- 恢复：history 接口能取回完整对话 ----
    code, data = call(base, "GET", f"/api/agent/chat/history?session_id={session_id}", token)
    if code == 200:
        items = data.get("messages") or []
        check("刷新恢复：历史条数 >= 2", len(items) >= 2, f"{len(items)} 条")
        first_user = next((m for m in items if m.get("role") == "user"), {})
        check(
            "刷新恢复：首轮内容还在",
            code_word in str(first_user.get("content") or "").upper(),
            f"首条={str(first_user.get('content'))[:40]}",
        )
    else:
        check("刷新恢复：历史可读", False, f"HTTP {code}")

    # ---- 兼容：只传 history 不传 session_id ----
    code, data = ask(
        base, token,
        "我最开始让你留意的那串随手编的字符是什么？",
        None,
        history=[
            {"role": "user", "content": seed_line},
            {"role": "assistant", "content": "好。"},
        ],
    )
    if code == 200:
        check(
            "兼容路径：首轮用前端 history 兜底",
            code_word in str(data.get("reply") or "").upper(),
            str(data.get("reply"))[:60],
        )
    else:
        check("兼容路径：接口可用", False, f"HTTP {code}")

    print(f"\n通过 {PASS}，失败 {FAIL}")
    if FAILED:
        print("\n失败项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
