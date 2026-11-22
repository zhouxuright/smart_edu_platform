import asyncio
import os
import sys
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

# Add backend directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'backend')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), 'backend', '.env'))

from sqlalchemy import select
from edu_app.core.database import AsyncSessionLocal
from edu_app.models.learning import MistakeRecord
# We can't import endpoints directly because it initializes FastAPI app and router which might fail
# Instead, we import the function directly if possible, but it's defined inside endpoints.py
# So we have to import endpoints.py
# But endpoints.py might have relative imports or circular imports that fail
try:
    from edu_app.api.endpoints import background_analyze_mistake
except ImportError as e:
    print(f"Import failed: {e}")
    # Try to mock dependencies if needed or just copy the function logic here
    # But copying logic defeats the purpose of testing the actual code
    # So let's try to fix the import
    sys.exit(1)

async def test_analysis(mistake_id):
    print(f"Testing analysis for mistake {mistake_id}...")
    try:
        await background_analyze_mistake(mistake_id)
        print("Analysis completed successfully.")
        
        # Verify result
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(MistakeRecord).where(MistakeRecord.id == mistake_id))
            mistake = result.scalars().first()
            if mistake:
                if mistake.analysis_result:
                    print("Analysis Result present:")
                    import json
                    print(json.dumps(mistake.analysis_result, ensure_ascii=False, indent=2))
                else:
                    print("Analysis Result missing!")
            else:
                print(f"Mistake {mistake_id} not found!")
                
    except Exception as e:
        print(f"Analysis failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    mistake_id = 21
    if len(sys.argv) > 1:
        mistake_id = int(sys.argv[1])
        
    asyncio.run(test_analysis(mistake_id))
