from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录，本地默认的数据根
_BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    DATABASE_URL: str
    LLM_API_KEY: str
    LLM_API_BASE: str
    LLM_MODEL: Optional[str] = None
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    VECTOR_BACKEND: str | None = None
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_ENV: Optional[str] = None
    PINECONE_INDEX: Optional[str] = None
    MILVUS_HOST: Optional[str] = None
    MILVUS_PORT: Optional[str] = None
    MILVUS_COLLECTION: Optional[str] = None
    LLM_DISABLE: bool = False
    # 每个非管理员用户每天可调用大模型的次数，超出返回 429
    LLM_DAILY_QUOTA: int = 30
    # 向量化配置：local=BGE-M3 本地推理，openai=OpenAI 兼容 /embeddings，auto=优先本地
    EMBEDDING_PROVIDER: str = "auto"
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_BATCH_SIZE: int = 8
    EMBEDDING_MAX_LENGTH: int = 512
    # 是否打印每条 SQL。本地排查时打开，线上必须关：一是日志量，二是把参数打到日志里
    DB_ECHO: bool = False
    # 连接池上限。不显式配置时 SQLAlchemy 默认 5+10=15，单个副本就吃掉 15 条连接；
    # 将来加到多副本，每副本都按这个数算进 Postgres 的 max_connections。
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    # 每个用户的每分钟请求上限。单副本用进程内计数即可，不引入 Redis。
    RATE_LIMIT_PER_MINUTE: int = 120
    # 登录/注册单独收紧，防撞库。同一个 key 每分钟最多试这么多次。
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10
    # 同时打给大模型的请求数上限。供应商的并发限制通常比服务器先到，
    # 超过就排队，排队超过 LLM_QUEUE_TIMEOUT 秒直接返回 503，避免请求堆积。
    LLM_MAX_CONCURRENCY: int = 20
    LLM_QUEUE_TIMEOUT: int = 15
    # 运行期数据根目录。uploads/、storage/resumes/、faiss.index 全挂在它下面。
    # 线上挂持久卷后指过去（如 /data），不配则落到 backend/ 目录——容器里那个目录
    # 是临时的，重启就没了，所以线上必须配。
    DATA_DIR: Optional[str] = None
    # 允许的前端来源，逗号分隔。线上填 Vercel 域名；"*" 只适合本地
    CORS_ORIGINS: str = "*"

    @property
    def data_root(self) -> Path:
        root = Path(self.DATA_DIR).expanduser() if self.DATA_DIR else _BACKEND_DIR
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [item.strip() for item in raw.split(",") if item.strip()]

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """把云厂商给的连接串改成 asyncpg 能吃的形式。

        Neon / Supabase / Render 的控制台一律给 `postgresql://...?sslmode=require`，
        直接丢给 create_async_engine 会报两个错：
        1. 没有驱动前缀 → 它去找 psycopg2 的同步驱动，异步上下文里直接崩；
        2. `sslmode` / `channel_binding` 是 libpq 的参数，asyncpg 不认，会被当成
           未知关键字透传给 connect()，报 unexpected keyword argument。
        这里统一收口，避免部署时对着一条连接串排查半天。
        """
        url = (value or "").strip()
        if not url:
            return url

        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url[len(prefix):]
                break

        if not url.startswith("postgresql+asyncpg://"):
            return url

        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(url)
        # libpq 参数名 -> asyncpg 参数名；其余原样保留（asyncpg 支持 ssl=require 等字符串值）
        renamed = {"sslmode": "ssl"}
        dropped = {"channel_binding", "target_session_attrs"}
        query = []
        for key, val in parse_qsl(parts.query, keep_blank_values=True):
            if key in dropped:
                continue
            query.append((renamed.get(key, key), val))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
