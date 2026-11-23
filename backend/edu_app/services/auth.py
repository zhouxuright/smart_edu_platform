from datetime import datetime, timedelta, UTC
from typing import Optional
from jose import jwt
from passlib.context import CryptContext
from starlette.concurrency import run_in_threadpool
from edu_app.core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto") # 密码哈希上下文

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)


# argon2 是故意设计成慢的（实测校验 48ms、哈希 81ms），而且是纯 CPU 同步计算。
# 直接在 async 路由里调用会把整个事件循环卡住——单进程部署时，20 个并发登录
# 就能让服务冻结约 1 秒，连静态文件都发不出去。
# 所以 HTTP 链路一律走下面两个 async 版本，把计算丢到线程池；
# 上面的同步版本保留给脚本和测试用。
async def verify_password_async(plain_password, hashed_password):
    return await run_in_threadpool(verify_password, plain_password, hashed_password)

async def get_password_hash_async(password):
    return await run_in_threadpool(get_password_hash, password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        minutes = getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 15)
        expire = datetime.now(UTC) + timedelta(minutes=minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt
