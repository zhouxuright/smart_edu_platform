"""检索质量 Top-1 评估脚本。

用法：
    python scripts/eval_retrieval.py                      # 默认账号 e2e_eval@example.com
    python scripts/eval_retrieval.py --email a@b.com --password xxx

评估思路：
    8 篇彼此独立的文档，每篇只讲一个主题；查询句刻意不与文档标题共享词面，
    逼检索器真正用语义而不是关键词重叠来命中。命中即 Top-1 返回的标题等于期望标题。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api"

# (查询, 期望命中的文档标题)
CASES = [
    ("怎样才能让我的履历更容易被挑中", "简历撰写"),
    ("投简历靠朋友介绍是不是更容易", "投递渠道"),
    ("面试被问到解决过什么问题该怎么讲", "面试应答"),
    ("想展示自己懂用户该准备什么材料", "作品集"),
    ("笔试里分组排名要用什么函数", "SQL笔试"),
    ("实习结束前要做哪些交接", "实习转正"),
    ("怎么开口问工资和年终奖", "薪资谈判"),
    ("找工作怎么排计划避免面试撞车", "时间规划"),
]


def post(path, payload, token=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="e2e_eval_1795264961@example.com")
    ap.add_argument("--password", default="EvalPass123")
    ap.add_argument("--top-k", type=int, default=3)
    args = ap.parse_args()

    try:
        login = post("/login", {"email": args.email, "password": args.password})
    except urllib.error.HTTPError as e:
        print(f"登录失败 {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
        return 1
    token = login["access_token"]
    print(f"登录成功 user_id={login.get('user_id')} role={login.get('role')}\n")

    hits = 0
    for i, (query, expect) in enumerate(CASES, 1):
        try:
            res = post("/rag/search", {"query": query, "top_k": args.top_k}, token)
        except urllib.error.HTTPError as e:
            print(f"{i}. {query} -> 请求失败 {e.code}: {e.read().decode('utf-8', 'ignore')[:150]}")
            continue

        results = res.get("results") or res.get("data") or []
        if not results:
            print(f"{i}. {query} -> 无结果")
            continue

        top = results[0]
        title = top.get("title") or top.get("document_title") or "(无标题)"
        score = top.get("score")
        ok = title.strip() == expect
        hits += 1 if ok else 0
        mark = "OK " if ok else "MISS"
        score_txt = f"{score:.3f}" if isinstance(score, (int, float)) else str(score)
        print(f"{i}. [{mark}] {query}")
        print(f"     期望: {expect} | 实际: {title} | 分数: {score_txt}")
        if not ok:
            for r in results[1:]:
                t = r.get("title") or r.get("document_title") or "(无标题)"
                s = r.get("score")
                print(f"       - 备选: {t} ({s:.3f})" if isinstance(s, (int, float)) else f"       - 备选: {t}")

    total = len(CASES)
    print(f"\nTop-1 命中率 {hits}/{total} = {hits * 100 // total}%")
    return 0 if hits == total else 0


if __name__ == "__main__":
    sys.exit(main())
