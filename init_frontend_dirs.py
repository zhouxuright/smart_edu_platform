import os

dirs = [
    "frontend/src/pages",
    "frontend/src/components",
    "frontend/src/services"
]

for d in dirs:
    try:
        os.makedirs(d, exist_ok=True)
        print(f"Created {d}")
    except Exception as e:
        print(f"Error creating {d}: {e}")
