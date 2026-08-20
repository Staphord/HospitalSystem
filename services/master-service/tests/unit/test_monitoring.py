"""Unit tests for monitoring telemetry and tenant metrics in master-service.
"""
from unittest.mock import MagicMock, patch
import pytest
from starlette.requests import Request

from app.api.v1.monitoring.router import monitoring_telemetry, monitoring_tenant_counts
from app.core.security import TokenPayload

def get_handler(fn):
    return getattr(fn, "__wrapped__", fn)

def make_req():
    return Request(scope={"type": "http", "method": "GET", "path": "/telemetry", "headers": [], "client": ("127.0.0.1", 1234)})

def make_user():
    return TokenPayload(sub="sa1", preferred_username="admin", email="a@b.com", realm_access={"roles": ["super_admin"]}, raw={"type": "superadmin", "role": "super_admin"})

@pytest.mark.asyncio
async def test_monitoring_telemetry_success():
    req = make_req()
    user = make_user()
    fn = get_handler(monitoring_telemetry)

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        MagicMock(scalar=lambda: 5),
        MagicMock(scalar=lambda: 1048576),
    ]
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    mock_psutil = MagicMock()
    mock_psutil.cpu_percent.return_value = 10.5
    mock_psutil.cpu_count.return_value = 4
    mock_psutil.virtual_memory.return_value = MagicMock(total=8000, available=4000, used=4000, percent=50.0)
    mock_psutil.disk_usage.return_value = MagicMock(total=100000, used=50000, free=50000, percent=50.0)

    with patch.dict("sys.modules", {"psutil": mock_psutil}):
        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            res = await fn(req, user)
            assert "cpu" in res
            assert "memory" in res
            assert res["db_connections"]["active"] == 5

@pytest.mark.asyncio
async def test_monitoring_telemetry_errors():
    req = make_req()
    user = make_user()
    fn = get_handler(monitoring_telemetry)

    with patch("sqlalchemy.create_engine", side_effect=Exception("DB Error")):
        res = await fn(req, user)
        assert "db_error" in res

@pytest.mark.asyncio
async def test_monitoring_tenant_counts():
    req = make_req()
    user = make_user()
    db = MagicMock()

    db.query.return_value.count.return_value = 10
    db.query.return_value.filter.return_value.count.side_effect = [8, 1, 1, 2]

    fn = get_handler(monitoring_tenant_counts)
    res = await fn(req, db, user)
    assert res["total"] == 10
    assert res["active"] == 8
    assert res["suspended"] == 1
