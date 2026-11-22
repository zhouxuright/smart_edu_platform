from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import declarative_base
from edu_app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    # 云数据库（Neon/Supabase 免费版）会主动断开空闲连接，甚至整体休眠。
    # 没有 pre_ping 的话，休眠后的第一个请求必然拿到一条已经死掉的连接。
    pool_pre_ping=True,
    # 比数据库侧的空闲超时更短，主动换新连接，别等对方先动手
    pool_recycle=300,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base() # 声明式基类

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close() # 关闭数据库会话
