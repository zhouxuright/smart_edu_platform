"""重建向量（分块级 + 文档级）。

换 embedding 模型（如哈希兜底 → BGE-M3）或换分词后必须执行一次，
否则旧向量与新查询向量不在同一空间，语义检索会失效。

两张表都要重建：
  - document_chunks.vector   分块级检索用（主链路）
  - document_embeddings.vector  文档级回退链路用（无分块的历史数据）

用法（在 backend 目录）：
    python scripts/reindex_embeddings.py                 # 两张表都重建
    python scripts/reindex_embeddings.py --dry-run       # 只看统计
    python scripts/reindex_embeddings.py --target chunks # 只重建分块
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from edu_app.core.config import settings
from edu_app.models.learning import Document, DocumentChunk, DocumentEmbedding
from edu_app.services.embedding import embed_texts, embedding_status

BATCH_SIZE = 16


async def _rebuild_table(session, model, text_getter, label: str) -> int:
    """通用重建：按批向量化并回写。返回成功条数。"""
    rows = (await session.execute(select(model).order_by(model.id))).scalars().all()
    if not rows:
        print(f"[{label}] 没有数据，跳过。")
        return 0

    old_dims = sorted({len(r.vector or []) for r in rows})
    print(f"[{label}] 待处理 {len(rows)} 条，现有维度 {old_dims}")

    # 先把正文快照成纯字符串，避免后续循环里再碰 ORM 属性
    payload = [(row, text_getter(row) or "") for row in rows]

    done = 0
    for start in range(0, len(payload), BATCH_SIZE):
        batch = payload[start:start + BATCH_SIZE]
        try:
            vectors = await embed_texts([text for _, text in batch])
        except Exception as exc:  # 单批失败不该让整次重建前功尽弃
            print(f"[{label}] 第 {start}~{start + len(batch)} 条向量化失败，已跳过：{exc}")
            continue

        for (row, _), vector in zip(batch, vectors):
            row.vector = vector
        await session.commit()
        done += len(batch)
        print(f"[{label}] 已处理 {done}/{len(payload)}")

    if payload:
        print(f"[{label}] 完成，新维度 {len(payload[0][0].vector or [])} 维，成功 {done}/{len(payload)}")
    return done


async def rebuild(dry_run: bool = False, target: str = "all") -> int:
    status = await asyncio.to_thread(embedding_status)
    print(f"向量后端：{status['provider']} / {status['model']} / {status['dim']} 维")

    engine = create_async_engine(settings.DATABASE_URL)
    total = 0
    try:
        # expire_on_commit=False 很关键：每批 commit 之后如果实例被过期，
        # 下一批再读正文会触发同步惰性加载，异步上下文里直接抛 MissingGreenlet。
        async with AsyncSession(engine, expire_on_commit=False) as session:
            if target in ("all", "chunks"):
                if dry_run:
                    n = len((await session.execute(select(DocumentChunk.id))).scalars().all())
                    print(f"[分块] 待处理 {n} 条（dry-run 不写入，目标 {status['dim']} 维）")
                else:
                    total += await _rebuild_table(
                        session, DocumentChunk, lambda r: r.content, "分块"
                    )

            if target in ("all", "documents"):
                if dry_run:
                    n = len((await session.execute(select(DocumentEmbedding.id))).scalars().all())
                    print(f"[文档] 待处理 {n} 条（dry-run 不写入，目标 {status['dim']} 维）")
                else:
                    # 文档级向量取自 Document.content，需要 join 出正文
                    docs = {
                        d.id: d.content
                        for d in (await session.execute(select(Document))).scalars().all()
                    }
                    total += await _rebuild_table(
                        session,
                        DocumentEmbedding,
                        lambda r: docs.get(r.document_id, ""),
                        "文档",
                    )
    finally:
        await engine.dispose()

    print(f"全部完成，共更新 {total} 条。")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="重建向量（分块级 + 文档级）")
    parser.add_argument("--dry-run", action="store_true", help="只看统计，不写库")
    parser.add_argument(
        "--target", choices=["all", "chunks", "documents"], default="all",
        help="重建范围，默认 all",
    )
    args = parser.parse_args()
    asyncio.run(rebuild(args.dry_run, args.target))
