import mimetypes
import os
# Fix for OpenMP duplicate runtime error (OMP: Error #15)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from edu_app.api import endpoints
from edu_app.core.database import engine, Base, AsyncSessionLocal
from edu_app.core.indexes import ensure_indexes, ensure_columns
from sqlalchemy import text
from edu_app.models import user, learning, saas, plan, job, notification, resume, agent, memory, workflow  # noqa: F401 导入模型以完成注册
from edu_app.core.tenant_middleware import TenantContextMiddleware
from edu_app.core.rate_limit import RateLimitMiddleware
from edu_app.api import webrtc_agent, internship_plans, rag_documents, ui_events, applications, notifications, resumes, agent_runs
# agent_chat 依赖 endpoints / applications，必须放在它们之后导入
from edu_app.api import agent_chat
# 导入即完成流程注册（jd_match 等），不需要在别处显式调用
from edu_app import flows  # noqa: F401
try:
    from prometheus_fastapi_instrumentator import Instrumentator
except Exception:
    Instrumentator = None
import logging
from datetime import datetime, UTC
from jose import jwt
from edu_app.core.config import settings
import asyncio
from sqlalchemy.future import select

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all 只建表：不给已有表补索引，也不给已有表补列，所以单独补一遍（幂等）。
        # 顺序必须是先列后索引——新索引可能引用新列，反过来会报 column does not exist。
        await ensure_columns(conn)
        await ensure_indexes(conn)
    task = None

    # 预热 BGE-M3：首次加载约 10-20 秒，放到后台线程，避免第一个上传请求干等
    async def warmup_embedding():
        try:
            from edu_app.services.embedding import embedding_status

            status = await asyncio.to_thread(embedding_status)
            logging.info("Embedding backend ready: %s", status)
        except Exception as error:
            logging.warning("Embedding warmup failed: %s", error)

    warmup_task = asyncio.create_task(warmup_embedding())

    async def faiss_rebuild_loop():
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    # 显式 join 出 Document：访问 row.document 会触发同步惰性加载，异步上下文里直接报错
                    result = await session.execute(
                        select(learning.DocumentEmbedding, learning.Document)
                        .join(learning.Document, learning.Document.id == learning.DocumentEmbedding.document_id)
                        .order_by(learning.DocumentEmbedding.id)
                    )
                    rows = result.all()
                    vectors = []
                    metas = []
                    for emb, doc in rows:
                        vec = emb.vector or []
                        # 换 embedding 模型后旧维度向量不能混进来，否则索引维度对不上
                        if vec and (not vectors or len(vec) == len(vectors[0])):
                            vectors.append(vec)
                            metas.append({
                                "document_id": emb.document_id,
                                "owner_id": doc.owner_id,
                                "tenant_id": doc.tenant_id,
                            })
                    if vectors:
                        import numpy as np
                        import faiss
                        xb = np.array(vectors).astype("float32")
                        faiss.normalize_L2(xb)
                        idx = faiss.IndexFlatIP(len(vectors[0]))
                        idx.add(xb)
                        endpoints.faiss_index = idx
                        endpoints.faiss_ids = metas
                        endpoints.faiss_dim = len(vectors[0])
                        endpoints.save_faiss()
            except Exception:
                pass
            await asyncio.sleep(3600)
    task = asyncio.create_task(faiss_rebuild_loop())

    # 计划提醒的实际投递：没有这个循环，「第二天提醒」只是数据库里的一个开关
    from edu_app.services.reminder_scheduler import reminder_loop

    reminder_stop = asyncio.Event()
    reminder_task = asyncio.create_task(reminder_loop(reminder_stop))

    # 会话归档：闲置 30 分钟或满 20 轮，生成摘要 + 长期记忆。
    # 放在进程内循环而不是 broker：每 10 分钟扫一次，量级远达不到需要队列
    from edu_app.services.memory import archive_loop

    memory_stop = asyncio.Event()
    memory_task = asyncio.create_task(archive_loop(memory_stop))

    # 过期清扫：作废超时的流程与提案。没有这一步，expires_at 和提案 TTL
    # 只是写在字段里的装饰——作废的流程会一直躺在列表里，超时提案
    # 一直显示「待确认」，用户点下去才报错
    from edu_app.services.workflow import sweep_loop

    sweep_stop = asyncio.Event()
    sweep_task = asyncio.create_task(sweep_loop(sweep_stop))

    try:
        yield
    finally:
        reminder_stop.set()
        memory_stop.set()
        sweep_stop.set()
        for t in (task, reminder_task, memory_task, sweep_task):
            try:
                t.cancel()
            except Exception:
                pass

app = FastAPI(title="AI Agent教育个性化学习系统", lifespan=lifespan)
if Instrumentator:
    Instrumentator().instrument(app).expose(app)

# 跨域配置（CORS）
# 线上把 CORS_ORIGINS 设成前端域名（逗号分隔）。allow_origins=["*"] 配
# allow_credentials=True 时浏览器会拒收通配符，所以只要不是 "*"，就按白名单精确匹配。
origins = settings.cors_origin_list
allow_all = origins == ["*"]

app.add_middleware(TenantContextMiddleware)
# 限流放在 CORS 内层：429 也要带上 CORS 头，否则前端只看到 "CORS error"，
# 看不到真正的「请求过于频繁」。
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=not allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(endpoints.router, prefix="/api")
app.include_router(internship_plans.router, prefix="/api")
app.include_router(rag_documents.router, prefix="/api")
app.include_router(webrtc_agent.router, prefix="/api")
app.include_router(ui_events.router, prefix="/api")
app.include_router(applications.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(resumes.router, prefix="/api")
app.include_router(agent_runs.router, prefix="/api")
app.include_router(agent_chat.router, prefix="/api")

# Mount static directory for uploads
UPLOAD_DIR = str(settings.data_root / "uploads")
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=UPLOAD_DIR), name="static")

logging.basicConfig(level=logging.INFO)

# 只审计写操作。读请求占实际流量九成以上，而前端还会轮询额度、通知这类接口，
# 给它们每条都 insert 一行 audit_events 等于把数据库当日志文件用：
# 一次页面加载几十个请求就是几十次写库，并发上来先被审计拖垮。
# 静态资源和 SPA 页面请求完全不记，交给平台自己的访问日志。
_AUDIT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@app.middleware("http")
async def audit_logger(request: Request, call_next):
    start = datetime.now(UTC).timestamp()
    response = await call_next(request)
    duration = datetime.now(UTC).timestamp() - start

    path = request.url.path
    if not path.startswith("/api"):
        return response

    logging.info(f"method={request.method} path={path} status={response.status_code} duration={duration:.3f}s")

    if request.method not in _AUDIT_METHODS:
        return response

    try:
        auth_header = request.headers.get("Authorization", "")
        email = None
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                email = payload.get("sub")
            except Exception:
                email = None
        async with AsyncSessionLocal() as session:
            db_user = None
            if email:
                from sqlalchemy.future import select
                result = await session.execute(select(user.User).where(user.User.email == email))
                db_user = result.scalars().first()
            ae = learning.AuditEvent(
                user_id=getattr(db_user, "id", None),
                tenant_id=getattr(db_user, "tenant_id", None),
                method=request.method,
                path=path,
                status_code=response.status_code,
                duration_ms=duration * 1000.0,
            )
            session.add(ae)
            await session.commit()
    except Exception:
        pass
    return response


# ---------------------------------------------------------------------------
# 前端静态目录（单服务模式）
# ---------------------------------------------------------------------------
_raw_dist = os.getenv("FRONTEND_DIST", "").strip()
FRONTEND_DIST = Path(_raw_dist).expanduser() if _raw_dist else None
SPA_READY = bool(FRONTEND_DIST and FRONTEND_DIST.is_dir())
if SPA_READY:
    logging.info("前端静态目录已启用: %s", FRONTEND_DIST)


def _spa_file(path: Path) -> FileResponse:
    """按文件类型给缓存头。

    Vite 产物名里带内容哈希，可以长缓存；index.html 和 sw.js 绝不能缓存，
    否则用户会一直拿到旧壳子，Service Worker 也更新不了。
    """
    name = path.name
    if name == "sw.js":
        headers = {"Cache-Control": "no-cache, must-revalidate"}
    elif name in ("index.html", "manifest.webmanifest"):
        headers = {"Cache-Control": "no-cache"}
    else:
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}

    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if name.endswith(".webmanifest"):
        media = "application/manifest+json"
    return FileResponse(path, media_type=media, headers=headers)


@app.get("/api/health", include_in_schema=False)
async def health():
    """平台健康检查。

    为什么要真的查一次库：如果只返回 200，数据库挂了它照样是绿的，
    这个接口就等于没有。这里跑一条 SELECT 1，3 秒超时；
    库不通时返回 503，让平台把实例判定为不健康。

    故意不放在 /api 的鉴权路由里：健康检查不带 token。
    """
    database_ok = False
    detail = "ok"
    try:
        async with AsyncSessionLocal() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=3)
        database_ok = True
    except Exception as error:  # noqa: BLE001 - 健康检查要吞掉所有异常并如实报告
        detail = f"{type(error).__name__}: {error}"[:200]

    payload = {
        "status": "ok" if database_ok else "degraded",
        "database": database_ok,
        "detail": detail,
    }
    return JSONResponse(payload, status_code=200 if database_ok else 503)


@app.get("/")
async def root():
    if SPA_READY:
        return _spa_file(FRONTEND_DIST / "index.html")
    return {"message": "Welcome to the AI Agent Education Platform API"}


# ---------------------------------------------------------------------------
# 单服务模式：同一个容器同时提供 API 和前端静态文件
#
# 只在 FRONTEND_DIST 指向一个真实存在的目录时启用（镜像里设成 /app/frontend_dist）。
# 本地开发不设这个变量，行为与前后端分离时完全一致。
#
# 为什么不直接 app.mount("/", StaticFiles(html=True))：那样 SPA 的深层链接
# （/dashboard、/resume/3）会 404 —— 磁盘上没有对应文件，StaticFiles 不会回退到
# index.html。前端用的是 history 路由，必须自己兜底。
# ---------------------------------------------------------------------------
if SPA_READY:
    if (FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="spa-assets")

    # 这些前缀是真实接口，没匹配到就该 404，不能回退成 index.html ——
    # 否则前端拿到一段 HTML 去 JSON.parse，报错信息会完全指错方向
    _API_PREFIXES = ("api", "static", "docs", "redoc", "openapi.json", "metrics")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        head = full_path.split("/", 1)[0]
        if head in _API_PREFIXES:
            raise HTTPException(status_code=404, detail="Not Found")

        target = (FRONTEND_DIST / full_path).resolve()
        root_dir = FRONTEND_DIST.resolve()
        # 防路径穿越：解析后必须仍在 dist 目录内
        if target.is_file() and target.is_relative_to(root_dir):
            return _spa_file(target)
        return _spa_file(root_dir / "index.html")

