import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from edu_app.main import app
from edu_app.core.database import get_db, Base
import uuid
import base64
from unittest.mock import patch, AsyncMock

# Use in-memory SQLite with aiosqlite
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db

@pytest_asyncio.fixture(scope="module")
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture
async def client(setup_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

@pytest_asyncio.fixture
async def auth_token(client):
    email = f"test_{uuid.uuid4()}@example.com"
    password = "password123"
    
    # Register
    resp = await client.post("/api/register", json={
        "email": email,
        "password": password,
        "full_name": "Test User",
        "role": "student"
    })
    assert resp.status_code == 200
    
    # Login
    resp = await client.post("/api/login", json={
        "email": email,
        "password": password
    })
    assert resp.status_code == 200
    return resp.json()["access_token"]

@pytest.mark.asyncio
async def test_auth_flow(client):
    email = f"auth_{uuid.uuid4()}@example.com"
    password = "password123"
    
    # Register
    resp = await client.post("/api/register", json={
        "email": email,
        "password": password,
        "full_name": "Auth User",
        "role": "student"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == email
    
    # Login
    resp = await client.post("/api/login", json={
        "email": email,
        "password": password
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()

@pytest.mark.asyncio
async def test_mistake_flow(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    # Create Mistake
    mistake_data = {
        "subject": "Math",
        "topic": "Calculus",
        "question_content": "What is the derivative of x^2?",
        "mistake_type": "concept",
        "cause_analysis": "Forgot power rule"
    }
    resp = await client.post("/api/mistakes/", json=mistake_data, headers=headers)
    assert resp.status_code == 200
    mistake_id = resp.json()["id"]
    
    # Get Mistakes
    resp = await client.get("/api/mistakes/", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
    
    # Get Single Mistake
    resp = await client.get(f"/api/mistakes/{mistake_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["question_content"] == mistake_data["question_content"]

@pytest.mark.asyncio
async def test_dashboard_summary(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    resp = await client.get("/api/dashboard/summary", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "active_courses" in data
    assert "study_hours" in data

@pytest.mark.asyncio
async def test_rag_ingest_search(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    doc_data = {
        "title": "Test Doc",
        "content": "This is a test document for RAG system.",
        "url": "http://example.com"
    }
    
    # Mock embeddings to avoid LLM call
    with patch("edu_app.api.endpoints.OpenAIEmbeddings") as mock_embed:
        mock_instance = mock_embed.return_value
        mock_instance.embed_query.return_value = [0.1] * 1536
        
        resp = await client.post("/api/rag/ingest", json=doc_data, headers=headers)
        assert resp.status_code == 200
        
        # Search
        search_data = {
            "query": "test document",
            "top_k": 2
        }
        resp = await client.post("/api/rag/search", json=search_data, headers=headers)
        assert resp.status_code == 200
        # Results might be empty if vector store not perfectly mocked/connected, but endpoint works
        
@pytest.mark.asyncio
async def test_agent_endpoints(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    # Mentor Agent (Non-streaming)
    req_data = {
        "message": "How do I learn Math?",
        "history": []
    }
    
    # Mock mentor response
    with patch("edu_app.agents.mentor.get_mentor_response", new_callable=AsyncMock) as mock_mentor:
        mock_mentor.return_value = "Study hard!"
        
        resp = await client.post("/api/agent/mentor", json=req_data, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["response"] == "Study hard!"

    # Test Parent Stream
    async def mock_stream(*args, **kwargs):
        yield "Part 1"
        yield "Part 2"

    with patch("edu_app.agents.parent.generate_parent_report_stream", side_effect=mock_stream):
        resp = await client.post("/api/agent/parent/stream", json={"student_id": 1}, headers=headers)
        assert resp.status_code == 200
        # Streaming response verification can be complex, just check status for now
    
    # Test Assessment Stream
    with patch("edu_app.agents.assessment.analyze_performance_stream", side_effect=mock_stream):
        resp = await client.post("/api/agent/assessment/stream", json={"student_id": 1}, headers=headers)
        assert resp.status_code == 200

@pytest.mark.asyncio
async def test_ocr_endpoint_real(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}

    img_content = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
    files = {"file": ("test.gif", img_content, "image/gif")}

    resp = await client.post("/api/mistake/ocr", files=files, headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert "text" in data
    assert isinstance(data["text"], str)
