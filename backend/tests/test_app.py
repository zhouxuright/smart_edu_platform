from fastapi.testclient import TestClient
from edu_app.main import app


def test_root_ok():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "message" in data
    assert "AI Agent" in data["message"]
