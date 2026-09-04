"""Storage rules for assistant chat history.

The properties under test are the ones a hospital would ask about: history is
personal, it is deletable by the person who owns it, and it cannot grow without
limit. Everything runs against a real SQLite database built from the same
declarative metadata as the PostgreSQL migration, so these are not mock
assertions about calls that were made.
"""

import uuid

import pytest

from app.assistant import history
from app.models.assistant import AssistantConversation, AssistantMessage

TENANT = "hosp-aaaa1111"
OTHER_TENANT = "hosp-bbbb2222"
ALICE = "user-alice"
BOB = "user-bob"

QUESTION = "How do I register a new patient?"
ANSWER = "Open Reception, then Register patient."
SOURCES = [{"label": "Reception workflow", "kind": "workflow", "version": "1.0"}]


async def _record(db, *, tenant=TENANT, actor=ALICE, question=QUESTION, answer=ANSWER):
    conversation = await history.resolve_conversation(
        db,
        conversation_id=None,
        tenant_id=tenant,
        actor_sub=actor,
        question=question,
    )
    await history.record_exchange(
        db,
        conversation=conversation,
        question=question,
        answer=answer,
        answer_status="supported",
        sources=SOURCES,
        request_id="req-1",
    )
    return conversation


class TestTitlesAreDerivedOnTheServer:
    def test_the_opening_question_becomes_the_title(self):
        assert history.derive_title(QUESTION) == QUESTION

    def test_a_long_question_is_shortened_for_the_list(self):
        title = history.derive_title("word " * 200)

        assert len(title) <= history.MAX_TITLE_CHARS
        assert title.endswith("…")

    def test_newlines_are_collapsed_so_a_title_stays_one_line(self):
        assert history.derive_title("How do I\n\nregister\ta patient?") == (
            "How do I register a patient?"
        )

    def test_an_empty_question_still_produces_a_usable_label(self):
        assert history.derive_title("   ") == "New conversation"


class TestAnExchangeIsStored:
    async def test_a_question_and_its_answer_are_both_kept(self, tenant_db):
        conversation = await _record(tenant_db)

        messages = await history.get_messages(
            tenant_db,
            conversation_id=conversation.conversation_id,
            tenant_id=TENANT,
            actor_sub=ALICE,
        )

        assert [m.author for m in messages] == ["user", "assistant"]
        assert messages[0].body == QUESTION
        assert messages[1].body == ANSWER
        assert messages[1].answer_status == "supported"
        assert messages[1].sources == SOURCES

    async def test_the_stored_question_carries_no_answer_fields(self, tenant_db):
        conversation = await _record(tenant_db)

        messages = await history.get_messages(
            tenant_db,
            conversation_id=conversation.conversation_id,
            tenant_id=TENANT,
            actor_sub=ALICE,
        )

        assert messages[0].answer_status is None
        assert messages[0].sources == []

    async def test_a_second_question_continues_the_same_thread(self, tenant_db):
        first = await _record(tenant_db)

        again = await history.resolve_conversation(
            tenant_db,
            conversation_id=first.conversation_id,
            tenant_id=TENANT,
            actor_sub=ALICE,
            question="And how do I find them again?",
        )
        await history.record_exchange(
            tenant_db,
            conversation=again,
            question="And how do I find them again?",
            answer="Open Reception, then Search patient.",
            answer_status="supported",
            sources=[],
            request_id="req-2",
        )

        assert again.conversation_id == first.conversation_id
        assert again.message_count == 4
        conversations = await history.list_conversations(
            tenant_db, tenant_id=TENANT, actor_sub=ALICE
        )
        assert len(conversations) == 1

    async def test_the_list_is_ordered_by_most_recently_used(self, tenant_db):
        older = await _record(tenant_db, question="First question")
        newer = await _record(tenant_db, question="Second question")

        conversations = await history.list_conversations(
            tenant_db, tenant_id=TENANT, actor_sub=ALICE
        )

        assert [c.conversation_id for c in conversations] == [
            newer.conversation_id,
            older.conversation_id,
        ]


class TestHistoryIsPersonal:
    """A conversation id is a value a browser sends, so it is never trusted."""

    async def test_another_staff_member_cannot_open_the_thread(self, tenant_db):
        conversation = await _record(tenant_db, actor=ALICE)

        assert (
            await history.get_conversation(
                tenant_db,
                conversation_id=conversation.conversation_id,
                tenant_id=TENANT,
                actor_sub=BOB,
            )
            is None
        )

    async def test_another_staff_member_reads_none_of_the_messages(self, tenant_db):
        conversation = await _record(tenant_db, actor=ALICE)

        assert (
            await history.get_messages(
                tenant_db,
                conversation_id=conversation.conversation_id,
                tenant_id=TENANT,
                actor_sub=BOB,
            )
            == []
        )

    async def test_another_tenant_cannot_open_the_thread(self, tenant_db):
        conversation = await _record(tenant_db, tenant=TENANT, actor=ALICE)

        assert (
            await history.get_conversation(
                tenant_db,
                conversation_id=conversation.conversation_id,
                tenant_id=OTHER_TENANT,
                actor_sub=ALICE,
            )
            is None
        )

    async def test_the_list_shows_only_the_callers_own_threads(self, tenant_db):
        mine = await _record(tenant_db, actor=ALICE)
        await _record(tenant_db, actor=BOB)

        conversations = await history.list_conversations(
            tenant_db, tenant_id=TENANT, actor_sub=ALICE
        )

        assert [c.conversation_id for c in conversations] == [mine.conversation_id]

    async def test_another_staff_member_cannot_delete_the_thread(self, tenant_db):
        conversation = await _record(tenant_db, actor=ALICE)

        removed = await history.delete_conversation(
            tenant_db,
            conversation_id=conversation.conversation_id,
            tenant_id=TENANT,
            actor_sub=BOB,
        )

        assert removed is False
        assert (
            await history.get_conversation(
                tenant_db,
                conversation_id=conversation.conversation_id,
                tenant_id=TENANT,
                actor_sub=ALICE,
            )
            is not None
        )

    async def test_an_id_that_belongs_to_no_one_resolves_to_nothing(self, tenant_db):
        assert (
            await history.get_conversation(
                tenant_db,
                conversation_id=uuid.uuid4(),
                tenant_id=TENANT,
                actor_sub=ALICE,
            )
            is None
        )

    async def test_an_unowned_id_starts_a_new_thread_rather_than_joining_one(
        self, tenant_db
    ):
        """A question is still answered and still stored, in the asker's own thread."""
        theirs = await _record(tenant_db, actor=BOB)

        mine = await history.resolve_conversation(
            tenant_db,
            conversation_id=theirs.conversation_id,
            tenant_id=TENANT,
            actor_sub=ALICE,
            question=QUESTION,
        )

        assert mine.conversation_id != theirs.conversation_id
        assert mine.actor_sub == ALICE


class TestDeletion:
    async def test_deleting_a_thread_takes_its_messages_with_it(self, tenant_db):
        conversation = await _record(tenant_db)

        assert await history.delete_conversation(
            tenant_db,
            conversation_id=conversation.conversation_id,
            tenant_id=TENANT,
            actor_sub=ALICE,
        )

        assert (
            await history.get_messages(
                tenant_db,
                conversation_id=conversation.conversation_id,
                tenant_id=TENANT,
                actor_sub=ALICE,
            )
            == []
        )

    async def test_clearing_removes_every_thread_the_caller_owns(self, tenant_db):
        await _record(tenant_db, actor=ALICE, question="First")
        await _record(tenant_db, actor=ALICE, question="Second")

        assert (
            await history.delete_all_conversations(
                tenant_db, tenant_id=TENANT, actor_sub=ALICE
            )
            == 2
        )
        assert (
            await history.list_conversations(
                tenant_db, tenant_id=TENANT, actor_sub=ALICE
            )
            == []
        )

    async def test_clearing_leaves_a_colleagues_history_alone(self, tenant_db):
        await _record(tenant_db, actor=ALICE)
        theirs = await _record(tenant_db, actor=BOB)

        await history.delete_all_conversations(
            tenant_db, tenant_id=TENANT, actor_sub=ALICE
        )

        remaining = await history.list_conversations(
            tenant_db, tenant_id=TENANT, actor_sub=BOB
        )
        assert [c.conversation_id for c in remaining] == [theirs.conversation_id]


class TestHistoryCannotGrowWithoutLimit:
    async def test_the_oldest_thread_is_dropped_at_the_ceiling(
        self, tenant_db, monkeypatch
    ):
        monkeypatch.setattr(
            history.settings, "assistant_history_max_conversations", 3, raising=False
        )

        created = []
        for index in range(5):
            created.append(await _record(tenant_db, question=f"Question {index}"))

        surviving = {
            c.conversation_id
            for c in await history.list_conversations(
                tenant_db, tenant_id=TENANT, actor_sub=ALICE
            )
        }

        assert len(surviving) == 3
        assert created[0].conversation_id not in surviving
        assert created[-1].conversation_id in surviving

    async def test_a_dropped_thread_leaves_no_orphaned_messages(
        self, tenant_db, monkeypatch
    ):
        monkeypatch.setattr(
            history.settings, "assistant_history_max_conversations", 1, raising=False
        )

        dropped = await _record(tenant_db, question="First")
        await _record(tenant_db, question="Second")

        from sqlalchemy import func, select

        remaining = await tenant_db.execute(
            select(func.count())
            .select_from(AssistantMessage)
            .where(AssistantMessage.conversation_id == dropped.conversation_id)
        )
        assert remaining.scalar_one() == 0

    async def test_a_full_thread_rolls_into_a_new_one(self, tenant_db, monkeypatch):
        """The question is still answered; it just starts a fresh thread."""
        monkeypatch.setattr(
            history.settings, "assistant_history_max_messages", 2, raising=False
        )

        first = await _record(tenant_db)

        next_thread = await history.resolve_conversation(
            tenant_db,
            conversation_id=first.conversation_id,
            tenant_id=TENANT,
            actor_sub=ALICE,
            question="One more thing",
        )

        assert next_thread.conversation_id != first.conversation_id


class TestTheStoreKeepsWhatTheMigrationDeclares:
    async def test_a_conversation_records_its_owner_and_tenant(self, tenant_db):
        conversation = await _record(tenant_db)

        stored = await tenant_db.get(
            AssistantConversation, conversation.conversation_id
        )
        assert stored.tenant_id == TENANT
        assert stored.actor_sub == ALICE
        assert stored.message_count == 2

    @pytest.mark.parametrize("attribute", ["tenant_id", "actor_sub"])
    async def test_every_message_carries_the_owner_scope_it_is_queried_by(
        self, tenant_db, attribute
    ):
        conversation = await _record(tenant_db)

        messages = await history.get_messages(
            tenant_db,
            conversation_id=conversation.conversation_id,
            tenant_id=TENANT,
            actor_sub=ALICE,
        )

        assert all(getattr(message, attribute) for message in messages)
