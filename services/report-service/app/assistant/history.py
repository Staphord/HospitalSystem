"""Storage for assistant chat history.

Every function here takes the caller's tenant and actor_sub and filters on both.
That is the whole access control story for history, and it is deliberately kept
in one small module rather than spread across the service: a conversation id is
a value a browser can send, so it is never trusted on its own. An id belonging
to another staff member, another tenant, or nothing at all resolves to None and
is reported as not found, which is the same answer either way and so tells a
prober nothing.

Two bounds are enforced on write, from configuration:

* a ceiling on conversations per staff member, oldest dropped first, so an
  unattended panel cannot grow a tenant database without limit;
* a ceiling on messages in one thread, after which the thread stops accepting
  new messages and the next question starts a fresh one.

Nothing here writes to a log. The audit trail for a question is unchanged and
still records the actor, the capability, the outcome, and the content versions,
never the words.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.assistant import (
    AUTHOR_ASSISTANT,
    AUTHOR_USER,
    AssistantConversation,
    AssistantMessage,
)

# A title is a short label for a list, not a second copy of the question.
MAX_TITLE_CHARS = 120


def derive_title(question: str) -> str:
    """Build the list label for a new thread from its opening question.

    Server-side and one-way: the browser never supplies a title, so the list
    cannot become a route for text that did not come from a real question.
    Newlines are collapsed because a title is rendered on one line.
    """
    collapsed = " ".join((question or "").split())
    if not collapsed:
        return "New conversation"
    if len(collapsed) <= MAX_TITLE_CHARS:
        return collapsed

    # Cut on a word boundary where there is one close to the limit, so a title
    # does not end mid-word for the sake of a few characters.
    clipped = collapsed[: MAX_TITLE_CHARS - 1]
    spaced = clipped.rsplit(" ", 1)[0]
    if len(spaced) >= MAX_TITLE_CHARS - 30:
        clipped = spaced
    return clipped.rstrip() + "…"


def _max_conversations() -> int:
    return max(1, int(getattr(settings, "assistant_history_max_conversations", 50)))


def _max_messages() -> int:
    return max(2, int(getattr(settings, "assistant_history_max_messages", 200)))


async def list_conversations(
    db: AsyncSession, *, tenant_id: str, actor_sub: str
) -> list[AssistantConversation]:
    """The caller's own threads, most recently used first."""
    result = await db.execute(
        select(AssistantConversation)
        .where(
            AssistantConversation.tenant_id == tenant_id,
            AssistantConversation.actor_sub == actor_sub,
        )
        .order_by(AssistantConversation.last_message_at.desc())
        .limit(_max_conversations())
    )
    return list(result.scalars().all())


async def get_conversation(
    db: AsyncSession, *, conversation_id: uuid.UUID, tenant_id: str, actor_sub: str
) -> AssistantConversation | None:
    """Resolve one thread, or None if it is not this caller's.

    The ownership filter is part of the query rather than a check on a row that
    was already loaded, so another user's conversation is never read into memory
    at all.
    """
    result = await db.execute(
        select(AssistantConversation).where(
            AssistantConversation.conversation_id == conversation_id,
            AssistantConversation.tenant_id == tenant_id,
            AssistantConversation.actor_sub == actor_sub,
        )
    )
    return result.scalar_one_or_none()


async def get_messages(
    db: AsyncSession, *, conversation_id: uuid.UUID, tenant_id: str, actor_sub: str
) -> list[AssistantMessage]:
    """The messages of one thread, oldest first, scoped to its owner."""
    result = await db.execute(
        select(AssistantMessage)
        .where(
            AssistantMessage.conversation_id == conversation_id,
            AssistantMessage.tenant_id == tenant_id,
            AssistantMessage.actor_sub == actor_sub,
        )
        .order_by(AssistantMessage.sequence.asc(), AssistantMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def _message_count(db: AsyncSession, conversation_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(AssistantMessage)
        .where(AssistantMessage.conversation_id == conversation_id)
    )
    return int(result.scalar_one() or 0)


async def _prune_oldest(db: AsyncSession, *, tenant_id: str, actor_sub: str) -> None:
    """Drop this caller's oldest threads once they are over the ceiling.

    Oldest by last use, so the threads someone keeps returning to survive and
    the ones they abandoned are the ones that go.
    """
    keep = _max_conversations()
    result = await db.execute(
        select(AssistantConversation.conversation_id)
        .where(
            AssistantConversation.tenant_id == tenant_id,
            AssistantConversation.actor_sub == actor_sub,
        )
        .order_by(AssistantConversation.last_message_at.desc())
        .offset(keep)
    )
    stale = list(result.scalars().all())
    if not stale:
        return

    # Messages first: the in-memory SQLite the test suite uses does not enforce
    # the cascade the PostgreSQL foreign key declares unless it is asked to, so
    # orphans are removed explicitly rather than relying on a behaviour that
    # differs between the two engines.
    await db.execute(
        delete(AssistantMessage).where(AssistantMessage.conversation_id.in_(stale))
    )
    await db.execute(
        delete(AssistantConversation).where(
            AssistantConversation.conversation_id.in_(stale)
        )
    )


async def start_conversation(
    db: AsyncSession, *, tenant_id: str, actor_sub: str, question: str
) -> AssistantConversation:
    """Open a new thread, titled from its opening question."""
    now = datetime.now(timezone.utc)
    conversation = AssistantConversation(
        conversation_id=uuid.uuid4(),
        tenant_id=tenant_id,
        actor_sub=actor_sub,
        title=derive_title(question),
        message_count=0,
        created_at=now,
        last_message_at=now,
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def resolve_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID | None,
    tenant_id: str,
    actor_sub: str,
    question: str,
) -> AssistantConversation:
    """Continue the caller's thread, or start one.

    An id that does not resolve to this caller's own thread quietly starts a new
    one rather than raising: the person asked a question, and they get their
    answer stored somewhere sensible whatever their browser sent. A thread that
    has reached its ceiling also rolls into a new one instead of the question
    being refused.
    """
    if conversation_id is not None:
        existing = await get_conversation(
            db,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            actor_sub=actor_sub,
        )
        if existing is not None:
            count = await _message_count(db, existing.conversation_id)
            # Two rows per exchange, so the ceiling is checked with room for the
            # pair about to be written.
            if count + 2 <= _max_messages():
                return existing

    return await start_conversation(
        db, tenant_id=tenant_id, actor_sub=actor_sub, question=question
    )


async def record_exchange(
    db: AsyncSession,
    *,
    conversation: AssistantConversation,
    question: str,
    answer: str,
    answer_status: str,
    sources: list[dict],
    request_id: str,
) -> None:
    """Append one question and the answer it was given, and commit.

    The answer stored is the sanitized text that was actually shown on screen,
    never raw provider output, so reopening a thread cannot reintroduce anything
    the answer sanitizer already refused.
    """
    now = datetime.now(timezone.utc)
    position = int(conversation.message_count or 0)
    db.add(
        AssistantMessage(
            message_id=uuid.uuid4(),
            conversation_id=conversation.conversation_id,
            tenant_id=conversation.tenant_id,
            actor_sub=conversation.actor_sub,
            author=AUTHOR_USER,
            sequence=position + 1,
            body=question,
            answer_status=None,
            sources=[],
            request_id=request_id,
            created_at=now,
        )
    )
    db.add(
        AssistantMessage(
            message_id=uuid.uuid4(),
            conversation_id=conversation.conversation_id,
            tenant_id=conversation.tenant_id,
            actor_sub=conversation.actor_sub,
            author=AUTHOR_ASSISTANT,
            sequence=position + 2,
            body=answer,
            answer_status=answer_status,
            sources=sources,
            request_id=request_id,
            created_at=now,
        )
    )

    conversation.message_count = (conversation.message_count or 0) + 2
    conversation.last_message_at = now

    await _prune_oldest(
        db, tenant_id=conversation.tenant_id, actor_sub=conversation.actor_sub
    )
    await db.commit()


async def delete_conversation(
    db: AsyncSession, *, conversation_id: uuid.UUID, tenant_id: str, actor_sub: str
) -> bool:
    """Delete one of the caller's own threads. Returns whether one was removed."""
    conversation = await get_conversation(
        db, conversation_id=conversation_id, tenant_id=tenant_id, actor_sub=actor_sub
    )
    if conversation is None:
        return False

    await db.execute(
        delete(AssistantMessage).where(
            AssistantMessage.conversation_id == conversation_id
        )
    )
    await db.execute(
        delete(AssistantConversation).where(
            AssistantConversation.conversation_id == conversation_id,
            AssistantConversation.tenant_id == tenant_id,
            AssistantConversation.actor_sub == actor_sub,
        )
    )
    await db.commit()
    return True


async def delete_all_conversations(
    db: AsyncSession, *, tenant_id: str, actor_sub: str
) -> int:
    """Delete every thread this caller owns. Returns how many were removed.

    Scoped to the one caller on purpose. There is deliberately no "clear this
    hospital's history" here: no staff member should be able to erase a
    colleague's record from the assistant panel.
    """
    result = await db.execute(
        select(AssistantConversation.conversation_id).where(
            AssistantConversation.tenant_id == tenant_id,
            AssistantConversation.actor_sub == actor_sub,
        )
    )
    ids = list(result.scalars().all())
    if not ids:
        return 0

    await db.execute(
        delete(AssistantMessage).where(AssistantMessage.conversation_id.in_(ids))
    )
    await db.execute(
        delete(AssistantConversation).where(
            AssistantConversation.conversation_id.in_(ids)
        )
    )
    await db.commit()
    return len(ids)
