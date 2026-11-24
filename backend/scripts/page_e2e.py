"""页面级端到端：把每个前端页面「用户点下去」的那个动作真实打一遍。

和 `smoke_remote.py` 的区别：smoke 只验证「服务活着、表建出来了」，
这个脚本验证「点进页面 → 触发接口 → 拿到符合前端预期的数据」。
它回答的是「页面上的按钮到底能不能用」，而不是「进程有没有起来」。

覆盖 13 个受保护页面 + 5 个流式接口。流式接口不能只判状态码——
200 也可能是空流，所以读到第一段 SSE 数据才算通过。

用法（backend 目录）：
    python scripts/page_e2e.py                          # 打本地
    python scripts/page_e2e.py https://xxx.up.railway.app   # 打线上

注意：会注册一个 e2e_ 前缀的探针账号并消耗约 10 次 AI 额度。
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


def call(base, method, path, token=None, body=None, timeout=90):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def call_stream(base, path, token, body=None, timeout=90):
    """读 SSE 的第一段数据。只判 200 不够：空流也是 200。"""
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            first = resp.read(400).decode("utf-8", "replace")
            return resp.status, ctype, first
    except urllib.error.HTTPError as e:
        return e.code, "", e.read(300).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, "", str(e)


def call_auth(base, path, body, retries=2):
    """登录/注册有独立的限流（默认 10 次/分钟，按 IP）。

    跑完一轮 E2E 会消耗其中一半，如果紧接着再跑就会撞 429。
    撞了就等一会儿重试，而不是把它当成功能失败——那不是被测对象的问题。
    """
    code, data = call(base, "POST", path, None, body)
    for _ in range(retries):
        if code != 429:
            break
        wait = 20
        if isinstance(data, dict):
            wait = min(int(data.get("retry_after") or wait), 65)
        print(f"      （限流 429，等待 {wait}s 后重试）")
        time.sleep(wait)
        code, data = call(base, "POST", path, None, body)
    return code, data


def rand(n=8):
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    print(f"目标：{base}\n")

    email = f"e2e_{rand()}@example.com"
    password = "Passw0rd!" + rand(6)

    # ---- 注册 / 登录 ----
    code, body = call_auth(base, "/api/register", {
        "email": email, "password": password, "full_name": "页面E2E", "role": "student",
    })
    check("注册", code == 200, f"HTTP {code}")
    code, body = call_auth(base, "/api/login", {"email": email, "password": password})
    token = (body or {}).get("access_token") if isinstance(body, dict) else None
    check("登录拿到 token", code == 200 and bool(token), f"HTTP {code}")

    if not token:
        print("\n拿不到 token，后续全部跳过")
        return 1

    # ---- 1. 仪表盘 ----
    code, body = call(base, "GET", "/api/dashboard/summary", token)
    ok = code == 200 and isinstance(body, dict) and "recommendations" in body
    check("仪表盘：概览可读", ok, f"HTTP {code}, 推荐{len((body or {}).get('recommendations') or [])}条")

    code, body = call(base, "POST", "/api/learning_records", token, {
        "subject": "数学", "topic": "立体几何", "activity_type": "quiz", "score": 62,
    })
    check("仪表盘：记录学习活动", code in (200, 201), f"HTTP {code}")

    # ---- 2. 我的计划 ----
    code, body = call(base, "GET", "/api/internship-plans/current", token)
    check("我的计划：读当前计划", code == 200, f"HTTP {code}")

    code, body = call(base, "POST", "/api/internship-plans/generate", token, {
        "target_role": "数据分析实习生", "stage": "大三", "weekly_hours": 10, "replace": True,
    })
    check("我的计划：生成计划（调模型）", code in (200, 201), f"HTTP {code}")

    # ---- 2.5 多学科知识抽取 ----
    code, body = call(base, "POST", "/api/knowledge/extract", token, {
        "text": "立体几何中，直线与平面垂直的判定定理：如果一条直线与平面内两条相交直线都垂直，"
                "那么这条直线与这个平面垂直。线面角是斜线与它在平面内射影所成的角。",
        "subject": "数学",
    })
    check("知识抽取：抽取知识点（调模型）", code == 200, f"HTTP {code}")

    # ---- 3. 智能错题本 ----
    code, body = call(base, "POST", "/api/mistakes/", token, {
        "subject": "数学", "topic": "立体几何", "question_content": "正方体体对角线夹角是多少？",
    })
    mistake_id = (body or {}).get("id") if isinstance(body, dict) else None
    check("错题本：新增错题", code in (200, 201) and bool(mistake_id), f"HTTP {code}")

    code, body = call(base, "GET", "/api/mistakes/", token)
    check("错题本：列表可读", code == 200, f"HTTP {code}")

    # ---- 4. 简历工作台 ----
    # 必须带真实内容：模拟面试的 prepare 会拒收「不足 50 字」的空简历，
    # 那是产品校验不是 bug，所以 E2E 要造一份像样的简历。
    resume_payload = {
        "title": "E2E 简历",
        "target_role": "数据分析实习生",
        "data": {
            "basics": {
                "name": "端到端",
                "email": "e2e@example.com",
                "city": "上海",
                "summary": "统计学本科三年级，做过两个数据分析项目，熟悉 SQL 与 Python，"
                           "擅长用数据讲清楚业务问题。",
            },
            "experience": [
                {
                    "company": "某电商公司",
                    "role": "数据分析实习生",
                    "period": "2026.03-2026.08",
                    "bullets": [
                        "负责用户留存分析，搭建 SQL 取数口径，把日活口径统一到一张宽表。",
                        "用 Python 做漏斗分析，定位注册到首单流失最大的环节，推动改版后转化提升 8%。",
                    ],
                }
            ],
            "projects": [
                {
                    "name": "校园二手交易平台用户行为分析",
                    "role": "负责人",
                    "bullets": ["爬取并清洗 1.2 万条交易记录，用聚类划分四类用户并给出运营建议。"],
                }
            ],
        },
    }
    code, body = call(base, "POST", "/api/resumes", token, resume_payload)
    resume_id = (body or {}).get("id") if isinstance(body, dict) else None
    check("简历：新建", code in (200, 201) and bool(resume_id), f"HTTP {code}")

    if resume_id:
        code, body = call(base, "GET", f"/api/resumes/{resume_id}", token)
        check("简历：读详情", code == 200, f"HTTP {code}")

        url = f"{base}/api/resumes/{resume_id}/pdf"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                head = resp.read(5)
                check("简历：导出 PDF", resp.status == 200 and head.startswith(b"%PDF"),
                      f"HTTP {resp.status}, magic={head[:4]!r}")
        except Exception as e:  # noqa: BLE001
            check("简历：导出 PDF", False, str(e)[:80])

    # ---- 5. 投递看板 ----
    code, body = call(base, "POST", "/api/applications", token, {
        "company": "E2E 公司", "position": "数据分析实习", "status": "applied",
    })
    check("投递：新建记录", code in (200, 201), f"HTTP {code}")
    code, body = call(base, "GET", "/api/applications", token)
    check("投递：列表可读", code == 200, f"HTTP {code}")
    code, body = call(base, "GET", "/api/applications/stats", token)
    check("投递：统计可读", code == 200, f"HTTP {code}")

    # ---- 6. 企业知识库 ----
    code, body = call(base, "POST", "/api/rag/documents/batch-text", token, {
        "items": [{"title": "E2E 文档", "content": "这是一段用于端到端验证的知识库文本，内容包含数据分析与用户研究。"}],
    })
    check("知识库：文本入库", code in (200, 201), f"HTTP {code}")

    code, body = call(base, "GET", "/api/rag/documents", token)
    check("知识库：文档列表", code == 200, f"HTTP {code}")

    code, body = call(base, "POST", "/api/rag/search", token, {"query": "数据分析", "top_k": 3})
    check("知识库：检索", code == 200, f"HTTP {code}")

    # ---- 7. 工作流：JD 匹配 / 模拟面试 ----
    jd_text = (
        "岗位：数据分析实习生（2027 届）。岗位职责：1. 负责用户行为数据的日常取数与看板维护；"
        "2. 参与留存、转化等核心指标的异动归因分析；3. 配合产品与运营做 A/B 实验设计与效果评估。"
        "任职要求：1. 熟练使用 SQL，能独立写多表关联与窗口函数；2. 掌握 Python 数据分析栈"
        "（pandas、numpy、matplotlib）；3. 有统计学基础，理解假设检验与置信区间；"
        "4. 每周到岗 4 天以上，实习期 6 个月优先。"
    )
    code, body = call(base, "POST", "/api/agent/runs", token, {"kind": "jd_match", "input": {
        "jd_text": jd_text,
        "target_role": "数据分析实习生",
        "resume_id": resume_id,
    }})
    jd_run = (body or {}).get("id") if isinstance(body, dict) else None
    jd_status = (body or {}).get("status") if isinstance(body, dict) else ""
    check("JD 匹配：流程跑完", code in (200, 201) and jd_status in ("succeeded", "awaiting_human"),
          f"HTTP {code}, status={jd_status}, err={(body or {}).get('error') if isinstance(body, dict) else ''}")

    code, body = call(base, "POST", "/api/agent/runs", token, {"kind": "mock_interview", "input": {
        "target_role": "数据分析实习生",
        "resume_id": resume_id,
    }})
    mi_run = (body or {}).get("id") if isinstance(body, dict) else None
    mi_status = (body or {}).get("status") if isinstance(body, dict) else ""
    check("模拟面试：启动流程", code in (200, 201) and bool(mi_run), f"HTTP {code}, status={mi_status}")
    check("模拟面试：停在等待作答", mi_status == "awaiting_human", f"status={mi_status}")

    if mi_run:
        code, body = call(base, "GET", f"/api/agent/runs/{mi_run}", token)
        has_q = isinstance(body, dict) and bool((body.get("state") or {}).get("_pending"))
        check("模拟面试：能取回题目", code == 200 and has_q, f"HTTP {code}")
        # 答一题，验证 resume 链路
        code, body = call(base, "POST", f"/api/agent/runs/{mi_run}/resume", token,
                          {"accept": True, "payload": {"answer": "我会先明确分析目标，再取数验证，最后给出结论。"}})
        check("模拟面试：提交回答推进", code == 200, f"HTTP {code}")

    # ---- 8. 额度 ----
    code, body = call(base, "GET", "/api/llm/quota", token)
    check("额度：可读", code == 200, f"HTTP {code}")

    # ---- 9. Agent 助手 ----
    code, body = call(base, "GET", "/api/agent/chat/tools?page=dashboard", token)
    check("Agent 助手：工具表可读", code == 200, f"HTTP {code}")
    code, body = call(base, "POST", "/api/agent/chat", token, {"message": "你好", "page": "dashboard"})
    check("Agent 助手：对话可用", code == 200, f"HTTP {code}")

    # ---- 10. 五个流式页面 ----
    # 学生能开的三个：导师、知识图谱、协作学习。
    student_streams = [
        ("AI 学习导师", "/api/agent/mentor/stream", {"message": "给我一条学习建议", "history": []}),
        ("知识图谱", "/api/agent/knowledge/stream", {"subject": "数学", "topic": "立体几何"}),
        ("协作学习", "/api/agent/collaborative/stream", {"topic": "如何准备数据分析实习", "history": []}),
    ]
    for label, path, body in student_streams:
        code, ctype, first = call_stream(base, path, token, body)
        got_data = bool(first.strip())
        check(f"流式：{label}", code == 200 and got_data,
              f"HTTP {code}, 首段{len(first)}字符")

    # ---- 11. 角色隔离：学生不该能看家长视图 / 学习评估 ----
    # 这里断言 403 才是正确行为。如果学生能拿到家长数据，那才是安全问题。
    for label, path, body in [
        ("家长门户", "/api/agent/parent/stream", {"student_id": 0}),
        ("学习评估", "/api/agent/assessment/stream", {"student_id": 0}),
    ]:
        code, ctype, first = call_stream(base, path, token, body)
        check(f"角色隔离：学生访问{label}被拒", code == 403, f"HTTP {code}")

    # ---- 12. 家长绑定全链路 ----
    # 这一段是「家长门户到底能不能用」的真正答案：
    # 学生生成邀请码 → 家长绑定 → 家长看孩子的数据 → 学生撤销 → 家长立刻失去权限。
    parent_email = f"e2e_parent_{rand()}@example.com"
    code, body = call_auth(base, "/api/register", {
        "email": parent_email, "password": password, "full_name": "E2E家长", "role": "parent",
    })
    check("家长：可自助注册家长账号", code == 200, f"HTTP {code}")
    code, body = call_auth(base, "/api/login", {"email": parent_email, "password": password})
    parent_token = (body or {}).get("access_token") if isinstance(body, dict) else None
    check("家长：登录", code == 200 and bool(parent_token), f"HTTP {code}")

    code, body = call_auth(base, "/api/register", {
        "email": f"e2e_admin_{rand()}@example.com", "password": password,
        "full_name": "想自封管理员", "role": "admin",
    })
    check("注册：不允许自封 admin", code == 400, f"HTTP {code}")

    if parent_token:
        code, body = call(base, "GET", "/api/parent/children", parent_token)
        check("家长：绑定前孩子列表为空", code == 200 and not (body or {}).get("items"),
              f"HTTP {code}")

        code, body = call(base, "POST", "/api/parent/invite", token, {})
        invite_code = (body or {}).get("code") if isinstance(body, dict) else None
        check("学生：生成邀请码", code == 200 and bool(invite_code), f"HTTP {code}")

        if invite_code:
            code, body = call(base, "POST", "/api/parent/link", parent_token, {"code": invite_code})
            check("家长：用码绑定孩子", code == 200 and (body or {}).get("linked"), f"HTTP {code}")

            code, body = call(base, "POST", "/api/parent/link", parent_token, {"code": invite_code})
            check("邀请码一次性（重复使用被拒）", code == 409, f"HTTP {code}")

            code, body = call(base, "GET", "/api/parent/children", parent_token)
            kids = (body or {}).get("items") or []
            check("家长：孩子列表出现 1 个", len(kids) == 1, f"HTTP {code}, {len(kids)} 个")
            child_id = kids[0]["student_id"] if kids else None

            if child_id:
                code, body = call(base, "GET", f"/api/dashboard/summary?student_id={child_id}", parent_token)
                # 关键断言：拿到的是孩子的数据，不是家长自己的空数据
                check("家长：仪表盘显示孩子的数据", code == 200 and (body or {}).get("student_id") == child_id,
                      f"HTTP {code}, name={(body or {}).get('name')}")

                code, ctype, first = call_stream(base, "/api/agent/parent/stream", parent_token,
                                                 {"student_id": child_id})
                check("家长：能生成孩子报告", code == 200 and bool(first.strip()), f"HTTP {code}")

                code, body = call(base, "GET", "/api/parent/viewers", token)
                viewers = (body or {}).get("items") or []
                check("学生：能看到谁在看自己的学情", code == 200 and len(viewers) == 1,
                      f"HTTP {code}, {len(viewers)} 人")

                if viewers:
                    parent_id = viewers[0]["parent_id"]
                    code, body = call(base, "DELETE", f"/api/parent/viewers/{parent_id}", token)
                    check("学生：撤销家长权限", code == 200, f"HTTP {code}")

                    code, body = call(base, "GET", f"/api/dashboard/summary?student_id={child_id}", parent_token)
                    check("撤销后家长立即失去访问", code == 403, f"HTTP {code}")

    # ---- 13. 计费接口不该被普通用户碰 ----
    # 订单创建/确认都是 admin 专属。普通学生能建单，等于谁都能给自己充值。
    code, body = call(base, "POST", "/api/saas/order/create", token, {
        "tenant_id": "TEN_ANY", "amount": 299, "order_type": "token_topup",
    })
    check("计费：学生不能创建充值订单", code == 403, f"HTTP {code}")

    code, body = call(base, "POST", "/api/saas/order/ORD_FAKE/confirm", token, {})
    check("计费：学生不能确认收款", code == 403, f"HTTP {code}")

    print(f"\n通过 {PASS}，失败 {FAIL}")
    if FAILED:
        print("\n失败明细：")
        for f in FAILED:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
