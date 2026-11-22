"""文档级回退链路回归测试。

场景：文档有 DocumentEmbedding 但没有 DocumentChunk（历史数据 / 旧入库接口），
且向量维度混杂（换 embedding 模型前后的数据并存）。
此时代码必须走线性扫描并按当前查询维度过滤，不能因 np.array 维度不齐而 500。

用法：python scripts/test_legacy_fallback.py
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg

from edu_app.services.auth import get_password_hash
from edu_app.services.embedding import embed_text

DSN = os.getenv("DIAG_DSN", "postgresql://postgres:CHANGE_ME@localhost:5432/smart_edu")
BASE = "http://127.0.0.1:8000/api"
EMAIL = "legacy_fallback_probe@example.com"
PASSWORD = "ProbePass123"


def post(path, payload, token=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


async def setup():
    conn = await asyncpg.connect(DSN)
    # 清掉上一轮残留
    uid = await conn.fetchval("select id from users where email=$1", EMAIL)
    if uid:
        await conn.execute("delete from document_embeddings where document_id in (select id from documents where owner_id=$1)", uid)
        await conn.execute("delete from document_chunks where document_id in (select id from documents where owner_id=$1)", uid)
        await conn.execute("delete from documents where owner_id=$1", uid)
        await conn.execute("delete from audit_events where user_id=$1", uid)
        await conn.execute("delete from users where id=$1", uid)

    uid = await conn.fetchval(
        "insert into users(email, hashed_password, full_name, role) values($1,$2,$3,'student') returning id",
        EMAIL, get_password_hash(PASSWORD), "回退链路探针",
    )

    # 两篇文档：一篇 1024 维（新），一篇 1536 维（旧），都没有分块
    d1 = await conn.fetchval(
        "insert into documents(title, content, owner_id) values($1,$2,$3) returning id",
        "新旧混合-新向量文档", "这篇文档讲的是如何准备面试中的自我介绍环节。", uid,
    )
    d2 = await conn.fetchval(
        "insert into documents(title, content, owner_id) values($1,$2,$3) returning id",
        "新旧混合-旧向量文档", "这篇文档讲的是简历排版和字体选择的注意事项。", uid,
    )

    # 新文档用真实向量，才能验证它确实能被检索到；
    # 旧文档用等长假向量，只验证它会被维度过滤跳过而不是让整条链路崩掉。
    vec_new = await embed_text("这篇文档讲的是如何准备面试中的自我介绍环节。")
    vec_old = [0.01] * 1536
    await conn.execute(
        "insert into document_embeddings(document_id, vector) values($1,$2::json),($3,$4::json)",
        d1, json.dumps(vec_new), d2, json.dumps(vec_old),
    )
    await conn.close()
    return uid


async def cleanup(uid):
    conn = await asyncpg.connect(DSN)
    await conn.execute("delete from document_embeddings where document_id in (select id from documents where owner_id=$1)", uid)
    await conn.execute("delete from document_chunks where document_id in (select id from documents where owner_id=$1)", uid)
    await conn.execute("delete from documents where owner_id=$1", uid)
    await conn.execute("delete from audit_events where user_id=$1", uid)
    await conn.execute("delete from users where id=$1", uid)
    await conn.close()


def main():
    uid = asyncio.run(setup())
    print(f"已创建探针用户 id={uid}（2 篇无分块文档，向量维度 1024/1536 混杂）")
    try:
        lg = post("/login", {"email": EMAIL, "password": PASSWORD})
        token = lg["access_token"]
        print("登录 ok")

        res = post("/rag/search", {"query": "面试自我介绍怎么准备", "top_k": 3}, token)
        mode = res.get("mode")
        results = res.get("results") or []
        print(f"mode={mode} 结果数={len(results)}")
        for r in results:
            print(f"  - {r.get('title')} score={r.get('score')}")

        assert mode != "chunk", "该用户没有分块，不应走块级链路"
        titles = [r.get("title") for r in results]
        assert "新旧混合-新向量文档" in titles, f"1024 维新文档应被检索到，实际返回 {titles}"
        assert "新旧混合-旧向量文档" not in titles, "1536 维旧向量应被跳过"
        print("\n通过：没有 500；新维度文档正常召回，旧维度向量被安全跳过。")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:400]
        print(f"\n失败：HTTP {e.code} {body}")
        return 1
    finally:
        asyncio.run(cleanup(uid))
        print("探针数据已清理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
