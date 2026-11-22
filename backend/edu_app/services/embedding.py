"""向量化：唯一入口，支持 BGE-M3 本地推理与 OpenAI 兼容接口。

优先级由 EMBEDDING_PROVIDER 决定：
- local：sentence-transformers 加载 BGE-M3（默认，离线可用，1024 维）
- openai：走 /embeddings 接口（需 base 支持，DeepSeek 不支持）
- auto：本地模型可用就用本地，否则退回哈希词袋

哈希词袋只保证链路不断，不具备语义能力，生产环境必须用 local 或 openai。
"""
import asyncio
import logging
import threading

import numpy as np

from edu_app.core.config import settings

logger = logging.getLogger(__name__)

_local_model = None
_local_failed = False
_model_lock = threading.Lock()


def simple_embed(text: str, dim: int = 1536) -> list[float]:
    """哈希词袋向量：无模型时的兜底，不代表语义相似度。

    用与 BM25 相同的分词，保证中文至少能反映词汇重叠。
    注意维度是 1536，与 BGE-M3 的 1024 不兼容，切换后必须重建向量。
    """
    from edu_app.services.text_utils import tokenize

    arr = np.zeros(dim, dtype="float32")
    for token in tokenize(text):
        arr[hash(token) % dim] += 1.0
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return [float(x) for x in arr]


def _load_local_model():
    """加载 BGE-M3（首次约 10-20 秒，之后常驻内存）。"""
    global _local_model, _local_failed
    if _local_model is not None:
        return _local_model
    if _local_failed:
        return None
    with _model_lock:
        if _local_model is not None:
            return _local_model
        if _local_failed:
            return None
        try:
            import os

            # 模型已在本地缓存时强制离线，避免 huggingface.co 不可达导致长时间卡住
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model %s on %s", settings.EMBEDDING_MODEL, settings.EMBEDDING_DEVICE)
            model = SentenceTransformer(settings.EMBEDDING_MODEL, device=settings.EMBEDDING_DEVICE)
            model.max_seq_length = int(getattr(settings, "EMBEDDING_MAX_LENGTH", 512) or 512)
            _local_model = model
            logger.info("Embedding model loaded, dim=%s", model.get_sentence_embedding_dimension())
            return _local_model
        except Exception as error:
            _local_failed = True
            logger.warning("Local embedding model unavailable, falling back: %s", error)
            return None


def local_model_ready() -> bool:
    return _local_model is not None


def resolve_provider() -> str:
    """实际生效的 provider：local / openai / fallback。"""
    provider = (getattr(settings, "EMBEDDING_PROVIDER", None) or "auto").lower()
    if provider == "local":
        return "local" if _load_local_model() is not None else "fallback"
    if provider == "openai":
        base = (getattr(settings, "LLM_API_BASE", None) or "").strip()
        key = (getattr(settings, "LLM_API_KEY", None) or "").strip()
        if base and key and "deepseek.com" not in base.lower():
            return "openai"
        return "fallback"
    # auto
    if _load_local_model() is not None:
        return "local"
    base = (getattr(settings, "LLM_API_BASE", None) or "").strip()
    key = (getattr(settings, "LLM_API_KEY", None) or "").strip()
    if base and key and "deepseek.com" not in base.lower():
        return "openai"
    return "fallback"


def _encode_local(texts: list[str]) -> list[list[float]]:
    model = _load_local_model()
    if model is None:
        return [simple_embed(t) for t in texts]
    vectors = model.encode(
        texts,
        batch_size=int(getattr(settings, "EMBEDDING_BATCH_SIZE", 8) or 8),
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [v.tolist() for v in vectors]


async def _encode_openai(texts: list[str]) -> list[list[float]]:
    from langchain_openai import OpenAIEmbeddings

    embeddings = OpenAIEmbeddings(
        openai_api_key=settings.LLM_API_KEY,
        openai_api_base=settings.LLM_API_BASE,
    )
    return await embeddings.aembed_documents(texts)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。本地推理放到线程池，避免阻塞事件循环。"""
    if not texts:
        return []
    snippets = [(t or "")[:8000] for t in texts]
    provider = resolve_provider()
    if provider == "local":
        try:
            return await asyncio.to_thread(_encode_local, snippets)
        except Exception as error:
            logger.warning("Local embedding failed, fallback to simple_embed: %s", error)
            return [simple_embed(s) for s in snippets]
    if provider == "openai":
        try:
            return await _encode_openai(snippets)
        except Exception as error:
            logger.warning("Remote embedding failed, fallback to simple_embed: %s", error)
            return [simple_embed(s) for s in snippets]
    return [simple_embed(s) for s in snippets]


async def embed_text(text: str) -> list[float]:
    vectors = await embed_texts([text])
    return vectors[0] if vectors else simple_embed("")


def embedding_status() -> dict:
    """给前端/运维看的当前向量化状态。"""
    provider = resolve_provider()
    dim = None
    if provider == "local" and _local_model is not None:
        dim = _local_model.get_sentence_embedding_dimension()
    elif provider == "openai":
        dim = 1536
    elif provider == "fallback":
        dim = 1536
    return {
        "provider": provider,
        "model": settings.EMBEDDING_MODEL if provider == "local" else ("remote" if provider == "openai" else "hash-fallback"),
        "dim": dim,
        "semantic": provider in ("local", "openai"),
    }
