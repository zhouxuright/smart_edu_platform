import os
# Fix for OpenMP duplicate runtime error (OMP: Error #15)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from edu_app.api import endpoints
from edu_app.core.database import engine, Base, AsyncSessionLocal
from sqlalchemy import text
from edu_app.models import user, learning, saas, plan, job, notification, resume, agent  # 导入模型以完成注册
from edu_app.core.tenant_middleware import TenantContextMiddleware
from edu_app.api import webrtc_agent, internship_plans, rag_documents, ui_events, applications, notifications, resumes
# agent_chat 依赖 endpoints / applications，必须放在它们之后导入
from edu_app.api import agent_chat
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
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id VARCHAR"))
            await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS tenant_id VARCHAR"))
            await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS owner_id INTEGER"))
        except Exception:
            pass
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

    try:
        yield
    finally:
        reminder_stop.set()
        for t in (task, reminder_task):
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
app.include_router(agent_chat.router, prefix="/api")

# Mount static directory for uploads
UPLOAD_DIR = str(settings.data_root / "uploads")
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=UPLOAD_DIR), name="static")

logging.basicConfig(level=logging.INFO)

@app.middleware("http")
async def audit_logger(request: Request, call_next):
    start = datetime.now(UTC).timestamp()
    response = await call_next(request)
    duration = datetime.now(UTC).timestamp() - start
    logging.info(f"method={request.method} path={request.url.path} status={response.status_code} duration={duration:.3f}s")
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
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration * 1000.0,
            )
            session.add(ae)
            await session.commit()
    except Exception:
        pass
    return response


@app.get("/")
async def root():
    return {"message": "Welcome to the AI Agent Education Platform API"}
