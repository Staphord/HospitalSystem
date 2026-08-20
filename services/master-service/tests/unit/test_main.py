"""Unit tests for main.py (FastAPI application lifespan, migrations, and health check) in master-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import _find_migrations_dir, _run_migrations, lifespan, app

def test_find_migrations_dir():
    with patch("os.path.isdir", return_value=True):
        res = _find_migrations_dir()
        assert res is not None

    with patch("os.path.isdir", return_value=False):
        res_none = _find_migrations_dir()
        assert res_none is None

def test_run_migrations():
    with patch("app.main._find_migrations_dir", return_value="/tmp/migrations"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "Applied"
            _run_migrations()
            assert mock_run.called

    with patch("app.main._find_migrations_dir", return_value=None):
        with pytest.raises(RuntimeError, match="Master migrations directory not found"):
            _run_migrations()

def test_run_migrations_error():
    import subprocess
    with patch("app.main._find_migrations_dir", return_value="/tmp/migrations"):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "alembic", stderr="Migration failed")):
            with pytest.raises(subprocess.CalledProcessError):
                _run_migrations()

@pytest.mark.asyncio
async def test_lifespan_context():
    dummy_app = MagicMock()
    with patch("app.main._run_migrations"):
        with patch("app.core.database.init_db"):
            with patch("app.services.subscription_plans.sync_plans_to_db"):
                with patch("app.main._start_suspension_loop", AsyncMock()):
                    with patch("app.events.subscriber.start_subscriber", AsyncMock()):
                        async with lifespan(dummy_app):
                            pass

@pytest.mark.asyncio
async def test_lifespan_exceptions():
    dummy_app = MagicMock()
    with patch("app.main._run_migrations"):
        with patch("app.core.database.init_db", side_effect=Exception("DB Init Exception")):
            with patch("app.services.subscription_plans.sync_plans_to_db"):
                with patch("app.main._start_suspension_loop", AsyncMock()):
                    with patch("app.events.subscriber.start_subscriber", side_effect=Exception("Sub Error")):
                        async with lifespan(dummy_app):
                            pass

@pytest.mark.asyncio
async def test_start_suspension_loop():
    from app.main import _start_suspension_loop
    with patch("app.services.suspension_job.suspension_loop", AsyncMock()):
        await _start_suspension_loop()

def test_health_endpoint():
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["service"] == "master-service"

@pytest.mark.asyncio
async def test_security_headers_prod():
    from app.main import security_headers

    req = MagicMock()
    call_next = AsyncMock(return_value=MagicMock(headers={}))

    with patch("app.config.settings.environment", "prod"):
        res = await security_headers(req, call_next)
        assert res.headers["Content-Security-Policy"] == "default-src 'none'"
