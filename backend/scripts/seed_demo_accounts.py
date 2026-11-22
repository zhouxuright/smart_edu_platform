"""创建本地演示账号（管理员 / 家长），用于验证角色菜单过滤。

注册接口只会创建学生账号（role 固定为 student），所以家长和管理员只能通过这里造。

密码来源：环境变量 DEMO_ADMIN_PASSWORD / DEMO_PARENT_PASSWORD。
未设置时会随机生成一个并打印出来——刻意不在代码里写死默认密码，
避免演示账号带着一个可预测的弱口令被带到线上。

用法（在 backend 目录）：
    python scripts/seed_demo_accounts.py
    DEMO_ADMIN_PASSWORD=xxx DEMO_PARENT_PASSWORD=yyy python scripts/seed_demo_accounts.py
"""
import asyncio
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from edu_app.core.config import settings
from edu_app.models.user import User
from edu_app.services.auth import get_password_hash

ACCOUNTS = [
    {
        "email": "admin@example.com",
        "full_name": "演示管理员",
        "role": "admin",
        "password_env": "DEMO_ADMIN_PASSWORD",
    },
    {
        "email": "parent@example.com",
        "full_name": "演示家长",
        "role": "parent",
        "password_env": "DEMO_PARENT_PASSWORD",
    },
]


async def main() -> int:
    engine = create_async_engine(settings.DATABASE_URL)
    generated: list[tuple[str, str]] = []
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for acc in ACCOUNTS:
                password = os.getenv(acc["password_env"])
                if not password:
                    password = secrets.token_urlsafe(9)
                    generated.append((acc["email"], password))

                existing = (
                    await session.execute(select(User).where(User.email == acc["email"]))
                ).scalars().first()

                if existing:
                    existing.role = acc["role"]
                    existing.hashed_password = get_password_hash(password)
                    print(f"已更新 {acc['email']} → role={acc['role']}")
                else:
                    session.add(
                        User(
                            email=acc["email"],
                            hashed_password=get_password_hash(password),
                            full_name=acc["full_name"],
                            role=acc["role"],
                            tenant_id=None,
                        )
                    )
                    print(f"已创建 {acc['email']} → role={acc['role']}")
            await session.commit()
    finally:
        await engine.dispose()

    if generated:
        print("\n以下账号的密码是本次随机生成的，请立即记录（只显示这一次）：")
        for email, pwd in generated:
            print(f"  {email}  {pwd}")
        print("建议用环境变量固定密码后重跑本脚本，或上线前删除这些演示账号。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
