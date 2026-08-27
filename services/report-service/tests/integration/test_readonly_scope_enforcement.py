"""Read-only impersonation enforcement, exercised over real requests.

ReadOnlyScopeMiddleware reads request.state.tenant *before* calling the next
handler, but that attribute is only set by the get_current_tenant dependency,
which runs afterwards. The tenant is therefore always None at the middleware's
check and it has never blocked a single write. The pre-existing unit test misses
this because it constructs the middleware directly and pre-sets state that never
exists at runtime.

These tests drive the real app over HTTP, so they fail if the guarantee is
absent rather than if a mock is shaped wrongly.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from app.core import tenant_auth as ta
from app.core.config import settings
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.main import app

# An assistant route is used because it is a real POST that exists in this
# service and is cheap to reach; the behaviour under test is the shared
# dependency, not the assistant.
WRITE_URL = "/api/v1/reports/assistant/feedback"
WRITE_BODY = {"request_id": "req-1", "rating": "helpful"}


def _context(scope: str) -> TenantContext:
    return TenantContext(
        tenant_id="hosp-aaaa1111",
        user_sub="user-1",
        preferred_username="jdoe",
        email=None,
        roles=["hospital_admin"],
        is_super_admin=False,
        scope=scope,
    )


@pytest.fixture
def readonly_session(monkeypatch):
    """Sign every request in as a real read-only impersonation session.

    The token decode is substituted, not the dependency itself. Overriding
    get_current_tenant would skip the very check under test and the assertion
    would pass for the wrong reason.
    """

    async def fake_decode(_token: str) -> dict:
        return {
            "sub": "user-1",
            "preferred_username": "jdoe",
            "tenant_id": "hosp-aaaa1111",
            "scope": "readonly",
            "realm_access": {"roles": ["hospital_admin"]},
        }

    async def not_suspended(_tenant_id: str) -> bool:
        return False

    monkeypatch.setattr(ta, "_decode_token", fake_decode)
    monkeypatch.setattr(ta, "is_tenant_suspended", not_suspended)
    yield {"Authorization": "Bearer any-token-the-decode-is-stubbed"}


@pytest.fixture
def enforcement_mode():
    original = settings.readonly_scope_enforcement

    def _set(mode: str) -> None:
        settings.readonly_scope_enforcement = mode

    yield _set
    settings.readonly_scope_enforcement = original


class TestReadOnlyScopeHelper:
    """The helper is called by the dependency, so it is tested on its own too."""

    def _request(self, method: str):
        scope = {
            "type": "http",
            "method": method,
            "path": "/api/v1/reports/assistant/feedback",
            "headers": [],
            "query_string": b"",
        }
        from starlette.requests import Request

        return Request(scope)

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_enforce_refuses_every_write_method(self, method, enforcement_mode):
        enforcement_mode("enforce")

        with pytest.raises(Exception) as excinfo:
            ta._apply_readonly_scope(self._request(method), _context("readonly"))

        assert getattr(excinfo.value, "status_code", None) == 403
        assert excinfo.value.detail["code"] == "READ_ONLY_SCOPE"

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    def test_reads_are_never_refused(self, method, enforcement_mode):
        enforcement_mode("enforce")

        # Must not raise: read-only means read-only, not no-access.
        ta._apply_readonly_scope(self._request(method), _context("readonly"))

    def test_a_full_scope_session_is_untouched(self, enforcement_mode):
        enforcement_mode("enforce")

        ta._apply_readonly_scope(self._request("POST"), _context("full"))

    def test_log_mode_permits_the_write_but_records_it(self, enforcement_mode, caplog):
        enforcement_mode("log")

        with caplog.at_level("WARNING"):
            ta._apply_readonly_scope(self._request("POST"), _context("readonly"))

        assert "readonly scope would refuse write" in caplog.text
        assert "hosp-aaaa1111" in caplog.text

    def test_the_log_line_carries_no_token_or_body(self, enforcement_mode, caplog):
        enforcement_mode("log")

        with caplog.at_level("WARNING"):
            ta._apply_readonly_scope(self._request("POST"), _context("readonly"))

        lowered = caplog.text.lower()
        for forbidden in ("bearer", "authorization", "password", "eyj"):
            assert forbidden not in lowered

    def test_off_disables_the_check_entirely(self, enforcement_mode):
        enforcement_mode("off")

        ta._apply_readonly_scope(self._request("POST"), _context("readonly"))

    def test_an_unrecognised_mode_falls_back_to_logging_not_to_blocking(
        self, enforcement_mode, caplog
    ):
        """A typo in configuration must not silently start refusing writes."""
        enforcement_mode("Enfrce")

        with caplog.at_level("WARNING"):
            ta._apply_readonly_scope(self._request("POST"), _context("readonly"))

        assert "readonly scope would refuse write" in caplog.text

    def test_mode_matching_is_case_and_whitespace_tolerant(self, enforcement_mode):
        enforcement_mode("  ENFORCE  ")

        with pytest.raises(Exception) as excinfo:
            ta._apply_readonly_scope(self._request("POST"), _context("readonly"))

        assert getattr(excinfo.value, "status_code", None) == 403


class TestReadOnlyScopeOverHttp:
    def test_a_readonly_write_is_refused_end_to_end(
        self, readonly_session, enforcement_mode
    ):
        enforcement_mode("enforce")

        with TestClient(app) as client:
            response = client.post(WRITE_URL, json=WRITE_BODY, headers=readonly_session)

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "READ_ONLY_SCOPE"

    def test_a_readonly_read_is_still_allowed_end_to_end(
        self, readonly_session, enforcement_mode
    ):
        """Read-only must keep working for reads, or impersonation is useless."""
        enforcement_mode("enforce")

        with TestClient(app) as client:
            response = client.get("/health", headers=readonly_session)

        assert response.status_code == 200

    def test_log_mode_leaves_existing_behaviour_unchanged(
        self, readonly_session, enforcement_mode
    ):
        """The default must not change what any caller sees today."""
        enforcement_mode("log")

        with TestClient(app) as client:
            response = client.post(WRITE_URL, json=WRITE_BODY, headers=readonly_session)

        assert response.status_code != 403

    def test_the_shipped_default_is_log_not_enforce(self):
        """Guards the rollout: enforcing by default would break live callers."""
        importlib.reload(importlib.import_module("app.core.config"))
        from app.core.config import Settings

        assert Settings().readonly_scope_enforcement == "log"
