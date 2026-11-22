import asyncio
import sys
import os
from dotenv import load_dotenv
from sqlalchemy import select

# Force load .env from backend directory
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)
load_dotenv(os.path.join(backend_dir, ".env"))

from edu_app.models.learning import LearningRecord
from edu_app.models.user import User
from edu_app.core.database import AsyncSessionLocal

async def main():
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User))
            users = result.scalars().all()
            print(f"Found {len(users)} users.")
            for u in users:
                print(f"User: {u.id} - {u.email} - {u.full_name}")
                res = await db.execute(select(LearningRecord).where(LearningRecord.user_id == u.id))
                records = res.scalars().all()
                print(f"  Records: {len(records)}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())