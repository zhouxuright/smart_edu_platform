import os

dirs = [
    "backend/app/core",
    "backend/app/models",
    "backend/app/schemas",
    "backend/app/api",
    "backend/app/agents",
    "backend/app/services"
]

for d in dirs:
    try:
        os.makedirs(d, exist_ok=True)
        print(f"Created {d}")
    except Exception as e:
        print(f"Error creating {d}: {e}")
