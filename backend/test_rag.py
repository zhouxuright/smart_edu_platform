
import httpx
import asyncio

BASE_URL = "http://localhost:8000/api"

async def test_search():
    async with httpx.AsyncClient() as client:
        # 1. Register/Login
        email = "test_rag@example.com"
        password = "password123"
        
        # Try login first
        try:
            resp = await client.post(f"{BASE_URL}/login", json={"email": email, "password": password})
            if resp.status_code != 200:
                # Register if login fails
                print("Login failed, trying register...")
                resp = await client.post(f"{BASE_URL}/register", json={
                    "email": email, 
                    "password": password, 
                    "full_name": "Test User", 
                    "role": "admin", 
                    "tenant_id": "tenant_1"
                })
                print(f"Register status: {resp.status_code}")
                # Login again
                resp = await client.post(f"{BASE_URL}/login", json={"email": email, "password": password})
                print(f"Second login status: {resp.status_code}, body: {resp.text}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Auth error: {e!r}")
            return

        if resp.status_code != 200:
            print(f"Login failed: {resp.text}")
            return

        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Search
        query = "请具体说说量化交易AI多Agent智能体平台 这个项目"
        print(f"Searching for: {query}")
        try:
            resp = await client.post(f"{BASE_URL}/rag/search", 
                                   json={"query": query, "top_k": 5}, 
                                   headers=headers, timeout=30.0)
            print(f"Search Status: {resp.status_code}")
            print(f"Search Result: {resp.json()}")
        except Exception as e:
            print(f"Search error: {e}")

if __name__ == "__main__":
    asyncio.run(test_search())
