"""The route the browser asks before it draws anything.

The floating launcher used to be rendered for every staff role and only
withdrawn after a question came back 404. On a hospital running with the
assistant switched off that put a permanent button on screen whose only
behaviour was to fail, and the only way to find out was to press it.

This route is what lets the browser decide first. Everything it reports is
derived from the verified token and the deployment switch; nothing is accepted
from the request.
"""

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.assistant import service as svc
from app.core.tenant_auth import get_current_tenant
from app.main import app

STATUS_URL = "/api/v1/reports/assistant/status"


@dataclass
class FakeTenantContext:
    tenant_id: str | None = "hosp-aaaa1111"
    user_sub: str = "user-1"
    preferred_username: str | None = "jdoe"
    email: str | None = None
    roles: list = field(default_factory=lambda: ["receptionist"])
    is_super_admin: bool = False
    scope: str = "full"
    raw_token: dict = field(default_factory=dict)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from app.core.limiter import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def assistant_on(monkeypatch):
    monkeypatch.setattr(svc.settings, "assistant_operational_chat_enabled", True)


@pytest.fixture
def as_user():
    def _sign_in(**kwargs):
        ctx = FakeTenantContext(**kwargs)
        app.dependency_overrides[get_current_tenant] = lambda: ctx
        return ctx

    yield _sign_in
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


class TestItAnswersRatherThanFailing:
    def test_it_is_a_200_even_when_the_assistant_is_off(self, client, as_user):
        # Deliberately not a 404. The browser has to tell "switched off here"
        # apart from "the request failed", and a 404 on the shell's own mount
        # is indistinguishable from a routing problem.
        as_user()

        response = client.get(STATUS_URL)

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_a_request_without_a_token_is_refused(self, client):
        app.dependency_overrides.clear()

        assert client.get(STATUS_URL).status_code == 401


class TestTheDeploymentSwitchDecidesFirst:
    def test_off_means_no_assistant_for_anyone(self, client, as_user, monkeypatch):
        monkeypatch.setattr(svc.settings, "assistant_operational_chat_enabled", False)
        as_user(roles=["doctor"])

        body = client.get(STATUS_URL).json()

        assert body["enabled"] is False
        assert body["capabilities"] == []

    def test_on_means_every_capability_this_role_can_reach(
        self, client, as_user, assistant_on
    ):
        as_user(roles=["doctor"])

        body = client.get(STATUS_URL).json()

        assert body["enabled"] is True
        # A doctor reaches the clinical capabilities as well as the operational
        # ones, so the list is more than just chat.
        assert "operational_chat" in body["capabilities"]
        assert "medication_check" in body["capabilities"]


class TestItReportsOnlyWhatThisCallerCouldReach:
    def test_a_receptionist_is_not_offered_the_clinical_capabilities(
        self, client, as_user, assistant_on
    ):
        as_user(roles=["receptionist"])

        capabilities = client.get(STATUS_URL).json()["capabilities"]

        assert "operational_chat" in capabilities
        assert "medication_check" not in capabilities
        assert "differential_support" not in capabilities

    def test_a_hospital_admin_is_not_offered_the_clinical_capabilities(
        self, client, as_user, assistant_on
    ):
        # Administrative seniority is not clinical access.
        as_user(roles=["hospital_admin"])

        assert "medication_check" not in client.get(STATUS_URL).json()["capabilities"]

    def test_a_platform_super_admin_has_no_assistant(
        self, client, as_user, assistant_on
    ):
        # Super admins administer tenants and never read tenant content, so
        # they are refused everywhere else and offered nothing here.
        as_user(roles=["super_admin"], is_super_admin=True, tenant_id=None)

        assert client.get(STATUS_URL).json()["enabled"] is False

    def test_a_read_only_impersonation_session_has_no_assistant(
        self, client, as_user, assistant_on
    ):
        as_user(roles=["doctor"], scope="readonly")

        assert client.get(STATUS_URL).json()["enabled"] is False

    def test_a_role_outside_the_matrix_has_no_assistant(
        self, client, as_user, assistant_on
    ):
        as_user(roles=["hospital_user"])

        body = client.get(STATUS_URL).json()

        assert body["enabled"] is False
        assert body["capabilities"] == []


class TestItCarriesNothingSensitive:
    def test_the_response_has_only_the_three_contract_fields(
        self, client, as_user, assistant_on
    ):
        as_user(roles=["doctor"])

        body = client.get(STATUS_URL).json()

        assert set(body) == {"enabled", "capabilities", "provider_configured"}

    def test_it_reports_credential_presence_and_never_the_credential(
        self, client, as_user, assistant_on, monkeypatch
    ):
        from app.assistant import config_store

        config_store._cached = config_store.AssistantRuntimeConfig(
            api_key="gsk_secret_value"
        )
        config_store._cached_at = float("inf")
        as_user(roles=["doctor"])

        response = client.get(STATUS_URL)

        assert response.json()["provider_configured"] is True
        assert "gsk_secret_value" not in response.text

    def test_an_unconfigured_provider_still_offers_the_assistant(
        self, client, as_user, assistant_on
    ):
        # The launcher still appears. The panel can say the assistant is not
        # configured, which is a better answer than a provider error on the
        # first question.
        from app.assistant import config_store

        config_store._cached = config_store.AssistantRuntimeConfig(api_key=None)
        config_store._cached_at = float("inf")
        as_user(roles=["doctor"])

        body = client.get(STATUS_URL).json()

        assert body["enabled"] is True
        assert body["provider_configured"] is False
