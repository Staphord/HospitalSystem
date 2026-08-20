from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_contract_and_security_headers():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "consultation-service"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_consultation_routes_require_tenant_context():
    response = client.get("/api/v1/consultation/queue")

    # The router is protected by the tenant/auth dependency.  The exact
    # response can be 401 or 403 depending on the configured auth backend,
    # but an anonymous request must never reach the handler.
    assert response.status_code in {401, 403}
