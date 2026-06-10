import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    """Placeholder integration test for reception service API."""
    response = client.get("/")
    assert response.status_code in (200, 307, 404)
