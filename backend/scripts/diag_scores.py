"""检索打分诊断：打印每个候选块的原始余弦分与 BM25 分，用于标定混合权重。

不经过 HTTP，直接读库 + 调 embedding，避免为调试污染线上接口。
"""
import asyncio
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
import numpy as np

from edu_app.services.embedding import embed_text
from edu_app.services.text_utils import tokenize

DSN = os.getenv("DIAG_DSN", "postgresql://postgres:CHANGE_ME@localhost:5432/smart_edu")
OWNER_ID = int(os.getenv("DIAG_OWNER", "31"))

QUERIES = [
    "怎样才能让我的履历更容易被挑中",
    "投简历靠朋友介绍是不是更容易",
]


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        select c.id, c.content, c.vector, d.title
        from document_chunks c join documents d on d.id = c.document_id
        where d.owner_id = $1
        order by c.document_id, c.seq
        """,
        OWNER_ID,
    )
    await conn.close()
    if not rows:
        print("该 owner 没有分块数据")
        return

    texts = [r["content"] or "" for r in rows]
    titles = [r["title"] for r in rows]
    doc_tokens = [tokenize(t) for t in texts]
    total = len(texts)

    df = {}
    for tokens in doc_tokens:
        for w in set(tokens):
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log(((total - n + 0.5) / (n + 0.5)) + 1) for w, n in df.items()}
    lengths = [len(t) or 1 for t in doc_tokens]
    avgdl = sum(lengths) / total if total else 1.0
    k1, b = 1.5, 0.75

    for query in QUERIES:
        qvec = np.array(await embed_text(query), dtype="float32")
        qvec = qvec / (np.linalg.norm(qvec) or 1.0)
        qt = tokenize(query)
        print(f"\n===== {query}")
        print(f"查询分词: {qt}")

        scored = []
        for i, r in enumerate(rows):
            raw = r["vector"]
            # json 列经 asyncpg 取出是字符串，必须自行反序列化
            vec = json.loads(raw) if isinstance(raw, str) else (raw or [])
            if vec and len(vec) == len(qvec):
                arr = np.array(vec, dtype="float32")
                dense = float(np.dot(arr / (np.linalg.norm(arr) or 1.0), qvec))
            else:
                dense = 0.0

            tf = {}
            for w in doc_tokens[i]:
                tf[w] = tf.get(w, 0) + 1
            bm25 = 0.0
            hit = []
            for w in qt:
                if w in tf:
                    bm25 += idf.get(w, 0.0) * (tf[w] * (k1 + 1)) / (
                        tf[w] + k1 * (1 - b + b * lengths[i] / avgdl)
                    )
                    hit.append(w)
            scored.append((titles[i], dense, bm25, hit))

        scored.sort(key=lambda x: x[1], reverse=True)
        print(f"{'标题':<12} {'余弦':>7} {'BM25':>8}  命中词")
        for title, dense, bm25, hit in scored:
            print(f"{title:<12} {dense:>7.4f} {bm25:>8.3f}  {','.join(hit)}")


if __name__ == "__main__":
    asyncio.run(main())
