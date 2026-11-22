"""知识库文档管理：批量上传、批量解析、列表与删除。

一次请求里每个文件独立成败，互不影响；返回逐文件结果，便于前端展示进度。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_app.api.endpoints import get_current_user
from edu_app.core.database import get_db
from edu_app.models.learning import Document, DocumentChunk, DocumentEmbedding
from edu_app.models.user import User
from edu_app.services.document_pipeline import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    ParseError,
    SUPPORTED_EXTS,
    chunk_text,
    extract_text,
)
from edu_app.services.embedding import embed_texts, embedding_status

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FILES = 20
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024


class TextItem(BaseModel):
    title: str
    content: str
    url: Optional[str] = None


class BatchTextRequest(BaseModel):
    items: list[TextItem]


async def _save_document(
    db: AsyncSession, owner_id: int, tenant_id: Optional[str], title: str, text: str, url: Optional[str]
) -> dict:
    """落库一篇文档：正文 + 分块 + 向量，单篇一个事务。

    只接收快照出来的标量（owner_id / tenant_id），不接收 ORM 实例：
    批量过程中任何一次 rollback 都会让 current_user 过期，之后再访问它的属性
    会触发同步 IO 并抛 MissingGreenlet。
    """
    chunks = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
    if not chunks:
        raise ParseError("解析后没有可入库的内容")

    document = Document(
        title=title,
        content=text,
        source_url=url,
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    db.add(document)
    await db.flush()

    vectors = await embed_texts([c["content"] for c in chunks])
    for chunk, vector in zip(chunks, vectors):
        db.add(
            DocumentChunk(
                document_id=document.id,
                seq=chunk["seq"],
                content=chunk["content"],
                char_start=chunk["char_start"],
                char_end=chunk["char_end"],
                vector=vector,
            )
        )

    # 兼容旧的文档级索引（/rag/index/rebuild 仍然依赖它）
    doc_vector = (await embed_texts([text[:5000]]))[0]
    db.add(DocumentEmbedding(document_id=document.id, vector=doc_vector))

    await db.commit()
    await db.refresh(document)
    return {"document_id": document.id, "chunks": len(chunks), "chars": len(text)}


async def _find_same_title(db: AsyncSession, owner_id: int, title: str) -> Optional[Document]:
    result = await db.execute(
        select(Document).where(Document.owner_id == owner_id, Document.title == title).limit(1)
    )
    return result.scalars().first()


def _clean_title(raw: str, fallback: str) -> str:
    title = (raw or "").strip() or fallback
    return title[:200]


@router.post("/rag/documents/batch")
async def upload_documents_batch(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量上传并解析：一次最多 20 个文件，单文件 20MB，总计 60MB。"""
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {MAX_FILES} 个文件，当前 {len(files)} 个")

    owner_id = current_user.id
    tenant_id = getattr(current_user, "tenant_id", None)
    results, total_bytes = [], 0
    for upload in files:
        filename = upload.filename or "未命名文件"
        try:
            data = await upload.read()
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES:
                results.append({"filename": filename, "status": "failed", "error": "本批文件总大小超过 60MB"})
                break
            if len(data) > MAX_FILE_BYTES:
                results.append({"filename": filename, "status": "failed", "error": "单文件超过 20MB"})
                continue
            if not data:
                results.append({"filename": filename, "status": "failed", "error": "文件是空的（0 字节）"})
                continue

            ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
            if ext and ext not in SUPPORTED_EXTS:
                results.append({
                    "filename": filename,
                    "status": "failed",
                    "error": f"不支持的格式 {ext}，支持 pdf/docx/txt/md/csv/json",
                })
                continue

            title = _clean_title(filename.rsplit(".", 1)[0], filename)
            existing = await _find_same_title(db, owner_id, title)
            if existing:
                results.append({
                    "filename": filename,
                    "status": "duplicated",
                    "document_id": existing.id,
                    "error": "已有同名文档，已跳过",
                })
                continue

            parsed = extract_text(filename, data)
            saved = await _save_document(db, owner_id, tenant_id, title, parsed["text"], None)
            results.append({
                "filename": filename,
                "status": "ok",
                "pages": parsed["pages"],
                "format": parsed["format"],
                "warnings": parsed["warnings"],
                **saved,
            })
        except ParseError as error:
            await db.rollback()
            results.append({"filename": filename, "status": "failed", "error": str(error)})
        except Exception as error:  # 单个文件失败不影响其他文件
            await db.rollback()
            logger.exception("Batch ingest failed for %s", filename)
            results.append({"filename": filename, "status": "failed", "error": f"入库失败：{type(error).__name__}"})
        finally:
            await upload.close()

    succeeded = sum(1 for r in results if r["status"] == "ok")
    return {
        "total": len(results),
        "succeeded": succeeded,
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "duplicated": sum(1 for r in results if r["status"] == "duplicated"),
        "results": results,
    }


@router.post("/rag/documents/batch-text")
async def ingest_documents_batch_text(
    payload: BatchTextRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量粘贴文本入库：一次最多 20 篇，每篇独立成败。"""
    items = payload.items or []
    if not items:
        raise HTTPException(status_code=400, detail="没有收到内容")
    if len(items) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多 {MAX_FILES} 篇，当前 {len(items)} 篇")

    owner_id = current_user.id
    tenant_id = getattr(current_user, "tenant_id", None)
    results = []
    for index, item in enumerate(items, 1):
        title = _clean_title(item.title, f"未命名文档 {index}")
        try:
            text = (item.content or "").strip()
            if not text:
                results.append({"title": title, "status": "failed", "error": "内容为空"})
                continue
            existing = await _find_same_title(db, owner_id, title)
            if existing:
                results.append({"title": title, "status": "duplicated", "document_id": existing.id, "error": "已有同名文档，已跳过"})
                continue
            saved = await _save_document(db, owner_id, tenant_id, title, text, item.url)
            results.append({"title": title, "status": "ok", **saved})
        except ParseError as error:
            await db.rollback()
            results.append({"title": title, "status": "failed", "error": str(error)})
        except Exception as error:
            await db.rollback()
            logger.exception("Batch text ingest failed for %s", title)
            results.append({"title": title, "status": "failed", "error": f"入库失败：{type(error).__name__}"})

    return {
        "total": len(results),
        "succeeded": sum(1 for r in results if r["status"] == "ok"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "duplicated": sum(1 for r in results if r["status"] == "duplicated"),
        "results": results,
    }


@router.get("/rag/documents")
async def list_documents(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户可见的文档，带分块数。"""
    limit = max(1, min(int(limit or 50), 200))
    is_admin = getattr(current_user, "role", "student") == "admin"
    query = select(Document).order_by(Document.created_at.desc()).limit(limit)
    if not is_admin:
        query = query.where(Document.owner_id == current_user.id)
    documents = (await db.execute(query)).scalars().all()

    counts = {}
    if documents:
        rows = await db.execute(
            select(DocumentChunk.document_id, func.count(DocumentChunk.id))
            .where(DocumentChunk.document_id.in_([d.id for d in documents]))
            .group_by(DocumentChunk.document_id)
        )
        counts = {doc_id: count for doc_id, count in rows.all()}

    return {
        "total": len(documents),
        "documents": [
            {
                "id": d.id,
                "title": d.title,
                "chars": len(d.content or ""),
                "chunks": counts.get(d.id, 0),
                "url": d.source_url,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in documents
        ],
    }


@router.get("/rag/embedding/status")
async def embedding_status_endpoint(current_user: User = Depends(get_current_user)):
    """当前向量化后端状态，用于确认是否真的在用 BGE-M3。"""
    return embedding_status()


@router.post("/rag/documents/reindex-all")
async def reindex_all_documents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重建当前用户全部文档的分块向量。

    换 embedding 模型、改分词口径后必须跑一次，否则老向量与新查询向量不在同一空间。
    """
    is_admin = getattr(current_user, "role", "student") == "admin"
    doc_query = select(Document.id)
    if not is_admin:
        doc_query = doc_query.where(Document.owner_id == current_user.id)
    document_ids = [row[0] for row in (await db.execute(doc_query)).all()]
    if not document_ids:
        return {"documents": 0, "chunks": 0}

    chunks = (
        await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id.in_(document_ids))
            .order_by(DocumentChunk.document_id, DocumentChunk.seq)
        )
    ).scalars().all()
    if not chunks:
        return {"documents": len(document_ids), "chunks": 0}

    vectors = await embed_texts([c.content or "" for c in chunks])
    for chunk, vector in zip(chunks, vectors):
        chunk.vector = vector
    await db.commit()
    return {"documents": len(document_ids), "chunks": len(chunks)}


@router.post("/rag/documents/{document_id}/reindex")
async def reindex_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重算一篇文档所有分块的向量（换 embedding 模型或修过分词后使用）。"""
    document = (await db.execute(select(Document).where(Document.id == document_id))).scalars().first()
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    if document.owner_id != current_user.id and getattr(current_user, "role", "student") != "admin":
        raise HTTPException(status_code=404, detail="文档不存在")

    chunks = (
        await db.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == document_id).order_by(DocumentChunk.seq)
        )
    ).scalars().all()
    if not chunks:
        raise HTTPException(status_code=400, detail="这篇文档没有分块，请删除后重新上传")

    vectors = await embed_texts([c.content or "" for c in chunks])
    for chunk, vector in zip(chunks, vectors):
        chunk.vector = vector
    await db.commit()
    return {"document_id": document_id, "chunks": len(chunks)}


@router.delete("/rag/documents/{document_id}")
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除文档及其分块与向量；非本人（且非管理员）返回 404。"""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalars().first()
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    if document.owner_id != current_user.id and getattr(current_user, "role", "student") != "admin":
        raise HTTPException(status_code=404, detail="文档不存在")

    await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    await db.execute(delete(DocumentEmbedding).where(DocumentEmbedding.document_id == document_id))
    await db.execute(delete(Document).where(Document.id == document_id))
    await db.commit()
    return {"deleted": document_id}
