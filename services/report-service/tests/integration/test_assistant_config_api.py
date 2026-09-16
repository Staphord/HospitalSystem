"""The assistant configuration routes.

Platform scope. These set the model provider and the request bounds for every
hospital at once, so the thing worth testing hardest is who is refused: a
hospital administrator is senior inside one tenant and has no standing here at
all.

The other guarantee is that the API key crosses this boundary in one direction
only. It goes in on a save and never comes back out - not in a read, not in the
response to the save that set it, and not in an audit record.
"""

import pytest
from fastapi.testclient import TestClient

from app.assistant import config_store
from app.core.tenant_auth import get_current_tenant
from app.main import app

CONFIG_URL = "/api/v1/reports/admin/assistant-config"
TEST_URL = f"{CONFIG_URL}/test"


class FakeTenantContext:
    def __init__(
        self,
        roles=("hospital_admin",),
        is_super_admin=False,
        tenant_id="hosp-1",
        scope="full",
    ):
        self.tenant_id = tenant_id
        self.user_sub = "user-1"
        self.preferred_username = "someone"
        self.email = None
        self.roles = list(roles)
        self.is_super_admin = is_super_admin
        self.scope = scope
        self.raw_token = {}


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from app.core.limiter import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sign_in():
    def _sign_in(**kwargs):
        ctx = FakeTenantContext(**kwargs)
        app.dependency_overrides[get_current_tenant] = lambda: ctx
        return ctx

    yield _sign_in
    app.dependency_overrides.clear()


@pytest.fixture
def stored_config(monkeypatch):
    """A configuration in force, with saves captured rather than written."""
    saved = []

    def _save(values, *, actor):
        saved.append((values, actor))
        return config_store.AssistantRuntimeConfig(
            api_key="gsk_stored_value_9999", is_stored=True, updated_by=actor
        )

    monkeypatch.setattr(
        "app.api.v1.admin.assistant_config.save_config", _save, raising=True
    )
    monkeypatch.setattr(
        "app.api.v1.admin.assistant_config.get_config",
        lambda **_: config_store.AssistantRuntimeConfig(
            api_key="gsk_stored_value_9999", is_stored=True
        ),
        raising=True,
    )
    return saved


class TestOnlyThePlatformSuperAdminReachesIt:
    def test_a_hospital_admin_is_refused(self, client, sign_in, stored_config):
        # Administrative seniority inside one hospital is not standing to
        # reconfigure every hospital's assistant.
        sign_in(roles=["hospital_admin"])

        assert client.get(CONFIG_URL).status_code == 403

    @pytest.mark.parametrize(
        "role", ["doctor", "pharmacist", "receptionist", "cashier", "ward_nurse"]
    )
    def test_every_tenant_role_is_refused(self, client, sign_in, stored_config, role):
        sign_in(roles=[role])

        assert client.get(CONFIG_URL).status_code == 403

    def test_a_super_admin_is_admitted(self, client, sign_in, stored_config):
        sign_in(roles=["super_admin"], is_super_admin=True, tenant_id=None)

        assert client.get(CONFIG_URL).status_code == 200

    def test_a_read_only_super_admin_session_is_refused(
        self, client, sign_in, stored_config
    ):
        sign_in(roles=["super_admin"], is_super_admin=True, scope="readonly")

        assert client.get(CONFIG_URL).status_code == 403

    def test_a_tenant_role_cannot_write_either(self, client, sign_in, stored_config):
        sign_in(roles=["hospital_admin"])

        response = client.put(CONFIG_URL, json={"max_question_chars": 500})

        assert response.status_code == 403
        assert stored_config == []


class TestTheKeyOnlyTravelsInwards:
    def test_a_read_never_returns_the_key(self, client, sign_in, stored_config):
        sign_in(roles=["super_admin"], is_super_admin=True)

        body = client.get(CONFIG_URL).text

        assert "gsk_stored_value_9999" not in body

    def test_a_read_reports_presence_and_a_mask(self, client, sign_in, stored_config):
        sign_in(roles=["super_admin"], is_super_admin=True)

        body = client.get(CONFIG_URL).json()

        assert body["api_key_set"] is True
        assert body["api_key_hint"] == "****9999"

    def test_the_save_response_does_not_echo_the_key(
        self, client, sign_in, stored_config
    ):
        sign_in(roles=["super_admin"], is_super_admin=True)

        response = client.put(CONFIG_URL, json={"api_key": "gsk_brand_new_key"})

        assert response.status_code == 200
        assert "gsk_brand_new_key" not in response.text

    def test_omitting_the_key_leaves_the_stored_one_alone(
        self, client, sign_in, stored_config
    ):
        """The page renders a mask it never received, so it cannot send the key
        back unchanged. Omission has to mean "keep it"."""
        sign_in(roles=["super_admin"], is_super_admin=True)

        client.put(CONFIG_URL, json={"max_question_chars": 500})

        values, _ = stored_config[0]
        assert "api_key" not in values
        assert "clear_api_key" not in values

    def test_an_empty_key_is_not_treated_as_a_clear(
        self, client, sign_in, stored_config
    ):
        # An untouched form field is an empty string. Wiping the platform's
        # credential must take saying so.
        sign_in(roles=["super_admin"], is_super_admin=True)

        client.put(CONFIG_URL, json={"api_key": "   ", "max_question_chars": 500})

        values, _ = stored_config[0]
        assert "api_key" not in values
        assert "clear_api_key" not in values

    def test_clearing_takes_the_explicit_flag(self, client, sign_in, stored_config):
        sign_in(roles=["super_admin"], is_super_admin=True)

        client.put(CONFIG_URL, json={"clear_api_key": True})

        values, _ = stored_config[0]
        assert values["clear_api_key"] is True


class TestValuesAreCheckedBeforeTheyAreStored:
    def test_a_timeout_past_the_gateway_ceiling_is_refused(
        self, client, sign_in, stored_config
    ):
        sign_in(roles=["super_admin"], is_super_admin=True)

        response = client.put(CONFIG_URL, json={"request_timeout_seconds": 120})

        # Refused, not silently clamped: somebody setting 120 should be told it
        # cannot exceed 28 rather than saving successfully and getting 28.
        assert response.status_code == 400
        assert stored_config == []

    def test_an_unapproved_model_is_refused(self, client, sign_in, stored_config):
        sign_in(roles=["super_admin"], is_super_admin=True)

        response = client.put(CONFIG_URL, json={"model": "groq/compound-beta"})

        assert response.status_code == 422
        assert stored_config == []

    def test_an_unknown_provider_is_refused(self, client, sign_in, stored_config):
        sign_in(roles=["super_admin"], is_super_admin=True)

        response = client.put(CONFIG_URL, json={"provider": "some-other-vendor"})

        assert response.status_code == 422
        assert stored_config == []

    def test_a_plaintext_base_url_is_refused(self, client, sign_in, stored_config):
        sign_in(roles=["super_admin"], is_super_admin=True)

        response = client.put(CONFIG_URL, json={"base_url": "http://api.example.com"})

        assert response.status_code == 422
        assert stored_config == []

    def test_a_value_inside_its_range_is_stored(self, client, sign_in, stored_config):
        sign_in(roles=["super_admin"], is_super_admin=True)

        response = client.put(CONFIG_URL, json={"request_timeout_seconds": 25})

        assert response.status_code == 200
        values, actor = stored_config[0]
        assert values["request_timeout_seconds"] == 25
        assert actor == "someone"


class TestTheFormIsServedWithItsOwnLimits:
    def test_the_read_carries_the_ranges_the_server_enforces(
        self, client, sign_in, stored_config
    ):
        # So the form can enforce the same limits the API does, rather than
        # guessing them or discovering them on save.
        sign_in(roles=["super_admin"], is_super_admin=True)

        bounds = client.get(CONFIG_URL).json()["bounds"]

        assert bounds["request_timeout_seconds"]["maximum"] < 30
        assert set(bounds) == set(config_store.BOUNDS)

    def test_the_read_carries_the_selectable_models(
        self, client, sign_in, stored_config
    ):
        sign_in(roles=["super_admin"], is_super_admin=True)

        body = client.get(CONFIG_URL).json()

        assert body["chat_models"]
        assert body["transcription_models"]
        assert all("note" in m for m in body["chat_models"])

    def test_the_read_says_whether_the_deployment_switch_is_on(
        self, client, sign_in, stored_config, monkeypatch
    ):
        # Without this the portal lets someone configure a model perfectly and
        # watch nothing happen.
        from app.core.config import settings

        monkeypatch.setattr(settings, "assistant_operational_chat_enabled", False)
        sign_in(roles=["super_admin"], is_super_admin=True)

        assert client.get(CONFIG_URL).json()["assistant_enabled"] is False

        monkeypatch.setattr(settings, "assistant_operational_chat_enabled", True)

        assert client.get(CONFIG_URL).json()["assistant_enabled"] is True


class TestTheProviderCheck:
    def test_it_reports_when_no_key_is_set(self, client, sign_in, monkeypatch):
        monkeypatch.setattr(
            "app.api.v1.admin.assistant_config.get_config",
            lambda **_: config_store.AssistantRuntimeConfig(api_key=None),
            raising=True,
        )
        sign_in(roles=["super_admin"], is_super_admin=True)

        body = client.post(TEST_URL).json()

        assert body["ok"] is False
        assert "no api key" in body["message"].lower()

    def test_a_tenant_role_cannot_run_it(self, client, sign_in, stored_config):
        sign_in(roles=["hospital_admin"])

        assert client.post(TEST_URL).status_code == 403
