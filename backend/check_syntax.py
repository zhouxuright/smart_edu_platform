
import sys
import os

# Add backend directory to path
sys.path.append(os.path.abspath("e:\\smart_edu_platform\\backend"))

try:
    print("Checking schemas...")
    from edu_app.schemas import schemas
    print("Schemas OK")

    print("Checking models...")
    from edu_app.models import learning
    print("Models OK")

    print("Checking agents...")
    from edu_app.agents import mistake
    from edu_app.agents import knowledge
    print("Agents OK")

    print("Checking endpoints...")
    from edu_app.api import endpoints
    print("Endpoints OK")

    print("All checks passed!")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
