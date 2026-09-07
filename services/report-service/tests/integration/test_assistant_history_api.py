"""Endpoint-level tests for assistant chat history.

These exercise the real app, the real routes, and the real gates. Two things are
substituted: the token dependency, because Keycloak is not reachable from a
test, and the tenant session, which is pointed at an in-memory SQLite database
built from the same declarative metadata as the PostgreSQL migration. The model
provider is stubbed so no paid outbound call is ever made.
"""

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.assistant import service as svc
from app.assistant.provider import ProviderResponse
from app.assistant.router import get_history_db
from app.core.tenant_auth import get_current_tenant
from app.db.base import Base
from app.main import app

CHAT_URL = "/api/v1/reports/assistant/chat"
CONVERSATIONS_URL = "/api/v1/reports/assistant/conversations"

QUESTION = {"question": "How do I register a new patient?"}
SECOND_QUESTION = {"question": "What reports can I run?"}


@dataclass
class FakeTenantContext:
    tenant_id: str | None = "hosp-aaaa1111"
    user_sub: str = "user-alice"
    preferred_username: str | None = "alice"
    email: str | None = None
    roles: list = field(default_factory=lambda: ["receptionist"])
    is_super_admin: bool = False
    scope: str = "full"
    raw_token: dict = field(default_factory=dict)


class StubProvider:
    name = "stub"

    def describe(self):
        return {"provider": "stub", "model_version": "stub-1"}

    async def complete(self, request):
        return ProviderResponse(
            text="Open Reception, then Register patient.", model_version="stub-1"
        )


@pytest.fixture
def tenant_engine():
    """One SQLite database shared by every request inside a test."""
    engine = create_async_engine("sqlite+aiosqlite:///file:history?mode=memory&cache=shared&uri=true")
    yield engine


@pytest.fixture
def as_user(monkeypatch, tenant_engine):
    """Sign in as a chosen role, with chat and history both switched on."""
    monkeypatch.setattr(
        svc.settings, "assistant_operational_chat_enabled", True, raising=False
    )
    monkeypatch.setattr(
        svc.settings, "assistant_chat_history_enabled", True, raising=False
    )
    monkeypatch.setattr(svc, "get_provider", lambda: StubProvider())

    factory = async_sessionmaker(
        bind=tenant_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _tenant_db():
        async with factory() as session:
            yield session

    async def _prepare():
        async with tenant_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_prepare())

    def _sign_in(**kwargs):
        ctx = FakeTenantContext(**kwargs)
        app.dependency_overrides[get_current_tenant] = lambda: ctx
        app.dependency_overrides[get_history_db] = _tenant_db
        return ctx

    yield _sign_in
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Give each test its own rate-limit budget."""
    from app.core.limiter import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


class TestAskingStoresTheExchange:
    def test_an_answer_names_the_conversation_it_was_stored_in(
        self, client, as_user
    ):
        as_user()
        body = client.post(CHAT_URL, json=QUESTION).json()

        assert body["conversation_id"]

    def test_the_conversation_appears_in_the_list(self, client, as_user):
        as_user()
        client.post(CHAT_URL, json=QUESTION)

        listed = client.get(CONVERSATIONS_URL).json()["conversations"]

        assert len(listed) == 1
        assert listed[0]["title"] == QUESTION["question"]
        assert listed[0]["message_count"] == 2

    def test_reopening_returns_the_question_and_the_answer_in_order(
        self, client, as_user
    ):
        as_user()
        conversation_id = client.post(CHAT_URL, json=QUESTION).json()["conversation_id"]

        body = client.get(f"{CONVERSATIONS_URL}/{conversation_id}").json()

        assert [m["author"] for m in body["messages"]] == ["user", "assistant"]
        assert body["messages"][0]["body"] == QUESTION["question"]
        assert body["messages"][1]["answer_status"] == "supported"

    def test_a_second_question_continues_the_same_thread(self, client, as_user):
        as_user()
        first = client.post(CHAT_URL, json=QUESTION).json()["conversation_id"]

        second = client.post(
            CHAT_URL, json={**SECOND_QUESTION, "conversation_id": first}
        ).json()["conversation_id"]

        assert second == first
        listed = client.get(CONVERSATIONS_URL).json()["conversations"]
        assert len(listed) == 1
        assert listed[0]["message_count"] == 4

    def test_omitting_the_conversation_id_starts_a_new_thread(self, client, as_user):
        as_user()
        first = client.post(CHAT_URL, json=QUESTION).json()["conversation_id"]
        second = client.post(CHAT_URL, json=SECOND_QUESTION).json()["conversation_id"]

        assert first != second
        assert len(client.get(CONVERSATIONS_URL).json()["conversations"]) == 2


class TestHistoryIsPersonal:
    def test_a_colleague_sees_none_of_it(self, client, as_user):
        as_user(user_sub="user-alice")
        client.post(CHAT_URL, json=QUESTION)

        as_user(user_sub="user-bob")

        assert client.get(CONVERSATIONS_URL).json()["conversations"] == []

    def test_a_colleague_cannot_reopen_the_thread(self, client, as_user):
        as_user(user_sub="user-alice")
        conversation_id = client.post(CHAT_URL, json=QUESTION).json()["conversation_id"]

        as_user(user_sub="user-bob")
        response = client.get(f"{CONVERSATIONS_URL}/{conversation_id}")

        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_REQUEST"

    def test_a_colleague_cannot_delete_the_thread(self, client, as_user):
        as_user(user_sub="user-alice")
        conversation_id = client.post(CHAT_URL, json=QUESTION).json()["conversation_id"]

        as_user(user_sub="user-bob")
        assert client.delete(f"{CONVERSATIONS_URL}/{conversation_id}").status_code == 400

        as_user(user_sub="user-alice")
        assert client.get(f"{CONVERSATIONS_URL}/{conversation_id}").status_code == 200

    def test_another_tenant_cannot_reopen_the_thread(self, client, as_user):
        as_user(tenant_id="hosp-aaaa1111")
        conversation_id = client.post(CHAT_URL, json=QUESTION).json()["conversation_id"]

        as_user(tenant_id="hosp-bbbb2222")

        assert client.get(f"{CONVERSATIONS_URL}/{conversation_id}").status_code == 400


class TestDeletion:
    def test_a_staff_member_can_delete_one_conversation(self, client, as_user):
        as_user()
        conversation_id = client.post(CHAT_URL, json=QUESTION).json()["conversation_id"]

        assert client.delete(f"{CONVERSATIONS_URL}/{conversation_id}").status_code == 204
        assert client.get(CONVERSATIONS_URL).json()["conversations"] == []

    def test_a_staff_member_can_clear_everything_they_own(self, client, as_user):
        as_user()
        client.post(CHAT_URL, json=QUESTION)
        client.post(CHAT_URL, json=SECOND_QUESTION)

        assert client.delete(CONVERSATIONS_URL).status_code == 204
        assert client.get(CONVERSATIONS_URL).json()["conversations"] == []

    def test_clearing_leaves_a_colleagues_history_alone(self, client, as_user):
        as_user(user_sub="user-bob")
        client.post(CHAT_URL, json=QUESTION)

        as_user(user_sub="user-alice")
        client.post(CHAT_URL, json=SECOND_QUESTION)
        client.delete(CONVERSATIONS_URL)

        as_user(user_sub="user-bob")
        assert len(client.get(CONVERSATIONS_URL).json()["conversations"]) == 1

    def test_deleting_a_conversation_that_is_not_there_is_refused(
        self, client, as_user
    ):
        as_user()
        missing = "11111111-2222-4333-8444-555555555555"

        assert client.delete(f"{CONVERSATIONS_URL}/{missing}").status_code == 400


class TestTheCapabilityIsSwitchedIndependently:
    def test_history_off_leaves_chat_working_and_stores_nothing(
        self, client, as_user, monkeypatch
    ):
        as_user()
        monkeypatch.setattr(
            svc.settings, "assistant_chat_history_enabled", False, raising=False
        )

        body = client.post(CHAT_URL, json=QUESTION).json()

        assert body["answer"]
        assert body["conversation_id"] is None

    def test_history_off_hides_the_history_routes_entirely(
        self, client, as_user, monkeypatch
    ):
        as_user()
        monkeypatch.setattr(
            svc.settings, "assistant_chat_history_enabled", False, raising=False
        )

        # 404, the same answer as a capability that does not exist, so an
        # operator pulling the switch does not advertise that it is there.
        assert client.get(CONVERSATIONS_URL).status_code == 404

    def test_a_super_admin_is_refused_history(self, client, as_user):
        as_user(roles=["super_admin"], is_super_admin=True)

        assert client.get(CONVERSATIONS_URL).status_code == 403

    def test_a_read_only_session_is_refused_history(self, client, as_user):
        as_user(scope="readonly")

        assert client.get(CONVERSATIONS_URL).status_code == 403


class TestTheStoredConversationCarriesOnlyContractFields:
    def test_a_listed_conversation_exposes_no_owner_or_tenant(self, client, as_user):
        as_user()
        client.post(CHAT_URL, json=QUESTION)

        listed = client.get(CONVERSATIONS_URL).json()["conversations"][0]

        assert set(listed) == {
            "conversation_id",
            "title",
            "message_count",
            "created_at",
            "last_message_at",
        }

    def test_a_stored_message_exposes_no_owner_or_tenant(self, client, as_user):
        as_user()
        conversation_id = client.post(CHAT_URL, json=QUESTION).json()["conversation_id"]

        message = client.get(f"{CONVERSATIONS_URL}/{conversation_id}").json()["messages"][0]

        assert set(message) == {
            "message_id",
            "author",
            "body",
            "answer_status",
            "sources",
            "request_id",
            "created_at",
        }


class TestTheMigrationHasNotBeenRunYet:
    """Switching the flag on before migration 0028 must not break anything.

    The tables are dropped here to stand in for a tenant database that has not
    had the history migration applied. Asking questions has to keep working, and
    the history panel has to say it is unavailable rather than return a server
    error.
    """

    @pytest.fixture
    def without_history_tables(self, tenant_engine, as_user):
        as_user()

        async def _drop():
            async with tenant_engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)

        import asyncio

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_drop())

    def test_a_question_is_still_answered(self, client, without_history_tables):
        body = client.post(CHAT_URL, json=QUESTION)

        assert body.status_code == 200
        assert body.json()["answer"]

    def test_the_answer_reports_no_stored_conversation(
        self, client, without_history_tables
    ):
        assert client.post(CHAT_URL, json=QUESTION).json()["conversation_id"] is None

    def test_listing_reports_history_unavailable_rather_than_a_server_error(
        self, client, without_history_tables
    ):
        response = client.get(CONVERSATIONS_URL)

        assert response.status_code == 503
        assert response.json()["code"] == "PROVIDER_UNAVAILABLE"

    def test_reopening_reports_history_unavailable(self, client, without_history_tables):
        missing = "11111111-2222-4333-8444-555555555555"

        assert client.get(f"{CONVERSATIONS_URL}/{missing}").status_code == 503

    def test_deleting_reports_history_unavailable(self, client, without_history_tables):
        missing = "11111111-2222-4333-8444-555555555555"

        assert client.delete(f"{CONVERSATIONS_URL}/{missing}").status_code == 503

    def test_clearing_reports_history_unavailable(self, client, without_history_tables):
        assert client.delete(CONVERSATIONS_URL).status_code == 503
