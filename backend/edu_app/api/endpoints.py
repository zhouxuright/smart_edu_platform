from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, UTC
from typing import List, Optional, Any
from jose import JWTError, jwt

from edu_app.core.database import get_db, AsyncSessionLocal
from edu_app.core.config import settings
from edu_app.models.user import User
from edu_app.models.learning import StudentProfile, LearningRecord, KnowledgeNode, Document, DocumentEmbedding, DocumentChunk, MistakeRecord
from edu_app.models import saas
from edu_app.schemas import schemas
from edu_app.services import auth, ebbinghaus
from edu_app.agents import mentor, knowledge, assessment, collaborative, parent, mistake, rag_tools
from edu_app.agents.graph_rag import GraphRAGSearcher
from edu_app.core.model_router import model_router
from edu_app.services.advanced_rag_pipeline import advanced_rag_pipeline
from edu_app.services.embedding import embed_text, embedding_status
from edu_app.services import memory_events
from edu_app.services import quota as quota_service
from edu_app.services import llm_usage
from edu_app.services.text_utils import tokenize
from edu_app.services.llm_text import sanitize_markdown, strip_inline_markers, MarkdownStreamFilter
from edu_app.services.llm_gate import llm_client
import numpy as np
import faiss
import json
import os
import base64
import openpyxl
import csv
# Fix for OpenMP duplicate runtime error (OMP: Error #15)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from io import BytesIO
from pypdf import PdfReader
import httpx
import math
import logging
import re
import uuid
import asyncio

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

# 索引和元数据都落在 DATA_DIR 下：容器里只有挂载卷的目录才活得下去
FAISS_INDEX_PATH = str(settings.data_root / "faiss.index")
FAISS_META_PATH = str(settings.data_root / "faiss_meta.json")
faiss_index = None
faiss_ids = []
faiss_dim = None
DASHBOARD_REC_CACHE = {}
# 正在后台生成推荐的用户，防止同一用户并发触发多次模型调用
DASHBOARD_REC_INFLIGHT: set[int] = set()
# 必须持有后台任务的强引用，否则任务可能在执行途中被 GC 回收
DASHBOARD_REC_TASKS: set = set()
DASHBOARD_REC_TTL = 3600

# 可选的向量检索后端
pinecone = None
milvus_available = False
try:
    import pinecone as pinecone_client
    pinecone = pinecone_client
except Exception:
    pinecone = None
try:
    from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection
    milvus_available = True
except Exception:
    milvus_available = False

pinecone_index = None
milvus_collection = None

def new_trace_id():
    return uuid.uuid4().hex

def init_pinecone():
    global pinecone_index, faiss_dim
    if pinecone_index is not None:
        return True
    if settings.VECTOR_BACKEND and settings.VECTOR_BACKEND.lower() == "pinecone" and pinecone and settings.PINECONE_API_KEY and settings.PINECONE_INDEX:
        try:
            pinecone.init(api_key=settings.PINECONE_API_KEY, environment=getattr(settings, "PINECONE_ENV", None))
            existing = [i.name for i in pinecone.list_indexes()]
            dim = faiss_dim or 1536
            if settings.PINECONE_INDEX not in existing:
                pinecone.create_index(settings.PINECONE_INDEX, dimension=dim, metric="cosine")
            pinecone_index = pinecone.Index(settings.PINECONE_INDEX)
            return True
        except Exception:
            pinecone_index = None
            return False
    return False

def pinecone_upsert(vec, doc_id, metadata):
    if init_pinecone() and pinecone_index:
        try:
            pinecone_index.upsert(vectors=[{"id": str(doc_id), "values": vec, "metadata": metadata}])
            return True
        except Exception:
            return False
    return False

def pinecone_query(qvec, top_k):
    if init_pinecone() and pinecone_index:
        try:
            res = pinecone_index.query(vector=qvec, top_k=top_k, include_metadata=True)
            matches = []
            for m in res.matches:
                matches.append((float(m.score), int(m.id), m.metadata or {}))
            return matches
        except Exception:
            return []
    return []

def init_milvus():
    global milvus_collection
    if milvus_collection is not None:
        return True
    if settings.VECTOR_BACKEND and settings.VECTOR_BACKEND.lower() == "milvus" and milvus_available and settings.MILVUS_HOST and settings.MILVUS_PORT and settings.MILVUS_COLLECTION:
        try:
            connections.connect(alias="default", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
            name = settings.MILVUS_COLLECTION
            try:
                milvus_collection = Collection(name=name)
            except Exception:
                id_field = FieldSchema(name="doc_id", dtype=DataType.INT64, is_primary=True, auto_id=False)
                owner_field = FieldSchema(name="owner_id", dtype=DataType.INT64)
                tenant_field = FieldSchema(name="tenant_id", dtype=DataType.INT64)
                dim = faiss_dim or 1536
                vec_field = FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim)
                schema = CollectionSchema(fields=[id_field, owner_field, tenant_field, vec_field])
                milvus_collection = Collection(name=name, schema=schema)
            try:
                milvus_collection.create_index(field_name="vector", index_params={"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 1024}})
            except Exception:
                pass
            try:
                milvus_collection.load()
            except Exception:
                pass
            return True
        except Exception:
            milvus_collection = None
            return False
    return False

def milvus_upsert(vec, doc_id, metadata):
    try:
        name = settings.MILVUS_COLLECTION
        connections.connect(alias="default", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
        if milvus_collection is None:
            try:
                c = Collection(name=name)
            except Exception:
                id_field = FieldSchema(name="doc_id", dtype=DataType.INT64, is_primary=True, auto_id=False)
                owner_field = FieldSchema(name="owner_id", dtype=DataType.INT64)
                tenant_field = FieldSchema(name="tenant_id", dtype=DataType.INT64)
                vec_field = FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=len(vec))
                schema = CollectionSchema(fields=[id_field, owner_field, tenant_field, vec_field])
                c = Collection(name=name, schema=schema)
            try:
                c.create_index(field_name="vector", index_params={"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 1024}})
            except Exception:
                pass
            try:
                c.load()
            except Exception:
                pass
            globals()["milvus_collection"] = c
        dim_ok = True
        try:
            schema = milvus_collection.schema
            for f in schema.fields:
                if getattr(f, "name", "") == "vector":
                    dim_ok = (getattr(f, "params", {}).get("dim") or getattr(f, "dim", None)) == len(vec)
                    break
        except Exception:
            dim_ok = True
        if not dim_ok:
            return False
        data = [[int(doc_id)], [int(metadata.get("owner_id") or 0)], [int(metadata.get("tenant_id") or 0)], [vec]]
        milvus_collection.insert(data)
        milvus_collection.flush()
        return True
    except Exception:
        return False

def milvus_query(qvec, top_k):
    if init_milvus() and milvus_collection is not None:
        try:
            expr = None
            try:
                milvus_collection.load()
            except Exception:
                pass
            res = milvus_collection.search(
                data=[qvec],
                anns_field="vector",
                param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                limit=top_k,
                expr=expr,
                output_fields=["owner_id", "tenant_id"]
            )
            matches = []
            for hit in res[0]:
                owner = 0
                tenant = 0
                try:
                    ent = getattr(hit, "entity", None)
                    if ent is not None:
                        try:
                            owner = int(ent.get("owner_id"))
                        except Exception:
                            owner = 0
                        try:
                            tenant = int(ent.get("tenant_id"))
                        except Exception:
                            tenant = 0
                except Exception:
                    owner = 0
                    tenant = 0
                meta = {"owner_id": owner, "tenant_id": tenant}
                matches.append((float(hit.distance), int(hit.id), meta))
            return matches
        except Exception:
            return []
    return []

def save_faiss():
    global faiss_index, faiss_ids
    if faiss_index is not None:
        faiss.write_index(faiss_index, FAISS_INDEX_PATH)
        try:
            with open(FAISS_META_PATH, "w", encoding="utf-8") as f:
                json.dump({"ids": faiss_ids}, f)
        except Exception:
            pass

def load_faiss():
    global faiss_index, faiss_ids, faiss_dim
    if os.path.exists(FAISS_INDEX_PATH):
        try:
            faiss_index = faiss.read_index(FAISS_INDEX_PATH)
            if os.path.exists(FAISS_META_PATH):
                with open(FAISS_META_PATH, "r", encoding="utf-8") as f:
                    faiss_ids = json.load(f).get("ids", [])
            faiss_dim = faiss_index.d
        except Exception:
            faiss_index = None
            faiss_ids = []
            faiss_dim = None

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user


async def require_llm_quota(current_user: User = Depends(get_current_user)):
    """挂在会触发大模型推理的路由上：检查并扣减当日额度，超限返回 429。

    检索类接口不挂——它们走本地 BGE-M3，不产生 API 成本。

    顺带把当前用户写进 contextvar：token 计量发生在闸门层（httpx hook /
    LangChain callback），那里拿不到请求上下文，只能靠这个标记归属。
    管理员虽然不计次数配额，但照样要计 token——成本核算不分角色。
    """
    llm_usage.set_current_user(current_user.id)
    await quota_service.consume_quota(current_user)
    return True

# 学情访问范围校验搬到 services/scope.py 了：家长绑定上线后它要查库，
# 而新加的 api/parent_links.py 也要用它，留在本文件会形成循环 import。
from edu_app.services.scope import ensure_student_scope  # noqa: E402

async def _student_display_name(db: AsyncSession, student_id: int, current_user: User) -> str:
    """报告里该写谁的名字。

    原来四处都是 `student_name = current_user.full_name`，只有「学生没有档案」
    时才去查目标用户。结果家长看孩子的报告，标题写的是**家长自己的名字**——
    因为学生一建档案，就走不到那个分支了。取名字和有没有档案无关，直接查人。
    """
    if student_id == current_user.id:
        return current_user.full_name or current_user.email
    target = (await db.execute(select(User).where(User.id == student_id))).scalars().first()
    return (target.full_name or target.email) if target else f"学生#{student_id}"


# 认证与用户
@router.post("/register", response_model=schemas.UserResponse)
async def register(user: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == user.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="邮箱已被注册")
    
    hashed_password = await auth.get_password_hash_async(user.password)
    # 公开注册只能选 student / parent。
    # 家长必须是能自助注册的，否则家长门户永远只有演示账号能用；
    # 但 admin / teacher 属于机构角色，必须由后台开通，不能让注册接口自封。
    requested_role = (getattr(user, "role", None) or "student").strip().lower()
    if requested_role not in ("student", "parent"):
        raise HTTPException(status_code=400, detail="角色只能是 student 或 parent")
    new_user = User(
        email=user.email,
        hashed_password=hashed_password,
        full_name=user.full_name,
        role=requested_role,
        tenant_id=None,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@router.get("/llm/quota")
async def llm_quota_status(current_user: User = Depends(get_current_user)):
    """当前用户的 AI 额度用量，前端用来在调用前提示剩余次数。"""
    return await quota_service.quota_status(current_user)


@router.post("/login")
async def login(user: schemas.UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == user.email))
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(status_code=401, detail="凭证无效")
    # argon2 校验走线程池，别在事件循环里算（单次 48ms，会卡住整个进程）
    if not await auth.verify_password_async(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="凭证无效")
    
    access_token = auth.create_access_token(data={"sub": db_user.email})
    return {"access_token": access_token, "token_type": "bearer", "role": db_user.role, "user_id": db_user.id}

# 智能体接口
@router.post("/agent/mentor")
async def mentor_agent(request: schemas.MentorRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user), _quota: bool = Depends(require_llm_quota)):
    msg = (request.message or "").strip()
    if not msg or len(msg) > 2000:
        raise HTTPException(status_code=400, detail="消息不合法")
    history = request.history if hasattr(request, "history") and request.history else []
    if len(history) > 20:
        history = history[-20:]
    # 获取真实用户画像
    result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == current_user.id))
    profile = result.scalars().first()
    
    if not profile:
        # 若不存在则创建默认画像
        profile = StudentProfile(
            user_id=current_user.id,
            grade="大三", # 默认（面向大学生生涯规划）
            interests="未填写",
            learning_style="混合型"
        )
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
        
    profile_data = {
        "grade": profile.grade,
        "interests": profile.interests,
        "learning_style": profile.learning_style,
        "name": current_user.full_name,
        "context": ""
    }
    
    # RAG Retrieval for standard POST endpoint
    try:
        # Step 1: Analyze Intent and Rewrite Query
        rag_analysis = await rag_tools.analyze_query(msg, history)
        
        if rag_analysis["needs_rag"]:
            search_query = rag_analysis["search_query"] or msg
            logging.info(f"RAG Router: NEED RAG. Query: {search_query}")
            
            rag_payload = schemas.RAGSearch(query=search_query, top_k=3)
            rag_res = await rag_search(rag_payload, db, current_user)
            if rag_res and "results" in rag_res and rag_res["results"]:
                rag_context = "参考资料：\n"
                for doc in rag_res["results"]:
                    rag_context += f"[{doc['title']}] {doc['content'][:200]}...\n"
                profile_data["context"] = rag_context
        else:
            logging.info(f"RAG Router: NO RAG. Reason: {rag_analysis.get('reason')}")
            
    except Exception as e:
        logging.warning(f"Mentor Agent POST RAG failed: {e}")

    logging.info(f"mentor.local user={current_user.id} msg_len={len(msg)} hist_len={len(history)}")
    lower_msg = msg.lower()
    if ("self" in lower_msg and "attention" in lower_msg) and (("dk" in lower_msg) or ("根号" in msg) or ("√" in msg) or ("sqrt" in lower_msg)):
        quick = (
            "目标：解释 Transformer 中为何使用除以 √d_k 的缩放。\n"
            "思考：点积注意力的 q·k 随维度 d_k 增大会增大方差，未缩放时 softmax 的指数会过大，导致分布过于尖锐、梯度很小，训练不稳定。\n"
            "行动步骤：\n"
            "1）设 k_i ~ N(0,1)，q_i ~ N(0,1)，则 q·k 的方差约为 d_k。\n"
            "2）用 q·k / √d_k 将方差归一化到约 1，使 softmax 的输入稳定在合理范围。\n"
            "3）对比未缩放与缩放的梯度：缩放后梯度更稳定、收敛更快。\n"
            "结果/下一步：理解缩放的数值稳定性作用；可进一步推导方差与梯度关系并在实验中观察训练曲线。\n"
            "结论：除以 √d_k 是对 q·k 的标准化，避免随维度增大导致 softmax 饱和与训练不稳定。"
        )
        # 这是写死的示例答案，不是模型生成。不标 source 的话前端会显示成「AI 导师」，
        # 等于把模板冒充成回答。
        return {"response": quick, "source": "fallback"}
    try:
        response = await mentor.get_mentor_response(profile_data, msg, history)
    except Exception:
        response = ""
    if not response or not str(response).strip():
        base = (
            "目标：为你的问题提供清晰解释并给出学习行动。\n"
            "思考：当前智能体未返回内容，先基于通用学习方法给出指导。\n"
            "行动步骤：\n"
            "1）明确问题关键术语，列出已知与待证。\n"
            "2）查阅教材/论文与权威文章，做要点摘记。\n"
            "3）用例子演算与可视化（如推导、画图）检验理解。\n"
            "4）整理为笔记并形成 3 条可执行练习题。\n"
            "结果/下一步：完成以上步骤后，将不明点汇总再向导师提问。"
        )
        lower = msg.lower()
        if "self" in lower and "attention" in lower:
            base += (
                "\n补充提示：Scaled Dot-Product Attention 中除以 \u221As(d_k) 是为稳定数值范围，避免点积随维度增大导致 softmax 梯度过小，从而影响训练收敛。"
            )
        response = base
    src = "fallback" if ("本地回退" in response or "未返回内容" in response) else "llm"
    return {"response": sanitize_markdown(response), "source": src}

@router.post("/agent/mentor/stream")
async def mentor_agent_stream(request: schemas.MentorRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user), _quota: bool = Depends(require_llm_quota)):
    msg = (request.message or "").strip()
    if not msg or len(msg) > 2000:
        raise HTTPException(status_code=400, detail="消息不合法")
    history = request.history if hasattr(request, "history") and request.history else []
    if len(history) > 20:
        history = history[-20:]
    is_career = request.mode == "career"
    profile_data = {"grade": "未填写", "interests": "未填写", "learning_style": "未填写"}
    if not is_career:
        result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == current_user.id))
        profile = result.scalars().first()
        if not profile:
            profile = StudentProfile(user_id=current_user.id, grade="大三", interests="未填写", learning_style="混合型")
            db.add(profile)
            await db.commit()
            await db.refresh(profile)
        profile_data = {"grade": profile.grade, "interests": profile.interests, "learning_style": profile.learning_style}

    # Career mode does not send the question to the learning knowledge-base router.
    rag_context = ""
    try:
        # Step 1: Analyze Intent and Rewrite Query
        rag_analysis = await rag_tools.analyze_query(msg, history) if not is_career else {"needs_rag": False, "reason": "career mode"}

        if rag_analysis["needs_rag"]:
            search_query = rag_analysis["search_query"] or msg
            logging.info("Mentor requested knowledge retrieval")
            rag_payload = schemas.RAGSearch(query=search_query, top_k=3)
            rag_res = await rag_search(rag_payload, db, current_user)
            if rag_res and "results" in rag_res and rag_res["results"]:
                rag_context = "参考资料：\n"
                for doc in rag_res["results"]:
                    rag_context += f"[{doc['title']}] {doc['content'][:200]}...\n"
        else:
            logging.info(f"RAG Router: NO RAG. Reason: {rag_analysis.get('reason')}")
    except Exception as e:
        logging.warning(f"Mentor Agent RAG failed: {e}")
        rag_context = ""

    system_prompt = """你是一个学生的AI学习导师。
    学生档案：
    - 年级：{grade}
    - 兴趣：{interests}
    - 学习风格：{learning_style}
    
    {context}
    
    你的目标是指导学生，根据他们的学习风格解释概念，并鼓励他们。
    你必须具备思考、行动、推理与决策能力，按照「目标→思考→行动步骤→结果/下一步」结构输出。
    若问题属于知识问答（如“为什么/原理/推导/对比/作用”），先用1-3句话给出直接结论，再给出结构化说明与关键公式或例子。
    若信息不足，请先声明合理假设并继续完成任务。
    请务必使用简体中文回复。
    """
    sys_msg = system_prompt.format(
        grade=profile_data["grade"],
        interests=profile_data["interests"],
        learning_style=profile_data["learning_style"],
        context=rag_context
    )

    if is_career:
        sys_msg = (
            "你是面向大学生的生涯规划导师，帮助用户比较考研、实习和求职方向。"
            "以用户明确提供的背景为准，不猜年级和经历。先回应问题并指出关键取舍，再给两到三个可执行步骤，"
            "最后指出今天能做的一个动作；必要时追问一项会影响建议的信息。"
            "不要编造招生、招聘、截止时间或录取概率；时效信息提醒查询官方来源。"
            "不承诺录取或就业，不替用户做决定，只输出面向用户的简体中文建议。"
        )

    async def event_generator():
        base = (settings.LLM_API_BASE or "").strip()
        key = (settings.LLM_API_KEY or "").strip()
        model_name = (getattr(settings, "LLM_MODEL", None) or ("deepseek-chat" if "deepseek.com" in base else "gpt-4o"))
        used_llm = bool(base and key and not getattr(settings, "LLM_DISABLE", False) and not base.startswith("http://localhost") and key.lower() != "dummy")
        lower_msg = msg.lower()
        if not is_career and ("self" in lower_msg and "attention" in lower_msg) and (("dk" in lower_msg) or ("根号" in msg) or ("√" in msg) or ("sqrt" in lower_msg)):
            text = (
                "结论：除以 √d_k 是为了将 q·k 的方差归一到约 1，稳定 softmax 的数值与梯度。\n"
                "目标：解释 Transformer 的缩放点积注意力为什么使用 √d_k。\n"
                "思考：q·k 的方差随维度 d_k 增大，未缩放会使 softmax 过于尖锐，梯度过小，训练不稳定。\n"
                "行动步骤：\n1）推导 q·k 方差≈d_k；\n2）使用 q·k/√d_k 归一化；\n3）对比未缩放与缩放的训练曲线与梯度。\n"
                "结果/下一步：掌握缩放的数值稳定性作用，进一步结合实验验证。"
            )
            for chunk in text.split("\n"):
                yield f"data: {json.dumps({'content': chunk + '\n', 'source': 'fallback'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        if not used_llm:
            if is_career:
                fb = (
                    "真实模型未启用，下面是本地示例，并非针对你的个性化回答。\n"
                    "1. 写下当下要做的选择，以及最迟什么时候需要决定。\n"
                    "2. 列出每个选项的时间成本、已有准备和还缺的信息。\n"
                    "3. 今天完成一个小验证，例如对照三份实习岗位要求，或核对目标院校的招生简章。"
                )
            else:
                fb = (
                    "目标：为你的问题提供清晰解释并给出学习行动。\n"
                    "思考：当前使用本地回退生成，优先保证响应与可执行性。\n"
                    "行动步骤：\n1）明确关键术语与已知/待证；\n2）查阅资料并做要点摘记；\n3）用例子演算或可视化检验理解。\n"
                    "结果/下一步：形成3条练习题并复盘。"
                )
            for chunk in fb.splitlines(keepends=True):
                yield f"data: {json.dumps({'content': chunk, 'source': 'fallback'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model_name,
            "stream": True,
            **llm_usage.stream_options(),
            "temperature": 0.5,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": sys_msg},
                *[{"role": m["role"], "content": m["content"]} for m in history if m.get("role") in ("user", "assistant")],
                {"role": "user", "content": msg[:800] + ("..." if len(msg) > 800 else "")}
            ]
        }
        timeout = httpx.Timeout(20.0)
        # 扣住行尾未写完的 Markdown 标记，避免前端闪出半截 `**`
        markdown = MarkdownStreamFilter()

        def frame(text: str) -> str:
            return f"data: {json.dumps({'content': text}, ensure_ascii=False)}\n\n"

        try:
            async with llm_client(timeout, base) as client:
                async with client.stream("POST", "/chat/completions", headers=headers, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = llm_usage.parse_chunk(data)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("error"):
                            raise RuntimeError("Model stream reported an error")
                        choice = (obj.get("choices") or [{}])[0]
                        delta = (choice.get("delta") or {}).get("content") or ""
                        if delta:
                            ready = markdown.feed(delta)
                            if ready:
                                yield frame(ready)
        except Exception as error:
            logging.warning("Mentor stream failed: %s", type(error).__name__)
            yield 'data: {"error":"mentor_unavailable"}\n\n'
            return
        tail = markdown.flush()
        if tail:
            yield frame(tail)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


@router.websocket("/ws/agent/mentor")
async def mentor_agent_websocket(websocket: WebSocket):
    await websocket.accept()
    auth_header = websocket.headers.get("authorization") or websocket.headers.get("Authorization") or ""
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    
    if not token:
        token = websocket.query_params.get("token")
        
    if not token:
        await websocket.close(code=4401)
        return
    try:
        async with AsyncSessionLocal() as db:
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                email = payload.get("sub")
                if not email:
                    await websocket.close(code=4401)
                    return
            except JWTError:
                await websocket.close(code=4401)
                return
            result = await db.execute(select(User).where(User.email == email))
            current_user = result.scalars().first()
            if current_user is None:
                await websocket.close(code=4401)
                return
            result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == current_user.id))
            profile = result.scalars().first()
            if not profile:
                profile = StudentProfile(
                    user_id=current_user.id,
                    grade="大三",
                    interests="未填写",
                    learning_style="混合型"
                )
                db.add(profile)
                await db.commit()
                await db.refresh(profile)
            profile_data = {
                "grade": profile.grade,
                "interests": profile.interests,
                "learning_style": profile.learning_style,
                "name": current_user.full_name
            }
            while True:
                try:
                    text = await websocket.receive_text()
                except WebSocketDisconnect:
                    break
                try:
                    data = json.loads(text)
                except Exception:
                    await websocket.send_text(json.dumps({"error": "invalid_payload"}))
                    continue
                msg = (data.get("message") or "").strip()
                if not msg or len(msg) > 2000:
                    await websocket.send_text(json.dumps({"error": "invalid_message"}))
                    continue
                history = data.get("history") or []
                if len(history) > 20:
                    history = history[-20:]
                
                # RAG Retrieval
                rag_context = ""
                try:
                    # Step 1: Analyze Intent and Rewrite Query
                    rag_analysis = await rag_tools.analyze_query(msg, history)
                    
                    if rag_analysis["needs_rag"]:
                        search_query = rag_analysis["search_query"] or msg
                        logging.info(f"RAG Router: NEED RAG. Query: {search_query}")
                        
                        rag_payload = schemas.RAGSearch(query=search_query, top_k=3)
                        rag_res = await rag_search(rag_payload, db, current_user)
                        if rag_res and "results" in rag_res and rag_res["results"]:
                            rag_context = "参考资料：\n"
                            for doc in rag_res["results"]:
                                rag_context += f"[{doc['title']}] {doc['content'][:200]}...\n"
                    else:
                        logging.info(f"RAG Router: NO RAG. Reason: {rag_analysis.get('reason')}")
                except Exception as e:
                    logging.warning(f"Mentor Agent WS RAG failed: {e}")

                system_prompt = """你是一个学生的AI学习导师。
学生档案：
- 年级：{grade}
- 兴趣：{interests}
- 学习风格：{learning_style}

{context}

你的目标是指导学生，根据他们的学习风格解释概念，并鼓励他们。
你必须具备思考、行动、推理与决策能力，按照「目标→思考→行动步骤→结果/下一步」结构输出。
若问题属于知识问答（如“为什么/原理/推导/对比/作用”），先用1-3句话给出直接结论，再给出结构化说明与关键公式或例子。
若信息不足，请先声明合理假设并继续完成任务。
请务必使用简体中文回复。
"""
                sys_msg = system_prompt.format(
                    grade=profile_data["grade"],
                    interests=profile_data["interests"],
                    learning_style=profile_data["learning_style"],
                    context=rag_context
                )
                base = (settings.LLM_API_BASE or "").strip()
                key = (settings.LLM_API_KEY or "").strip()
                model_name = (getattr(settings, "LLM_MODEL", None) or ("deepseek-chat" if "deepseek.com" in base else "gpt-4o"))
                used_llm = bool(base and key and not getattr(settings, "LLM_DISABLE", False) and not base.startswith("http://localhost") and key.lower() != "dummy")
                lower_msg = msg.lower()
                if ("self" in lower_msg and "attention" in lower_msg) and (("dk" in lower_msg) or ("根号" in msg) or ("√" in msg) or ("sqrt" in lower_msg)):
                    quick = (
                        "结论：除以 √d_k 是为了将 q·k 的方差归一到约 1，稳定 softmax 的数值与梯度。\n"
                        "目标：解释 Transformer 的缩放点积注意力为什么使用 √d_k。\n"
                        "思考：q·k 的方差随维度 d_k 增大，未缩放会使 softmax 过于尖锐，梯度过小，训练不稳定。\n"
                        "行动步骤：\n1）推导 q·k 方差≈d_k；\n2）使用 q·k/√d_k 归一化；\n3）对比未缩放与缩放的训练曲线与梯度。\n"
                        "结果/下一步：掌握缩放的数值稳定性作用，进一步结合实验验证。"
                    )
                    await websocket.send_text("（本地示例回答，非模型生成）\n")
                    await websocket.send_text(quick)
                    await websocket.send_text("[DONE]")
                    continue
                if not used_llm:
                    fb = (
                        "目标：为你的问题提供清晰解释并给出学习行动。\n"
                        "思考：当前使用本地回退生成，优先保证响应与可执行性。\n"
                        "行动步骤：\n1）明确关键术语与已知/待证；\n2）查阅资料并做要点摘记；\n3）用例子演算或可视化检验理解。\n"
                        "结果/下一步：形成3条练习题并复盘。"
                    )
                    for chunk in fb.split("\n"):
                        await websocket.send_text(chunk)
                    await websocket.send_text("[DONE]")
                    continue
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": model_name,
                    "stream": True,
                    **llm_usage.stream_options(),
                    "temperature": 0.5,
                    "max_tokens": 4096,
                    "messages": [
                        {"role": "system", "content": sys_msg},
                        *[{"role": m["role"], "content": m["content"]} for m in history if m.get("role") in ("user", "assistant")],
                        {"role": "user", "content": msg[:800] + ("..." if len(msg) > 800 else "")}
                    ]
                }
                timeout = httpx.Timeout(20.0)
                try:
                    async with llm_client(timeout, base) as client:
                        async with client.stream("POST", "/chat/completions", headers=headers, json=payload) as resp:
                            async for line in resp.aiter_lines():
                                if not line:
                                    continue
                                if line.startswith("data:"):
                                    data_str = line[5:].strip()
                                else:
                                    data_str = line.strip()
                                if data_str == "[DONE]":
                                    await websocket.send_text("[DONE]")
                                    break
                                try:
                                    obj = llm_usage.parse_chunk(data_str)
                                    choice = (obj.get("choices") or [{}])[0]
                                    delta = (choice.get("delta") or {}).get("content") or ""
                                    reasoning = (choice.get("delta") or {}).get("reasoning_content") or ""
                                    if delta:
                                        await websocket.send_text(delta)
                                    elif reasoning:
                                        await websocket.send_text(reasoning)
                                except Exception:
                                    await websocket.send_text(data_str)
                except asyncio.TimeoutError:
                    await websocket.send_text("请求超时，请稍后重试。")
                    await websocket.send_text("[DONE]")
                except Exception as e:
                    await websocket.send_text(f"服务错误：{str(e)}")
                    await websocket.send_text("[DONE]")
    except Exception:
        await websocket.close(code=1011)


@router.websocket("/ws/agent/assessment")
async def assessment_agent_websocket(websocket: WebSocket):
    await websocket.accept()
    auth_header = websocket.headers.get("authorization") or websocket.headers.get("Authorization") or ""
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    
    if not token:
        token = websocket.query_params.get("token")
        
    if not token:
        await websocket.close(code=4401)
        return
    try:
        async with AsyncSessionLocal() as db:
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                email = payload.get("sub")
                if not email:
                    await websocket.close(code=4401)
                    return
            except JWTError:
                await websocket.close(code=4401)
                return
            result = await db.execute(select(User).where(User.email == email))
            current_user = result.scalars().first()
            if current_user is None:
                await websocket.close(code=4401)
                return

            while True:
                try:
                    text = await websocket.receive_text()
                except WebSocketDisconnect:
                    break
                try:
                    data = json.loads(text)
                except Exception:
                    await websocket.send_text(json.dumps({"error": "invalid_payload"}))
                    continue
                
                # Default to current user if student_id not provided
                try:
                    student_id = await ensure_student_scope(db, current_user, data.get("student_id") or current_user.id)
                except HTTPException as scope_error:
                    await websocket.send_text(json.dumps({"error": "forbidden", "detail": scope_error.detail}))
                    await websocket.send_text("[DONE]")
                    continue
                
                student_name = await _student_display_name(db, student_id, current_user)
                
                # Fetch learning records
                result = await db.execute(select(LearningRecord).where(LearningRecord.user_id == student_id).order_by(LearningRecord.timestamp.desc()).limit(10))
                records = result.scalars().all()

                try:
                    async for chunk in assessment.analyze_performance_stream(student_name, records):
                        await websocket.send_text(chunk)
                    await websocket.send_text("[DONE]")
                except Exception as e:
                    await websocket.send_text(f"服务错误：{str(e)}")
                    await websocket.send_text("[DONE]")
                    
    except Exception:
        await websocket.close(code=1011)


@router.websocket("/ws/agent/parent")
async def parent_agent_websocket(websocket: WebSocket):
    await websocket.accept()
    auth_header = websocket.headers.get("authorization") or websocket.headers.get("Authorization") or ""
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    
    if not token:
        token = websocket.query_params.get("token")
        
    if not token:
        await websocket.close(code=4401)
        return
    try:
        async with AsyncSessionLocal() as db:
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                email = payload.get("sub")
                if not email:
                    await websocket.close(code=4401)
                    return
            except JWTError:
                await websocket.close(code=4401)
                return
            result = await db.execute(select(User).where(User.email == email))
            current_user = result.scalars().first()
            if current_user is None:
                await websocket.close(code=4401)
                return

            while True:
                try:
                    text = await websocket.receive_text()
                except WebSocketDisconnect:
                    break
                try:
                    data = json.loads(text)
                except Exception:
                    await websocket.send_text(json.dumps({"error": "invalid_payload"}))
                    continue
                
                try:
                    student_id = await ensure_student_scope(db, current_user, data.get("student_id") or current_user.id)
                except HTTPException as scope_error:
                    await websocket.send_text(json.dumps({"error": "forbidden", "detail": scope_error.detail}))
                    await websocket.send_text("[DONE]")
                    continue

                student_name = await _student_display_name(db, student_id, current_user)
                
                result = await db.execute(select(LearningRecord).where(LearningRecord.user_id == student_id).order_by(LearningRecord.timestamp.desc()).limit(10))
                records = result.scalars().all()

                try:
                    async for chunk in parent.generate_parent_report_stream(student_name, records, "暂无反馈"):
                        await websocket.send_text(chunk)
                    await websocket.send_text("[DONE]")
                except Exception as e:
                    await websocket.send_text(f"服务错误：{str(e)}")
                    await websocket.send_text("[DONE]")

    except Exception:
        await websocket.close(code=1011)


@router.websocket("/ws/agent/collaborative")
async def collaborative_agent_websocket(websocket: WebSocket):
    await websocket.accept()
    auth_header = websocket.headers.get("authorization") or websocket.headers.get("Authorization") or ""
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    
    if not token:
        token = websocket.query_params.get("token")
        
    if not token:
        await websocket.close(code=4401)
        return
    try:
        # Collaborative agent might not need DB for now, but keeping structure consistent
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email = payload.get("sub")
        if not email:
            await websocket.close(code=4401)
            return

        while True:
            try:
                text = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                data = json.loads(text)
            except Exception:
                await websocket.send_text(json.dumps({"error": "invalid_payload"}))
                continue
            
            topic = (data.get("topic") or "").strip()
            if not topic:
                await websocket.send_text("请输入讨论主题")
                await websocket.send_text("[DONE]")
                continue
                
            history = data.get("history") or []
            if len(history) > 20:
                history = history[-20:]
                
            system_prompt = """你是一个协作学习Agent。
你的角色是促进学生之间的小组讨论。
你应该：鼓励参与、提出与主题相关的问题、综合不同观点并形成清晰结论、产出可执行行动项（包含负责人与截止时间）。
输出结构为「思考→行动建议→角色分配→决定与下一步」。
请务必使用简体中文回复。
"""
            hist_text = "\n".join([f"{m.get('user','成员')}: {m.get('content','')}" for m in history])
            
            base = (settings.LLM_API_BASE or "").strip()
            key = (settings.LLM_API_KEY or "").strip()
            model_name = (getattr(settings, "LLM_MODEL", None) or ("deepseek-chat" if "deepseek.com" in base else "gpt-4o"))
            used_llm = bool(base and key and not getattr(settings, "LLM_DISABLE", False) and not base.startswith("http://localhost") and key.lower() != "dummy")
            
            if not used_llm:
                fb = (
                    "思考：围绕主题组织一次有效讨论。\n"
                    "行动建议：\n1）每人提出一个关键问题并给出观点；\n2）总结共识与分歧，形成3条行动项及负责人；\n3）设定下次跟进时间与目标。\n"
                    "角色分配：自选负责人完成行动项。\n"
                    "决定与下一步：按上述执行并在下次会议复盘。"
                )
                for chunk in fb.split("\n"):
                    await websocket.send_text(chunk)
                await websocket.send_text("[DONE]")
                continue

            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {
                "model": model_name,
                "stream": True,
                **llm_usage.stream_options(),
                "temperature": 0.8,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Topic: {topic}\nCurrent Discussion:\n{hist_text}\nWhat is your contribution as the facilitator?"}
                ],
                "max_tokens": 4096
            }
            timeout = httpx.Timeout(20.0)
            try:
                async with llm_client(timeout, base) as client:
                    async with client.stream("POST", "/chat/completions", headers=headers, json=payload) as resp:
                        async for line in resp.aiter_lines():
                            if not line: continue
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    await websocket.send_text("[DONE]")
                                    break
                                try:
                                    obj = llm_usage.parse_chunk(data_str)
                                    choice = (obj.get("choices") or [{}])[0]
                                    delta = (choice.get("delta") or {}).get("content") or ""
                                    reasoning = (choice.get("delta") or {}).get("reasoning_content") or ""
                                    if delta:
                                        await websocket.send_text(delta)
                                    elif reasoning:
                                        await websocket.send_text(reasoning)
                                except Exception:
                                    pass
            except asyncio.TimeoutError:
                await websocket.send_text("请求超时，请稍后重试。")
                await websocket.send_text("[DONE]")
            except Exception as e:
                await websocket.send_text(f"服务错误：{str(e)}")
                await websocket.send_text("[DONE]")

    except Exception:
        await websocket.close(code=1011)

@router.post("/agent/assessment/stream")
async def assessment_agent_stream(request: schemas.AssessmentRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await ensure_student_scope(db, current_user, request.student_id)
    result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == request.student_id))
    profile = result.scalars().first()
    student_name = await _student_display_name(db, request.student_id, current_user)

    # 获取学习记录
    result = await db.execute(select(LearningRecord).where(LearningRecord.user_id == request.student_id).order_by(LearningRecord.timestamp.desc()).limit(10))
    records = result.scalars().all()

    async def event_generator():
        async for chunk in assessment.analyze_performance_stream(student_name, records):
             yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.post("/agent/collaborative/stream")
async def collaborative_agent_stream(request: schemas.CollaborativeRequest, current_user: User = Depends(get_current_user), _quota: bool = Depends(require_llm_quota)):
    topic = (request.topic or "").strip()
    if not topic or len(topic) > 512:
        raise HTTPException(status_code=400, detail="主题不合法")
    history = request.history if hasattr(request, "history") and request.history else []
    if len(history) > 20:
        history = history[-20:]
    system_prompt = """你是一个协作学习Agent。
    你的角色是促进学生之间的小组讨论。
    你应该：鼓励参与、提出与主题相关的问题、综合不同观点并形成清晰结论、产出可执行行动项（包含负责人与截止时间）。
    输出结构为「思考→行动建议→角色分配→决定与下一步」。
    请务必使用简体中文回复。
    """
    hist_text = "\n".join([f"{m.get('user','成员')}: {m.get('content','')}" for m in history])
    async def event_generator():
        base = (settings.LLM_API_BASE or "").strip()
        key = (settings.LLM_API_KEY or "").strip()
        model_name = (getattr(settings, "LLM_MODEL", None) or ("deepseek-chat" if "deepseek.com" in base else "gpt-4o"))
        used_llm = bool(base and key and not getattr(settings, "LLM_DISABLE", False) and not base.startswith("http://localhost") and key.lower() != "dummy")
        if not used_llm:
            fb = (
                "思考：围绕主题组织一次有效讨论。\n"
                "行动建议：\n1）每人提出一个关键问题并给出观点；\n2）总结共识与分歧，形成3条行动项及负责人；\n3）设定下次跟进时间与目标。\n"
                "角色分配：自选负责人完成行动项。\n"
                "决定与下一步：按上述执行并在下次会议复盘。"
            )
            for chunk in fb.split("\n"):
                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "stream": True,
            **llm_usage.stream_options(),
            "temperature": 0.8,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Topic: {topic}\nCurrent Discussion:\n{hist_text}\nWhat is your contribution as the facilitator?"}
            ],
            "max_tokens": 4096
        }
        timeout = httpx.Timeout(20.0)
        try:
            async with llm_client(timeout, base) as client:
                async with client.stream("POST", "/chat/completions", headers=headers, json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                yield "data: [DONE]\n\n"
                                break
                            try:
                                obj = llm_usage.parse_chunk(data)
                                choice = (obj.get("choices") or [{}])[0]
                                delta = (choice.get("delta") or {}).get("content") or ""
                                reasoning = (choice.get("delta") or {}).get("reasoning_content") or ""
                                if delta:
                                    yield f"data: {json.dumps({'content': delta}, ensure_ascii=False)}\n\n"
                                elif reasoning:
                                    yield f"data: {json.dumps({'content': reasoning}, ensure_ascii=False)}\n\n"
                            except Exception:
                                yield f"data: {json.dumps({'content': data}, ensure_ascii=False)}\n\n"
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'content': '请求超时，请稍后重试。'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'content': f'服务错误：{str(e)}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

@router.post("/agent/parent/stream")
async def parent_agent_stream(request: schemas.ParentReportRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    # 家长门户只对家长/老师开放：学生传自己的 id 也不该拿到一份「家长报告」
    await ensure_student_scope(db, current_user, request.student_id, require_roles=("parent", "teacher"))
    result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == request.student_id))
    profile = result.scalars().first()
    student_name = await _student_display_name(db, request.student_id, current_user)
    
    # 获取学习记录
    result = await db.execute(select(LearningRecord).where(LearningRecord.user_id == request.student_id).order_by(LearningRecord.timestamp.desc()).limit(10))
    records = result.scalars().all()
    
    async def event_generator():
        async for chunk in parent.generate_parent_report_stream(student_name, records, "暂无反馈"):
             yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.post("/agent/knowledge/stream")
async def knowledge_agent_stream(request: schemas.KnowledgeRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user), _quota: bool = Depends(require_llm_quota)):
    q = (request.topic or "")
    result = await db.execute(select(KnowledgeNode).where(KnowledgeNode.subject == request.subject))
    nodes = result.scalars().all()
    relevant_nodes = []
    if request.topic:
        for node in nodes:
            if request.topic in node.name or request.topic in node.description:
                relevant_nodes.append(node)
            elif node.related_nodes and any(request.topic in r for r in node.related_nodes):
                relevant_nodes.append(node)
    if not relevant_nodes:
        relevant_nodes = nodes
    async def event_generator():
        yield f"data: {json.dumps({'type': 'status', 'content': '正在构建基础图谱...'}, ensure_ascii=False)}\n\n"
        id_map = {}
        det_nodes = []
        det_edges = []
        idx = 1
        for node in relevant_nodes:
            nid = str(idx)
            id_map[node.name] = nid
            det_nodes.append({"id": nid, "label": node.name, "description": node.description or ""})
            yield f"data: {json.dumps({'type':'node','node':det_nodes[-1]}, ensure_ascii=False)}\n\n"
            idx += 1
        for node in relevant_nodes:
            src_id = id_map.get(node.name)
            pres = getattr(node, "prerequisites", None) or []
            rels = getattr(node, "related_nodes", None) or []
            for pre in pres:
                pre_id = id_map.get(pre)
                if pre_id:
                    e = {"source": pre_id, "target": src_id, "relationship": "前置知识"}
                    det_edges.append(e)
                    yield f"data: {json.dumps({'type':'edge','edge':e}, ensure_ascii=False)}\n\n"
            for rel in rels:
                rel_id = id_map.get(rel)
                if rel_id:
                    e = {"source": src_id, "target": rel_id, "relationship": "相关知识"}
                    det_edges.append(e)
                    yield f"data: {json.dumps({'type':'edge','edge':e}, ensure_ascii=False)}\n\n"
        base = (settings.LLM_API_BASE or "").strip()
        key = (settings.LLM_API_KEY or "").strip()
        model_name = (getattr(settings, "LLM_MODEL", None) or ("deepseek-chat" if "deepseek.com" in base else "gpt-4o"))
        used_llm = bool(base and key and not getattr(settings, "LLM_DISABLE", False) and not base.startswith("http://localhost") and key.lower() != "dummy")
        if used_llm:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            sys_prompt = """你是一个知识图谱Agent。
            你的任务是为给定的学科和主题生成一个结构化的知识图谱。
            输出必须是一个包含 'nodes' 和 'edges' 的有效 JSON 对象，节点含 id、label、description；边含 source、target、relationship。
            请务必使用简体中文回复内容。
            """
            ctx = [{"name": n.name, "description": n.description, "prerequisites": n.prerequisites, "related_nodes": n.related_nodes} for n in relevant_nodes]
            u_content = f"Subject: {request.subject}\nTopic: {request.topic}\nContext:\n{json.dumps(ctx, ensure_ascii=False)}"
            payload = {"model": model_name, "stream": True, **llm_usage.stream_options(), "temperature": 0.3, "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": u_content}], "max_tokens": 4096}
            timeout = httpx.Timeout(25.0)
            try:
                async with llm_client(timeout, base) as client:
                    async with client.stream("POST", "/chat/completions", headers=headers, json=payload) as resp:
                        acc = ""
                        async for line in resp.aiter_lines():
                            if not line:
                                continue
                            if line.startswith("data:"):
                                data = line[5:].strip()
                                if data == "[DONE]":
                                    break
                            try:
                                obj = llm_usage.parse_chunk(data)
                                choice = (obj.get("choices") or [{}])[0]
                                delta = (choice.get("delta") or {}).get("content") or ""
                                reasoning = (choice.get("delta") or {}).get("reasoning_content") or ""
                                if delta:
                                    acc += delta
                                if reasoning:
                                    # 对于图谱生成任务，我们忽略推理过程，只关注最终的JSON结果
                                    pass
                            except Exception:
                                acc += data
                        content = acc.strip()
                        if "```json" in content:
                            content = content.split("```json")[1].split("```")[0]
                        elif "```" in content and "nodes" in content:
                            content = content.split("```")[1].split("```")[0]
                        try:
                            llm_graph = json.loads(content)
                        except Exception:
                            llm_graph = {}
                        add_nodes = llm_graph.get("nodes") or []
                        add_edges = llm_graph.get("edges") or []
                        seen_labels = {n["label"] for n in det_nodes}
                        seen_edges = {(e["source"], e["target"], e["relationship"]) for e in det_edges}
                        for n in add_nodes:
                            if n.get("label") and n["label"] not in seen_labels:
                                yield f"data: {json.dumps({'type':'node','node':n}, ensure_ascii=False)}\n\n"
                                det_nodes.append(n)
                                seen_labels.add(n["label"])
                        for e in add_edges:
                            key_t = (e.get("source"), e.get("target"), e.get("relationship"))
                            if all(key_t) and key_t not in seen_edges:
                                yield f"data: {json.dumps({'type':'edge','edge':e}, ensure_ascii=False)}\n\n"
                                det_edges.append(e)
                                seen_edges.add(key_t)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'content': '生成知识图谱超时，请稍后重试。'}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'content': f'服务错误：{str(e)}'}, ensure_ascii=False)}\n\n"
        
        # 确保在任何情况下都发送一次完整的 graph 事件
        if det_nodes:
             yield f"data: {json.dumps({'type':'graph','nodes':det_nodes,'edges':det_edges}, ensure_ascii=False)}\n\n"
        
        yield "data: [DONE]\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


@router.post("/agent/knowledge")
async def knowledge_agent(request: schemas.KnowledgeRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    # 步骤1：从数据库中查找
    query = select(KnowledgeNode).where(
        KnowledgeNode.subject == request.subject
    )
    result = await db.execute(query)
    nodes = result.scalars().all()
    
    # 过滤相关节点（当前使用简单关键字匹配）
    relevant_nodes = []
    if request.topic:
        for node in nodes:
            if request.topic in node.name or request.topic in node.description:
                relevant_nodes.append(node)
            # Also include if it is in related_nodes
            elif node.related_nodes and any(request.topic in r for r in node.related_nodes):
                relevant_nodes.append(node)
    
    if not relevant_nodes:
        # 回退：包含该学科的全部节点，交由大模型判断或生成
        relevant_nodes = nodes
        
    graph = await knowledge.get_knowledge_graph_data(request.subject, request.topic, relevant_nodes)
    nodes = []
    seen_ids = set()
    for n in graph.get("nodes", []):
        nid = str(n.get("id")) if n.get("id") is not None else ""
        if not nid or nid in seen_ids:
            continue
        label = n.get("label") or ""
        description = n.get("description") or ""
        nodes.append({"id": nid, "label": label, "description": description})
        seen_ids.add(nid)
    node_ids = {n["id"] for n in nodes}
    edges = []
    seen_edges = set()
    for e in graph.get("edges", []):
        s = str(e.get("source")) if e.get("source") is not None else ""
        t = str(e.get("target")) if e.get("target") is not None else ""
        r = e.get("relationship") or ""
        key = (s, t, r)
        if not s or not t:
            continue
        if s not in node_ids or t not in node_ids:
            continue
        if key in seen_edges:
            continue
        edges.append({"source": s, "target": t, "relationship": r})
        seen_edges.add(key)
    return {"nodes": nodes, "edges": edges}

@router.post("/agent/assessment")
async def assessment_agent(request: schemas.AssessmentRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await ensure_student_scope(db, current_user, request.student_id)
    # 获取学习记录
    result = await db.execute(select(LearningRecord).where(LearningRecord.user_id == request.student_id))
    records = result.scalars().all()
    
    # 获取用户姓名
    u_result = await db.execute(select(User).where(User.id == request.student_id))
    user = u_result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=404, detail="未找到用户")

    if not records:
        return {"report": "目前还没有足够的学习记录来为您生成个性化评估报告。\n建议您先：\n1. 在“我的错题本”中上传错题\n2. 在“AI导师”中进行几次问答\n积累一定数据后，我将为您提供更精准的学习建议！"}

    try:
        report = await assessment.analyze_performance(user.full_name, records)
    except Exception as e:
        logging.error(f"Assessment agent failed: {e}")
        # 原来这里拼一段写死的评估当结果返回。它读起来像分析结论，
        # 实际跟这个学生的记录没有因果关系——失败就该如实报失败。
        raise HTTPException(status_code=502, detail=str(e) or "学习评估生成失败，请稍后重试")
    return {"report": report}




@router.post("/agent/collaborative")
async def collaborative_agent(request: schemas.CollaborativeRequest, current_user: User = Depends(get_current_user)):
    topic = (request.topic or "").strip()
    if not topic or len(topic) > 512:
        raise HTTPException(status_code=400, detail="主题不合法")
    history = request.history if hasattr(request, "history") and request.history else []
    if len(history) > 20:
        history = history[-20:]
    logging.info(f"collab.local topic_len={len(topic)} hist_len={len(history)}")
    try:
        response = await collaborative.facilitate_discussion(topic, history)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e) or "讨论引导生成失败，请稍后重试")
    return {"response": response}

@router.post("/agent/parent")
async def parent_agent(request: schemas.ParentReportRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await ensure_student_scope(db, current_user, request.student_id, require_roles=("parent", "teacher"))
    # 与学习评估类似，获取学习记录
    result = await db.execute(select(LearningRecord).where(LearningRecord.user_id == request.student_id))
    records = result.scalars().all()
    
    u_result = await db.execute(select(User).where(User.id == request.student_id))
    user = u_result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    try:
        report = await parent.generate_parent_report(user.full_name, records, "表现很好！")
    except Exception as error:
        # 原来这里兜一段写死的「目标：…近期记录：…」，把失败包装成一份报告。
        # 家长看到的是「分析」，其实是模板——这比报错坏得多，因为他没法分辨。
        raise HTTPException(status_code=502, detail=str(error) or "家长报告生成失败，请稍后重试")
    return {"report": report}


# 仪表盘汇总
def _clean_recommendation_lines(ai_text: str) -> list[str]:
    """把模型输出洗成 3 条干净的推荐行动。"""
    clean_lines = []
    for ln in (ai_text or "").splitlines():
        # 先洗掉 Markdown 标记，再去序号/CoT 标题头，避免 `**目标**：` 这类漏网
        line = strip_inline_markers(ln)
        line = re.sub(r"^[\s\d\.\-、)）]+", "", line).strip()
        # 过滤 "目标:", "思考:", "行动步骤:", "下一步:" 等 CoT 标题头
        line = re.sub(r"^(目标|思考|行动步骤|结果/下一步|下一步)[\s：:]*", "", line).strip()
        if line and len(line) > 5 and line not in ["目标", "思考", "行动步骤", "下一步", "行动建议"]:
            clean_lines.append(line)
    return clean_lines[:3]


# 推荐行动 → 该去哪一页动手。
#
# 为什么要做这层映射：推荐行动是模型生成的自由文本，而「点一下就能去做」
# 需要结构化目标。让模型直接吐路由是不可靠的（它会编出不存在的路径），
# 所以改成确定性关键词映射：命中就带按钮，命中不了就只显示文字。
# 顺序有意义，先匹配到的先赢——「模拟面试」必须排在「面试」类泛词前面。
RECOMMENDATION_ROUTES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    # 「记录一次真实学习活动（例如…模拟面试）」的意图是去记录，不是去面试，
    # 所以这条必须排在「模拟面试」前面，否则会被后面的关键词抢走。
    (("学习活动", "记录一次"), "/dashboard", "记录学习活动"),
    (("模拟面试",), "/mock-interview", "去模拟面试"),
    (("简历", "一页"), "/resume", "去简历工作台"),
    (("JD", "岗位", "招聘"), "/jd-match", "去做 JD 匹配"),
    (("错题", "订正", "专项复习", "专项练习", "复练"), "/mistake-book", "去错题本"),
    (("计划", "7 天", "拆成"), "/plan/internship", "查看我的计划"),
    (("投递", "申请"), "/applications", "去投递看板"),
    (("知识点", "知识覆盖", "复习", "学习频次"), "/knowledge-extraction", "去知识抽取"),
)


def recommendation_action(text: str) -> dict:
    """给一条推荐行动找出该跳转的页面。找不到就返回空 dict（前端只渲染文字）。"""
    for keywords, route, cta in RECOMMENDATION_ROUTES:
        if any(k in (text or "") for k in keywords):
            return {"route": route, "cta": cta}
    return {}


async def _refresh_dashboard_recommendations(user_id: int, full_name: str, record_count: int) -> None:
    """后台生成仪表盘推荐行动，结果写进进程内缓存。

    必须用独立 session：请求的 session 在响应返回后就关了，
    拿它做后台任务会抛 "Session is closed"。
    """
    try:
        async with AsyncSessionLocal() as db:
            profile = (
                await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
            ).scalars().first()
            # 产品面向大学生生涯规划（实习/求职），默认画像不能再按中学学科给建议
            profile_data = {
                "grade": getattr(profile, "grade", "大三"),
                "interests": getattr(profile, "interests", "互联网产品, 数据分析"),
                "learning_style": getattr(profile, "learning_style", "混合型"),
                "name": full_name,
            }
            history = [{"role": "system", "content": f"最近学习记录数: {record_count}"}]
            msg = "基于近期学情，直接给出3条具体可执行的学习行动建议，不要包含标题或思考过程。"
            ai_text = await mentor.get_mentor_response(profile_data, msg, history)
            lines = _clean_recommendation_lines(ai_text)
            if lines:
                DASHBOARD_REC_CACHE[user_id] = (datetime.now(UTC).timestamp(), lines)
    except Exception as error:  # noqa: BLE001 - 后台任务失败不能影响任何人
        logging.warning("仪表盘推荐后台生成失败 user=%s: %s", user_id, error)
    finally:
        DASHBOARD_REC_INFLIGHT.discard(user_id)


def _schedule_dashboard_recommendations(user_id: int, full_name: str, record_count: int) -> None:
    if user_id in DASHBOARD_REC_INFLIGHT:
        return
    DASHBOARD_REC_INFLIGHT.add(user_id)
    task = asyncio.create_task(
        _refresh_dashboard_recommendations(user_id, full_name, record_count)
    )
    DASHBOARD_REC_TASKS.add(task)
    task.add_done_callback(DASHBOARD_REC_TASKS.discard)


@router.get("/dashboard/summary")
async def dashboard_summary(
    student_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """仪表盘概览。

    `student_id` 让家长能看已绑定的孩子。不传就是看自己——
    学生传别人的 id 会被 scope 校验拦掉，所以这个参数不是越权口子。
    """
    target_id = await ensure_student_scope(db, current_user, student_id or current_user.id)
    since = datetime.now(UTC) - timedelta(days=14)
    result = await db.execute(select(LearningRecord).where(LearningRecord.user_id == target_id))
    records = result.scalars().all()

    def norm_ts(ts):
        if not ts:
            return None
        try:
            return ts if getattr(ts, "tzinfo", None) else ts.replace(tzinfo=UTC)
        except Exception:
            return None

    active_courses = len({r.subject for r in records if norm_ts(getattr(r, "timestamp", None)) and norm_ts(getattr(r, "timestamp", None)) >= since}) or len({r.subject for r in records})
    weights = {"quiz": 0.5, "discussion": 0.75, "exam": 1.5}
    study_hours = sum(weights.get(getattr(r, "activity_type", "quiz"), 0.5) for r in records)
    achievements = sum(1 for r in records if getattr(r, "score", None) and r.score >= 90)

    recommendations = []
    try:
        if records:
            scored = [(getattr(r, "score", None), getattr(r, "subject", ""), getattr(r, "topic", "")) for r in records if getattr(r, "score", None) is not None]
            scored.sort(key=lambda x: x[0])
            for s, subj, topic in scored[:3]:
                if topic:
                    recommendations.append(f"针对主题《{topic}》（{subj}）进行专项复习与练习，目标分数≥{min(95, int(s)+10)}")
            counts = {}
            for r in records:
                counts[r.subject] = counts.get(r.subject, 0) + 1
            least = None
            if counts:
                least = sorted(counts.items(), key=lambda x: x[1])[0][0]
            if least and least not in {rec.split('（')[0] for rec in recommendations}:
                recommendations.append(f"增加对{least}的学习频次（至少+2次活动），完善知识覆盖")
            latest = None
            if records:
                latest = sorted(records, key=lambda x: (norm_ts(getattr(x, "timestamp", None)) or datetime.now(UTC)))[-1]
            if latest:
                at = getattr(latest, "activity_type", "quiz")
                next_map = {
                    "quiz": "错题归纳并复练",
                    "discussion": "整理讨论要点并输出笔记",
                    "reading": "做阅读摘记与要点总结",
                    "exam": "试卷复盘与薄弱点专项训练",
                }
                recommendations.append(f"基于最近一次{at}，执行：{next_map.get(at, '复盘与巩固')}，形成可复用笔记")
        else:
            recommendations = [
                "先确定一个想投的实习方向，生成一份 7 天计划，把大目标拆成每天一个动作",
                "把简历整理成一页：最近一段经历写清你负责的部分和可量化结果",
                "记录一次真实学习活动（例如：完成一个岗位 JD 拆解或一次模拟面试）",
            ]
    except Exception:
        pass

    # 推荐行动要调模型，但不能在请求路径里等：冷缓存时用户看到的就是首屏一直转圈。
    # 改成「有旧的就先用旧的，没有就先用上面算出来的规则版兜底」，同时后台生成，
    # 下次打开就是模型版。用户永远不用等模型。
    # 缓存按**被查看的学生**为键：家长和孩子看同一份学情，该命中同一条缓存
    cached_rec = DASHBOARD_REC_CACHE.get(target_id)
    if cached_rec and cached_rec[1]:
        recommendations = cached_rec[1]
        if (datetime.now(UTC).timestamp() - cached_rec[0]) >= DASHBOARD_REC_TTL:
            _schedule_dashboard_recommendations(target_id, await _student_display_name(db, target_id, current_user), len(records))
    else:
        _schedule_dashboard_recommendations(target_id, await _student_display_name(db, target_id, current_user), len(records))

    # Get mistakes count
    m_result = await db.execute(select(MistakeRecord).where(MistakeRecord.user_id == target_id))
    mistakes_count = len(m_result.scalars().all())

    return {
        "name": await _student_display_name(db, target_id, current_user),
        "student_id": target_id,
        "mistakes_count": mistakes_count,
        "active_courses": active_courses,
        "study_hours": round(study_hours, 1),
        "achievements": achievements,
        "recommendations": recommendations,
        # 结构化版本：前端据此渲染「点一下就去对应页面」。
        # 老字段保留成纯字符串——小程序还在读它，改形状会直接把它打挂。
        "recommendation_actions": [
            {"text": text, **recommendation_action(text)} for text in recommendations
        ],
        "weekly_activity": sum(1 for r in records if norm_ts(getattr(r, "timestamp", None)) and norm_ts(getattr(r, "timestamp", None)) >= since),
        "subjects_count": len({r.subject for r in records}),
        "score_trend": [
            {
                "timestamp": (norm_ts(getattr(r, "timestamp", None)).isoformat() if norm_ts(getattr(r, "timestamp", None)) else None),
                "score": getattr(r, "score", None)
            }
            for r in sorted(records, key=lambda x: (norm_ts(getattr(x, "timestamp", None)) or datetime.now(UTC)))[-8:]
            if getattr(r, "score", None) is not None
        ],
    }

@router.post("/demo/seed_records")
async def demo_seed_records(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    now = datetime.now(UTC)
    samples = [
        {"subject": "数学", "topic": "函数概念", "activity_type": "quiz", "score": 85.0, "timestamp": now - timedelta(days=1)},
        {"subject": "物理", "topic": "牛顿运动定律", "activity_type": "discussion", "score": 92.0, "timestamp": now - timedelta(days=2)},
        {"subject": "英语", "topic": "阅读理解", "activity_type": "reading", "score": 78.0, "timestamp": now - timedelta(days=3)},
        {"subject": "数学", "topic": "导数应用", "activity_type": "exam", "score": 88.0, "timestamp": now - timedelta(days=5)},
        {"subject": "物理", "topic": "能量守恒", "activity_type": "quiz", "score": 95.0, "timestamp": now - timedelta(days=7)},
    ]
    for s in samples:
        lr = LearningRecord(
            user_id=current_user.id,
            subject=s["subject"],
            topic=s["topic"],
            activity_type=s["activity_type"],
            score=s["score"],
            timestamp=s["timestamp"],
            details={"notes": "auto-seeded"}
        )
        db.add(lr)
    await db.commit()
    return {"seeded": len(samples)}

@router.post("/agent/orchestrate")
async def agent_orchestrate(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user), _quota: bool = Depends(require_llm_quota)):
    """两个分析并发跑，结果拼成一份文档。

    **这不是多 Agent 编排**，别被名字骗了：没有状态机、没有共享黑板、
    没有路由决策，只是 `asyncio.gather` 并发调两次模型。真正的多 Agent 协同
    见 `services/workflow.py` 的 step-driven 流程（有状态、可恢复、可中断）。

    原来两个分支都写了 `except: return "目标：…行动步骤：…"` 这种兜底文案。
    已经删掉：模型失败时把写死的假分析当成 AI 结论返回、还存成文档，
    比直接报错坏得多——用户没有任何办法分辨。
    """
    r_res = await db.execute(select(LearningRecord).where(LearningRecord.user_id == current_user.id))
    records = r_res.scalars().all()
    p_res = await db.execute(select(StudentProfile).where(StudentProfile.user_id == current_user.id))
    profile = p_res.scalars().first()
    profile_data = {
        "grade": getattr(profile, "grade", "大三"),
        "interests": getattr(profile, "interests", "互联网产品, 数据分析"),
        "learning_style": getattr(profile, "learning_style", "混合型"),
        "name": current_user.full_name,
    }
    mentor_msg = "基于我的学习记录，给出下一周的学习行动清单，简洁分点。"

    from edu_app.agents.assessment import analyze_performance

    mentor_text, assess_text = await asyncio.gather(
        mentor.get_mentor_response(profile_data, mentor_msg, []),
        analyze_performance(current_user.full_name, records),
    )

    fused = f"综合导师建议：\n{mentor_text}\n\n评估总结：\n{assess_text}"
    title = f"AI 学习计划 - {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}"
    doc = Document(title=title, content=fused, source_url=None, owner_id=current_user.id, tenant_id=getattr(current_user, "tenant_id", None))
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return {"plan": fused, "document_id": doc.id}


# 学习记录：创建真实记录
@router.post("/learning_records")
async def create_learning_record(payload: schemas.LearningRecordCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    ts = None
    try:
        ts = datetime.fromisoformat(payload.timestamp) if hasattr(payload, "timestamp") and payload.timestamp else datetime.now(UTC)
    except Exception:
        ts = datetime.now(UTC)
    record = LearningRecord(
        user_id=current_user.id,
        subject=payload.subject,
        topic=payload.topic,
        activity_type=payload.activity_type,
        score=payload.score,
        timestamp=ts,
        details=payload.details if hasattr(payload, "details") else None
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {"id": record.id}

# RAG：入库文档
@router.post("/rag/ingest")
async def rag_ingest(payload: schemas.RAGIngest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = Document(title=payload.title, content=payload.content, source_url=payload.url, owner_id=current_user.id, tenant_id=getattr(current_user, "tenant_id", None))
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # 构建向量表示（统一走 BGE-M3）
    vec = await embed_text(payload.content)
    db.add(DocumentEmbedding(document_id=doc.id, vector=vec))
    await db.commit()
    metadata = {"document_id": doc.id, "owner_id": current_user.id, "tenant_id": getattr(current_user, "tenant_id", None)}
    # 优先尝试远端向量后端
    try:
        used_remote = False
        if settings.VECTOR_BACKEND and settings.VECTOR_BACKEND.lower() == "pinecone":
            used_remote = pinecone_upsert(vec, doc.id, metadata)
        elif settings.VECTOR_BACKEND and settings.VECTOR_BACKEND.lower() == "milvus":
            used_remote = milvus_upsert(vec, doc.id, metadata)
    except Exception:
        used_remote = False
    try:
        if not used_remote:
            global faiss_index, faiss_ids, faiss_dim
            if faiss_index is None:
                faiss_dim = len(vec)
                idx = faiss.IndexFlatIP(faiss_dim)
                faiss_index = idx
            xb = np.array([vec]).astype("float32")
            faiss.normalize_L2(xb)
            faiss_index.add(xb)
            faiss_ids.append(metadata)
            save_faiss()
    except Exception:
        pass
    return {"document_id": doc.id}

@router.post("/rag/ingest_pdf")
async def rag_ingest_pdf(title: str = Form(...), file: UploadFile = File(...), url: str = Form(None), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        try:
            data = await file.read()
            reader = PdfReader(BytesIO(data))
        except Exception as e:
            logging.error(f"PDF Read Error: {str(e)}")
            raise HTTPException(status_code=400, detail="无法读取PDF文件，文件可能已损坏或加密")

        content_pages = []
        try:
            if not reader.pages:
                raise HTTPException(status_code=400, detail="PDF文件为空")
            for page in reader.pages:
                try:
                    content_pages.append(page.extract_text() or "")
                except Exception:
                    content_pages.append("")
        except HTTPException as he:
            raise he
        except Exception as e:
            logging.error(f"PDF Parse Error: {str(e)}")
            content_pages.append("")
            
        text = "\n".join(content_pages)[:15000]
        if not text.strip():
            # 尝试 OCR 或其他处理？暂时报错
            logging.warning("PDF extracted text is empty")
            # 不强制报错，允许存空文档，但建议前端提示
            text = "(无文本内容或提取失败)"

        doc = Document(title=title, content=text, source_url=url, owner_id=current_user.id, tenant_id=getattr(current_user, "tenant_id", None))
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        
        vec = await embed_text(text)
            
        db.add(DocumentEmbedding(document_id=doc.id, vector=vec))
        await db.commit()
        
        metadata = {"document_id": doc.id, "owner_id": current_user.id, "tenant_id": getattr(current_user, "tenant_id", None)}
        # Try remote backends first
        try:
            used_remote = False
            if settings.VECTOR_BACKEND and settings.VECTOR_BACKEND.lower() == "pinecone":
                used_remote = pinecone_upsert(vec, doc.id, metadata)
            elif settings.VECTOR_BACKEND and settings.VECTOR_BACKEND.lower() == "milvus":
                used_remote = milvus_upsert(vec, doc.id, metadata)
        except Exception:
            used_remote = False
        try:
            if not used_remote:
                global faiss_index, faiss_ids, faiss_dim
                if faiss_index is None:
                    faiss_dim = len(vec)
                    idx = faiss.IndexFlatIP(faiss_dim)
                    faiss_index = idx
                xb = np.array([vec]).astype("float32")
                faiss.normalize_L2(xb)
                faiss_index.add(xb)
                faiss_ids.append(metadata)
                save_faiss()
        except Exception:
            pass
        return {"document_id": doc.id}
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Ingest PDF Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"PDF入库失败: {str(e)}")

# 混合检索的标定参数。改动后请跑 scripts/eval_retrieval.py 复核 Top-1 命中率。
SEMANTIC_FLOOR = 0.30      # BGE-M3 中文无关文本的余弦下限，低于此值视为不相关
SEMANTIC_SPAN = 0.55       # 从「不相关」到「完全相关」的余弦跨度
BM25_SATURATION = 3.0      # BM25 饱和尺度：原始分等于该值时词法分为 0.5
SEMANTIC_WEIGHT = 0.65     # 语义权重（BGE-M3 已能承担主排序）
KEYWORD_WEIGHT = 0.35      # 词法权重（专有名词、代码、数字仍需精确匹配）


async def current_embedding_dim() -> int:
    """当前 embedding 模型的向量维度。

    换模型后库里可能残留旧维度的向量，凡是「把向量塞进固定维度容器」的地方
    （FAISS 索引、批量矩阵）都必须先按这个值过滤。
    """
    status_info = await asyncio.to_thread(embedding_status)
    return int(status_info.get("dim") or 0)


async def chunk_level_search(db: AsyncSession, current_user: User, query: str, top_k: int):
    """分块级混合检索：块级向量 + BM25 → 加权混合 → MMR 去重。

    返回 None 表示知识库还没有分块数据，调用方应回落到文档级旧链路。
    """
    is_admin = getattr(current_user, "role", "student") == "admin"
    stmt = (
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .limit(3000)
    )
    if not is_admin:
        stmt = stmt.where(Document.owner_id == current_user.id)
    rows = (await db.execute(stmt)).all()
    if not rows:
        return None

    qvec = np.array(await embed_text(query), dtype="float32")
    qnorm = np.linalg.norm(qvec) or 1.0
    qvec = qvec / qnorm

    texts = [(chunk.content or "") for chunk, _ in rows]
    query_tokens = tokenize(query)
    doc_tokens = [tokenize(t) for t in texts]
    total = len(texts)

    df: dict[str, int] = {}
    for tokens in doc_tokens:
        for word in set(tokens):
            df[word] = df.get(word, 0) + 1
    idf = {w: math.log(((total - n + 0.5) / (n + 0.5)) + 1) for w, n in df.items()}

    lengths = [len(t) or 1 for t in doc_tokens]
    avgdl = (sum(lengths) / total) if total else 1.0
    k1, b = 1.5, 0.75

    dense_scores, bm25_scores = [], []
    for (chunk, _), tokens, dl in zip(rows, doc_tokens, lengths):
        vector = chunk.vector or []
        if vector and len(vector) == len(qvec):
            arr = np.array(vector, dtype="float32")
            norm = np.linalg.norm(arr) or 1.0
            dense_scores.append(float(np.dot(arr / norm, qvec)))
        else:
            dense_scores.append(0.0)

        tf: dict[str, int] = {}
        for word in tokens:
            tf[word] = tf.get(word, 0) + 1
        score = 0.0
        for word in query_tokens:
            if word in tf:
                score += idf.get(word, 0.0) * (tf[word] * (k1 + 1)) / (
                    tf[word] + k1 * (1 - b + b * dl / avgdl)
                )
        bm25_scores.append(score)

    max_dense = max(dense_scores, default=0.0)
    # 伪向量相似度普遍在 1e-3 量级以下，低于 0.01 视为「没有语义信号」
    has_semantic = max_dense > 0.01

    # 语义分：余弦相似度本身有绝对意义（BGE-M3 中文无关文本约 0.40 起），
    # 这里只做单调平移缩放，既保住绝对区分度，又让展示值落在 0-1 的可读区间。
    # 千万不能用「除以候选集最大值」——所有候选分数接近时，噪声会被放大成满分。
    def _semantic(i: int) -> float:
        if not has_semantic:
            return 0.0
        return max(0.0, min(1.0, (dense_scores[i] - SEMANTIC_FLOOR) / SEMANTIC_SPAN))

    # 词法分：BM25 无上界，用饱和函数压到 0-1，同样避免被候选集最大值放大
    def _keyword(i: int) -> float:
        raw = bm25_scores[i]
        if raw <= 0:
            return 0.0
        return raw / (raw + BM25_SATURATION)

    def _display(raw: float) -> float:
        # 加权融合在「只有语义命中、没有任何词面重叠」时上限只有 SEMANTIC_WEIGHT(0.65)，
        # 直接乘 100 展示会让一个正确的 Top-1 看起来只有 30 多分。
        # 开方是严格单调变换：拉高可读性，同时保证展示分与排序完全同源。
        return max(0.0, min(1.0, raw)) ** 0.5

    def build(idx: int, score: float) -> dict:
        chunk, document = rows[idx]
        return {
            "id": chunk.id,
            "document_id": document.id,
            "title": document.title,
            "content": chunk.content or "",
            "url": document.source_url,
            "chunk_seq": chunk.seq,
            "score": round(_display(score), 3),
            "keyword_score": round(_keyword(idx), 3),
            "semantic_score": round(_semantic(idx), 3),
        }

    if has_semantic:
        # 有语义向量时按加权混合分排序：分数与顺序同源，不会出现「排第一但分数低」
        blended = [
            SEMANTIC_WEIGHT * _semantic(i) + KEYWORD_WEIGHT * _keyword(i)
            for i in range(total)
        ]
        order = sorted(range(total), key=lambda i: blended[i], reverse=True)[: max(top_k * 3, 10)]
        candidates = [build(i, blended[i]) for i in order]
    else:
        # 只有词法信号时退回 RRF 融合，平局用 BM25 原始分破平
        def to_candidates(order_idx: list[int], scores: list[float]) -> list[dict]:
            return [
                {**build(i, float(scores[i])), "bm25": float(bm25_scores[i])}
                for i in order_idx
            ]

        dense_order = sorted(range(total), key=lambda i: dense_scores[i], reverse=True)[: max(top_k * 3, 10)]
        sparse_order = sorted(range(total), key=lambda i: bm25_scores[i], reverse=True)[: max(top_k * 3, 10)]
        fused = advanced_rag_pipeline.reciprocal_rank_fusion(
            to_candidates(dense_order, dense_scores),
            to_candidates(sparse_order, bm25_scores),
            top_k=top_k * 2,
        )
        fused.sort(key=lambda item: (item.get("rrf_score", 0.0), item.get("bm25", 0.0)), reverse=True)
        candidates = fused

    final = advanced_rag_pipeline.maximal_marginal_relevance(candidates, top_k=top_k)
    for item in final:
        item.pop("bm25", None)
        item.pop("_rel_norm", None)
    return final


# RAG：检索
@router.post("/rag/search")
async def rag_search(payload: schemas.RAGSearch, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        q = (payload.query or "").strip()
        if not q or len(q) > 1024:
            raise HTTPException(status_code=400, detail="查询不合法")
        top_k = payload.top_k if hasattr(payload, "top_k") and payload.top_k else 5
        if not (1 <= int(top_k) <= 50):
            raise HTTPException(status_code=400, detail="top_k 不合法")
        alpha = payload.alpha if hasattr(payload, "alpha") and payload.alpha is not None else 0.7
        if not (0.0 <= float(alpha) <= 1.0):
            raise HTTPException(status_code=400, detail="alpha 不合法")

        # 优先走分块级检索：知识库有新分块时，用块级向量 + BM25 融合，
        # 没有分块（历史数据）才回落到下面的文档级旧链路。
        chunk_results = await chunk_level_search(db, current_user, q, int(top_k))
        if chunk_results is not None:
            return {"results": chunk_results, "mode": "chunk"}

        load_faiss()
        scope_admin = getattr(current_user, "role", "student") == "admin"
        backend = (getattr(settings, "VECTOR_BACKEND", None) or "").lower()
        
        # Check if we have any data to search
        if backend not in ("pinecone", "milvus") and (faiss_index is None or not faiss_ids):
            chk = await db.execute(select(DocumentEmbedding).limit(1))
            if chk.scalars().first() is None:
                return {"results": []}

        qvec = await embed_text(q)

        candidates = []
        used_remote = False
        if qvec is not None:
            try:
                if backend == "pinecone":
                    matches = pinecone_query(qvec, top_k)
                    for score, did, meta in matches:
                        if not scope_admin:
                            # 宽松权限检查：是所有者 OR 是同租户
                            is_owner = meta.get("owner_id") == current_user.id
                            # 只有当 tenant_id 存在且相等时才算同租户
                            is_same_tenant = (meta.get("tenant_id") is not None and 
                                            meta.get("tenant_id") == getattr(current_user, "tenant_id", None))
                            
                            if not (is_owner or is_same_tenant):
                                continue
                        candidates.append((float(score), did))
                    used_remote = True if matches else False
                elif backend == "milvus":
                    matches = milvus_query(qvec, top_k)
                    for score, did, meta in matches:
                        if not scope_admin:
                            # 宽松权限检查：是所有者 OR 是同租户
                            is_owner = meta.get("owner_id") == current_user.id
                            # 只有当 tenant_id 存在且相等时才算同租户
                            is_same_tenant = (meta.get("tenant_id") is not None and 
                                            meta.get("tenant_id") == getattr(current_user, "tenant_id", None))
                            
                            if not (is_owner or is_same_tenant):
                                continue
                        candidates.append((float(score), did))
                    used_remote = True if matches else False
            except Exception:
                used_remote = False
            
            # FAISS 索引可能是旧模型建的（维度不符），此时必须走线性扫描，
            # 否则 search 会因维度不匹配直接抛错。
            faiss_usable = bool(faiss_ids) and faiss_index is not None and getattr(faiss_index, "d", len(qvec)) == len(qvec)
            if not used_remote and not faiss_usable:
                # Fallback to linear scan if no index
                # 必须显式 join 出 Document：row.document 是懒加载关系，
                # 在异步会话里访问会触发同步 IO，直接抛 MissingGreenlet。
                result = await db.execute(
                    select(DocumentEmbedding, Document)
                    .join(Document, Document.id == DocumentEmbedding.document_id)
                    .order_by(DocumentEmbedding.id)
                )
                rows = result.all()
                # 换过 embedding 模型后，历史文档向量维度可能与当前查询向量不同
                # （哈希兜底 1536 维 vs BGE-M3 1024 维）。混维度直接进 np.array 会抛错，
                # 必须按当前维度过滤，并提示这些文档需要重建向量。
                qdim = len(qvec)
                vectors = []
                meta = []
                skipped_dim = 0
                for embedding_row, document_row in rows:
                    vec = embedding_row.vector or []
                    if len(vec) != qdim:
                        skipped_dim += 1
                        continue
                    vectors.append(vec)
                    meta.append({
                        "document_id": embedding_row.document_id,
                        "owner_id": document_row.owner_id,
                        "tenant_id": document_row.tenant_id,
                    })
                if skipped_dim:
                    logging.warning(
                        "文档级检索跳过 %s 条维度不匹配的向量（当前 %s 维），请对相应文档执行重建向量",
                        skipped_dim, qdim,
                    )
                if vectors:
                    faiss_dim_local = len(vectors[0])
                    index = faiss.IndexFlatIP(faiss_dim_local)
                    xb = np.array(vectors).astype("float32")
                    faiss.normalize_L2(xb)
                    index.add(xb)
                    ids_local = meta
                    xq = np.array([qvec]).astype("float32")
                    faiss.normalize_L2(xq)
                    k = top_k
                    distances, indices = index.search(xq, max(k * 3, 10))
                    for score, idx in zip(distances[0], indices[0]):
                        if idx < 0 or idx >= len(ids_local):
                            continue
                        
                        m = ids_local[idx]
                        if not scope_admin:
                            # 宽松权限检查：是所有者 OR 是同租户
                            is_owner = m.get("owner_id") == current_user.id
                            # 只有当 tenant_id 存在且相等时才算同租户
                            is_same_tenant = (m.get("tenant_id") is not None and 
                                            m.get("tenant_id") == getattr(current_user, "tenant_id", None))
                            
                            if not (is_owner or is_same_tenant):
                                continue

                        candidates.append((float(score), ids_local[idx]["document_id"]))
            elif faiss_usable:
                xq = np.array([qvec]).astype("float32")
                faiss.normalize_L2(xq)
                k = top_k
                distances, indices = faiss_index.search(xq, max(k * 3, 10))
                for score, idx in zip(distances[0], indices[0]):
                    if idx < 0 or idx >= len(faiss_ids):
                        continue
                    meta = faiss_ids[idx]
                    
                    if not scope_admin:
                        # 宽松权限检查：是所有者 OR 是同租户
                        is_owner = meta.get("owner_id") == current_user.id
                        # 只有当 tenant_id 存在且相等时才算同租户
                        is_same_tenant = (meta.get("tenant_id") is not None and 
                                        meta.get("tenant_id") == getattr(current_user, "tenant_id", None))
                        
                        if not (is_owner or is_same_tenant):
                            continue

                    candidates.append((float(score), meta.get("document_id")))

        dtexts = []
        dids = []
        valid_candidates = [] # Filtered candidates that actually exist in DB

        if qvec is None:
            # No query vector (shouldn't happen given logic above, but safe fallback)
            dres = await db.execute(select(Document))
            docs_all = dres.scalars().all()
            for doc in docs_all:
                if not scope_admin:
                    # 宽松权限检查：是所有者 OR 是同租户
                    is_owner = getattr(doc, "owner_id", None) == current_user.id
                    is_same_tenant = (getattr(doc, "tenant_id", None) is not None and 
                                    getattr(doc, "tenant_id", None) == getattr(current_user, "tenant_id", None))
                    
                    if not (is_owner or is_same_tenant):
                        continue
                dtexts.append(doc.content or "")
                dids.append(doc.id)
            if not dtexts:
                return {"results": []}
        else:
            logging.info(f"rag.local user={current_user.id} q_len={len(q)} top_k={top_k} alpha={alpha} remote={used_remote} cand={len(candidates)}")
            for score, did in candidates:
                dres = await db.execute(select(Document).where(Document.id == did))
                doc = dres.scalars().first()
                if doc:
                    dtexts.append(doc.content or "")
                    dids.append(doc.id)
                    valid_candidates.append((score, did))
            if not dtexts:
                logging.warning("RAG Debug: No valid documents found from candidates")
                return {"results": []}

        docs_tokens = [tokenize(t) for t in dtexts]
        doc_lengths = [len(toks) for toks in docs_tokens]
        avgdl = (sum(doc_lengths) / len(doc_lengths)) if doc_lengths else 1.0
        
        df = {}
        for toks in docs_tokens:
            seen = set()
            for w in toks:
                if w not in seen:
                    df[w] = df.get(w, 0) + 1
                    seen.add(w)
        
        N = max(len(docs_tokens), 1)
        idf = {}
        for w, n in df.items():
            idf[w] = math.log(((N - n + 0.5) / (n + 0.5)) + 1)
        
        q_tokens = tokenize(payload.query)
        bm25_scores = []
        k1 = 1.5
        b = 0.75
        for toks, dl in zip(docs_tokens, doc_lengths):
            tf = {}
            for w in toks:
                tf[w] = tf.get(w, 0) + 1
            score = 0.0
            for w in q_tokens:
                if w in tf:
                    denom = tf[w] + k1 * (1 - b + b * (dl / avgdl))
                    score += idf.get(w, 0.0) * ((tf[w] * (k1 + 1)) / denom)
            bm25_scores.append(score)

        bm_max = max(bm25_scores) if bm25_scores else 1.0
        bm_max = bm_max if bm_max > 0 else 1.0
        bm_norm = [s / bm_max for s in bm25_scores]

        vec_scores = [s for s, _ in valid_candidates] if qvec is not None else [0.0 for _ in bm_norm]
        vec_max = max(vec_scores) if vec_scores else 1.0
        vec_max = vec_max if vec_max > 0 else 1.0
        vec_norm = [s / vec_max for s in vec_scores]

        alpha_val = getattr(payload, "alpha", 0.7) if hasattr(payload, "alpha") else 0.7
        # Ensure lists are same length before zip
        # Advanced RAG: Prepare Candidate Objects for RRF Fusion
        dense_candidates = []
        for score, did in valid_candidates:
            dres = await db.execute(select(Document).where(Document.id == did))
            doc = dres.scalars().first()
            if doc:
                dense_candidates.append({"id": doc.id, "title": doc.title, "content": doc.content[:1000], "url": doc.source_url, "score": float(score)})

        sparse_candidates = []
        # Sort documents by BM25 score
        bm25_tuples = sorted(zip(bm25_scores, dids), key=lambda x: x[0], reverse=True)
        for score, did in bm25_tuples:
            dres = await db.execute(select(Document).where(Document.id == did))
            doc = dres.scalars().first()
            if doc:
                sparse_candidates.append({"id": doc.id, "title": doc.title, "content": doc.content[:1000], "url": doc.source_url, "score": float(score)})

        # 1. 倒数排名融合算法 (Reciprocal Rank Fusion - RRF)
        fused_candidates = advanced_rag_pipeline.reciprocal_rank_fusion(dense_candidates, sparse_candidates, top_k=top_k * 2)

        # 2. 最大边际相关性去重与多样性筛选 (Maximal Marginal Relevance - MMR)
        final_results = advanced_rag_pipeline.maximal_marginal_relevance(fused_candidates, top_k=top_k)

        logging.info(f"Advanced RAG Debug: Final MMR results count={len(final_results)}")
        return {"results": final_results}
    except Exception as e:
        logging.error(f"RAG Search Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"RAG Search Failed: {str(e)}")

# FAISS 索引管理
@router.post("/rag/index/rebuild")
async def rag_index_rebuild(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    global faiss_index, faiss_ids, faiss_dim
    if getattr(current_user, "role", "student") != "admin":
        raise HTTPException(status_code=403, detail="Only admin can rebuild index")
    result = await db.execute(
        select(DocumentEmbedding, Document)
        .join(Document, Document.id == DocumentEmbedding.document_id)
        .order_by(DocumentEmbedding.id)
    )
    rows = result.all()
    # 索引维度必须与当前 embedding 模型一致，否则查询向量塞不进去。
    # 旧模型遗留的向量直接跳过，并提示需要重建。
    target_dim = await current_embedding_dim()
    vectors = []
    metas = []
    skipped_dim = 0
    for embedding_row, document_row in rows:
        vec = embedding_row.vector or []
        if not vec:
            continue
        if len(vec) != target_dim:
            skipped_dim += 1
            continue
        vectors.append(vec)
        metas.append({
            "document_id": embedding_row.document_id,
            "owner_id": document_row.owner_id,
            "tenant_id": document_row.tenant_id,
        })
    if skipped_dim:
        logging.warning(
            "重建索引时跳过 %s 条维度不匹配的向量（目标 %s 维），请执行 scripts/reindex_embeddings.py",
            skipped_dim, target_dim,
        )
    if not vectors:
        faiss_index = None
        faiss_ids = []
        faiss_dim = None
        # 若持久化文件存在则删除
        try:
            if os.path.exists(FAISS_INDEX_PATH):
                os.remove(FAISS_INDEX_PATH)
            if os.path.exists(FAISS_META_PATH):
                os.remove(FAISS_META_PATH)
        except Exception:
            pass
        return {"count": 0}
    dim = len(vectors[0])
    idx = faiss.IndexFlatIP(dim)
    xb = np.array(vectors).astype("float32")
    faiss.normalize_L2(xb)
    idx.add(xb)
    faiss_index = idx
    faiss_ids = metas
    faiss_dim = dim
    save_faiss()
    return {"count": len(metas)}

@router.delete("/rag/index")
async def rag_index_delete(current_user: User = Depends(get_current_user)):
    if getattr(current_user, "role", "student") != "admin":
        raise HTTPException(status_code=403, detail="Only admin can delete index")
    global faiss_index, faiss_ids, faiss_dim
    faiss_index = None
    faiss_ids = []
    faiss_dim = None
    try:
        if os.path.exists(FAISS_INDEX_PATH):
            os.remove(FAISS_INDEX_PATH)
        if os.path.exists(FAISS_META_PATH):
            os.remove(FAISS_META_PATH)
    except Exception:
        pass
    return {"deleted": True}

# Helper for OCR Logic
async def process_ocr_image(content: bytes, mime_type: str) -> str:
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过10MB")
    
    encoded_image = base64.b64encode(content).decode('utf-8')
    
    base = (settings.LLM_API_BASE or "").strip()
    key = (settings.LLM_API_KEY or "").strip()
    model_name = (getattr(settings, "LLM_MODEL", None) or "gpt-4o")
    
    # Determine if we can use LLM
    use_llm = bool(base and key and not getattr(settings, "LLM_DISABLE", False) and key.lower() != "dummy")
    
    if not use_llm:
        return "OCR功能需要配置有效的LLM API Key (支持Vision)。当前为演示模式：这是一个错题示例..."

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别图片中的文字，直接输出识别结果，不要包含其他无关内容。如果包含数学公式，请使用LaTeX格式。"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{encoded_image}"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 2048
    }
    
    timeout = httpx.Timeout(30.0)
    async with llm_client(timeout, base) as client:
        resp = await client.post("/chat/completions", headers=headers, json=payload)
        if resp.status_code != 200:
             logging.error(f"LLM Error: {resp.text}")
             raise HTTPException(status_code=500, detail=f"LLM Error: {resp.status_code}")
        
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content

@router.post("/mistake/ocr")
async def mistake_ocr(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    try:
        content = await file.read()
        mime_type = file.content_type or "image/jpeg"
        text = await process_ocr_image(content, mime_type)
        return {"text": text}
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"OCR Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"OCR识别失败: {str(e)}")

@router.post("/mistakes/batch_image_upload")
async def batch_image_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    upload_dir = str(settings.data_root / "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    base_url = str(request.base_url).rstrip("/")
    
    mistakes_created = []
    
    for file in files:
        try:
            content = await file.read()
            if not content: continue
            
            # 1. Save Image
            file_ext = os.path.splitext(file.filename)[1] or ".jpg"
            filename = f"{uuid.uuid4()}{file_ext}"
            file_path = os.path.join(upload_dir, filename)
            
            with open(file_path, "wb") as f:
                f.write(content)
            
            image_url = f"{base_url}/static/{filename}"
            
            # 2. OCR Processing
            mime_type = file.content_type or "image/jpeg"
            ocr_text = await process_ocr_image(content, mime_type)
            
            if not ocr_text:
                ocr_text = "(OCR Failed or Empty)"
            
            # 3. Create Mistake Record
            # Heuristic for Subject/Topic (Default to "Unclassified" / "General")
            # In a real app, user might select subject for the batch, or we use LLM to classify.
            # Here we default to "未分类" and let the analysis agent refine it if possible (or user updates it).
            
            mistake_record = MistakeRecord(
                user_id=current_user.id,
                subject="未分类", # Placeholder
                topic="综合",     # Placeholder
                question_content=ocr_text,
                image_url=image_url,
                is_resolved=0
            )
            db.add(mistake_record)
            
            # We need to flush to get ID for background task
            await db.flush()
            await db.refresh(mistake_record)
            
            mistakes_created.append(mistake_record)

            # 领域事件直接写长期记忆：错题是结构化事实，不经过模型。
            # 失败只记日志，不影响上传
            await memory_events.mistake_added(db, current_user.id, mistake_record)
            
            # 4. Trigger Analysis
            print(f"Adding background task for mistake {mistake_record.id}")
            background_tasks.add_task(background_analyze_mistake, mistake_record.id)
            
        except Exception as e:
            logging.error(f"Error processing file {file.filename}: {e}")
            # Continue with other files even if one fails
            continue
            
    await db.commit()
    
    return {
        "message": f"Successfully uploaded and processed {len(mistakes_created)} images.",
        "mistakes": [
            {
                "id": m.id,
                "question_content": m.question_content[:50] + "...",
                "image_url": m.image_url
            } for m in mistakes_created
        ]
    }



# 错题本相关接口
async def generate_embedding(text: str) -> List[float]:
    """统一走 services.embedding（BGE-M3 / 远端 / 兜底），避免多处口径不一致。"""
    return await embed_text(text)

async def background_analyze_mistake(mistake_id: int):
    print(f"EXECUTING background analysis for mistake {mistake_id}")
    logging.info(f"Starting background analysis for mistake {mistake_id}")
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(select(MistakeRecord).where(MistakeRecord.id == mistake_id))
            db_mistake = result.scalars().first()
            if db_mistake:
                # 1. Analyze Mistake
                analysis_result = await mistake.analyze_mistake(db_mistake.question_content, db_mistake.subject)
                db_mistake.analysis_result = analysis_result
                
                # 2. Create RAG Document from Mistake
                # This ensures the Mentor Agent can recall this mistake later
                doc_content = (
                    f"【错题记录】科目：{db_mistake.subject}\n"
                    f"主题：{db_mistake.topic}\n"
                    f"问题：{db_mistake.question_content}\n"
                    f"错误原因：{analysis_result.get('error_cause_analysis', '未知')}\n"
                    f"正确解法：{analysis_result.get('correct_solution', '暂无')}\n"
                    f"学习建议：{analysis_result.get('learning_suggestion', '暂无')}"
                )
                
                new_doc = Document(
                    title=f"错题分析: {db_mistake.topic}",
                    content=doc_content,
                    owner_id=db_mistake.user_id,
                    # doc_type="mistake", # Removed as column does not exist
                    created_at=datetime.now(UTC)
                )
                db.add(new_doc)
                await db.flush() # Get ID
                
                # 3. Generate Embedding and Save
                vec = await generate_embedding(doc_content)
                doc_embed = DocumentEmbedding(
                    document_id=new_doc.id,
                    vector=vec
                )
                db.add(doc_embed)
                
                await db.commit()
                logging.info(f"Background analysis and RAG indexing completed for mistake {mistake_id}")
        except Exception as e:
            logging.error(f"Background analysis failed for mistake {mistake_id}: {e}")

@router.post("/mistakes/", response_model=schemas.MistakeResponse)
async def create_mistake(mistake: schemas.MistakeCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_mistake = MistakeRecord(
        user_id=current_user.id,
        subject=mistake.subject,
        topic=mistake.topic,
        question_content=mistake.question_content,
        image_url=mistake.image_url,
        is_resolved=0
    )
    db.add(db_mistake)
    # 先 flush 拿到 id 再写记忆，最后一起提交：记忆和错题在同一个事务里，
    # 不会出现「错题没写成、记忆却记了一笔」
    await db.flush()
    await memory_events.mistake_added(db, current_user.id, db_mistake)
    await db.commit()
    await db.refresh(db_mistake)
    
    # Trigger background analysis
    background_tasks.add_task(background_analyze_mistake, db_mistake.id)
    
    return db_mistake

# Helper for file upload
@router.post("/upload/image")
async def upload_image(request: Request, file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    # Ensure directory exists
    # Matches main.py mount: backend/uploads -> /static
    upload_dir = str(settings.data_root / "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(upload_dir, filename)
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
        
    # backend/uploads is mounted at /static
    # Return full URL for Mini Program compatibility
    base_url = str(request.base_url).rstrip("/")
    return {"url": f"{base_url}/static/{filename}"}



@router.post("/mistakes/{mistake_id}/analyze", response_model=schemas.MistakeResponse)
async def analyze_mistake_endpoint(mistake_id: int, request: schemas.MistakeAnalyzeRequest = None, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(MistakeRecord).where(MistakeRecord.id == mistake_id, MistakeRecord.user_id == current_user.id))
    db_mistake = result.scalars().first()
    if not db_mistake:
        raise HTTPException(status_code=404, detail="Mistake not found")
    
    analysis_result = await mistake.analyze_mistake(db_mistake.question_content, db_mistake.subject)
    
    db_mistake.analysis_result = analysis_result
    await db.commit()
    await db.refresh(db_mistake)
    return db_mistake

@router.get("/mistakes/", response_model=List[schemas.MistakeResponse])
async def get_mistakes(skip: int = 0, limit: int = 20, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(MistakeRecord).where(MistakeRecord.user_id == current_user.id).offset(skip).limit(limit).order_by(MistakeRecord.created_at.desc()))
    return result.scalars().all()

@router.get("/mistakes/{mistake_id}", response_model=schemas.MistakeResponse)
async def get_mistake(mistake_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(MistakeRecord).where(MistakeRecord.id == mistake_id, MistakeRecord.user_id == current_user.id))
    db_mistake = result.scalars().first()
    if not db_mistake:
        raise HTTPException(status_code=404, detail="Mistake not found")
    return db_mistake

@router.post("/mistakes/batch_upload")
async def batch_upload_mistakes(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.csv')):
        raise HTTPException(status_code=400, detail="Only .xlsx and .csv files are supported")
    
    mistakes_to_add = []
    
    try:
        content = await file.read()
        if file.filename.endswith('.xlsx'):
            file_obj = BytesIO(content)
            wb = openpyxl.load_workbook(filename=file_obj, read_only=True)
            ws = wb.active
            rows = list(ws.values)
        elif file.filename.endswith('.csv'):
            text = content.decode('utf-8-sig')
            reader = csv.reader(text.splitlines())
            rows = list(reader)
        else:
            rows = []
            
        if not rows or len(rows) < 2:
            raise HTTPException(status_code=400, detail="Empty or invalid file format (need headers)")
            
        headers = [str(h).lower().strip() if h else "" for h in rows[0]]
        data_rows = rows[1:]
        
        # Simple column mapping
        subject_idx = -1
        topic_idx = -1
        content_idx = -1
        image_idx = -1
        
        for i, h in enumerate(headers):
            if any(k in h for k in ["subject", "科目"]): subject_idx = i
            elif any(k in h for k in ["topic", "主题", "知识点"]): topic_idx = i
            elif any(k in h for k in ["content", "question", "内容", "题目", "问题"]): content_idx = i
            elif any(k in h for k in ["image", "url", "图片"]): image_idx = i
            
        if subject_idx == -1 or content_idx == -1:
            raise HTTPException(status_code=400, detail=f"Missing required columns: Subject/科目 and Content/题目. Found: {headers}")
            
        for row in data_rows:
            if not row: continue
            
            # Safe get by index
            def get_val(idx):
                if idx != -1 and idx < len(row) and row[idx]:
                    return str(row[idx]).strip()
                return None

            subject = get_val(subject_idx) or "未分类"
            content_val = get_val(content_idx)
            topic = get_val(topic_idx)
            image_url = get_val(image_idx)
            
            if not content_val: continue
            
            mistake_record = MistakeRecord(
                user_id=current_user.id,
                subject=subject,
                topic=topic,
                question_content=content_val,
                image_url=image_url,
                is_resolved=0
            )
            db.add(mistake_record)
            mistakes_to_add.append(mistake_record)
            
        if not mistakes_to_add:
             raise HTTPException(status_code=400, detail="No valid mistake entries found")

        # 批量导入一次可能几十条，同一知识点只写一条记忆（upsert 会合并），
        # 所以这里按记录逐条调也不会刷出几十行
        for record in mistakes_to_add:
            await memory_events.mistake_added(db, current_user.id, record)

        await db.commit()
        
        # Trigger background analysis for each mistake
        for m in mistakes_to_add:
            await db.refresh(m)
            background_tasks.add_task(background_analyze_mistake, m.id)
            
        return {"message": f"Successfully uploaded {len(mistakes_to_add)} mistakes", "count": len(mistakes_to_add)}
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Batch upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process file: {str(e)}")

# 知识抽取与关联接口
@router.post("/knowledge/extract", response_model=schemas.KnowledgeExtractionResponse)
async def extract_knowledge(request: schemas.KnowledgeExtractionRequest, current_user: User = Depends(get_current_user), _quota: bool = Depends(require_llm_quota)):
    try:
        extraction_result = await knowledge.extract_knowledge_from_text(request.text, request.subject)
        return extraction_result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# V2.0.0 阶段接口：SaaS 租户与计费管理
# ==========================================
@router.post("/saas/tenant/register", response_model=schemas.TenantResponse)
async def create_saas_tenant(payload: schemas.TenantCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if getattr(current_user, "role", "student") != "admin":
        raise HTTPException(status_code=403, detail="只有系统管理员可以创建租户/机构账户")
        
    db_tenant = saas.Tenant(
        id=payload.id,
        name=payload.name,
        plan_type=payload.plan_type or "basic",
        max_students=payload.max_students or 100
    )
    db.add(db_tenant)
    await db.commit()
    await db.refresh(db_tenant)
    return db_tenant

@router.post("/saas/order/create")
async def create_subscription_order(payload: schemas.SubscriptionOrderCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """创建充值/订阅订单。

    这里**只建单，不收款**。原来直接把 `payment_status` 写成 "paid"、
    还回「充值已即时生效」，等于没有支付网关也能凭空充值——一旦有人基于它做计费
    就是资损，而且账面上看不出来。现在订单停在 pending，等真实回调或人工确认。
    """
    if getattr(current_user, "role", "student") != "admin":
        raise HTTPException(status_code=403, detail="只有系统管理员可以创建充值订单")

    if payload.amount is None or payload.amount <= 0:
        raise HTTPException(status_code=400, detail="订单金额必须大于 0")
    if payload.order_type not in ("plan_subscription", "token_topup"):
        raise HTTPException(status_code=400, detail="订单类型只能是 plan_subscription 或 token_topup")

    tenant = await db.get(saas.Tenant, payload.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    order_id = f"ORD_{uuid.uuid4().hex[:12].upper()}"
    order = saas.SubscriptionOrder(
        id=order_id,
        tenant_id=payload.tenant_id,
        amount=payload.amount,
        order_type=payload.order_type,
        payment_channel=payload.payment_channel or "wechat_pay",
        payment_status="pending",
    )
    db.add(order)
    await db.commit()
    return {
        "order_id": order.id,
        "payment_status": "pending",
        "amount": payload.amount,
        "message": "订单已创建，待支付。接入支付网关后由回调置为 paid；当前可先人工确认。",
    }


@router.post("/saas/order/{order_id}/confirm")
async def confirm_subscription_order(order_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """人工确认收款（对账用）。只有 admin 能调。

    做成独立接口而不是在创建时置 paid：收款是一个**可审计的独立事件**，
    谁在什么时候确认的、原状态是什么，都要留下痕迹。等接了真实支付网关，
    回调直接打这个逻辑即可，前端和账目口径都不用改。
    """
    if getattr(current_user, "role", "student") != "admin":
        raise HTTPException(status_code=403, detail="只有系统管理员可以确认收款")

    order = await db.get(saas.SubscriptionOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")

    if order.payment_status == "paid":
        # 幂等：重复确认不该报错，也不该重复入账
        return {"order_id": order.id, "payment_status": "paid", "already_paid": True}
    if order.payment_status not in ("pending", "failed"):
        raise HTTPException(status_code=409, detail=f"订单当前状态为 {order.payment_status}，不能确认收款")

    order.payment_status = "paid"
    await db.commit()
    return {"order_id": order.id, "payment_status": "paid", "already_paid": False}

# ==========================================
# V3.0.0 阶段接口：艾宾浩斯自适应复练与长期记忆
# ==========================================
@router.post("/ebbinghaus/due_reviews")
async def get_ebbinghaus_due_reviews(payload: schemas.EbbinghausReviewQuery, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    target_user_id = payload.student_id or current_user.id
    due_items = await ebbinghaus.get_due_reviews_for_user(db, target_user_id, max_items=payload.max_items or 5)
    return {"student_id": target_user_id, "due_count": len(due_items), "reviews": due_items}

# 原来的 POST /memory/insights 已删除：它每次请求重查画像 + 错题 + 学习记录
# 拼成一段字符串，和 user_memories 是两套事实来源，而前端从未调用过它。
# 长期记忆统一走 services/memory.py（render_memories 注入对话上下文）。

@router.post("/graph_rag/search")
async def graph_rag_expanded_search(payload: schemas.GraphRAGQuery, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    expanded_data = await GraphRAGSearcher.expand_graph_context(db, payload.subject, payload.query_keywords)
    return expanded_data

# ==========================================
# V4.0.0 阶段接口：端云协同 Model Router
# ==========================================
@router.post("/model_router/query")
async def query_model_router(payload: schemas.RouterQueryRequest, current_user: User = Depends(get_current_user)):
    messages = [{"role": "user", "content": payload.query}]
    response_text = await model_router.dispatch_request(messages, payload.task_type)
    return {"query": payload.query, "response": response_text}
