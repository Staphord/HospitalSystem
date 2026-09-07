"""Tenant-database tables owned by the hospital assistant.

Assistant chat history: the questions a staff member asked and the answers they
were given, kept so they can reopen a thread later instead of asking again.

Two properties matter and are enforced everywhere these rows are touched:

* **Owned.** Every row carries the tenant and the actor_sub of the staff member
  who created it, and every read is filtered on both. History is personal; one
  staff member never sees another's, and a conversation id from another user's
  browser resolves to nothing rather than to someone else's thread.
* **Deletable.** Unlike the append-only clinical decision support audit tables,
  these are the user's own record and the user may delete them. Removing a
  conversation removes its messages with it. The audit trail is separate and is
  untouched by a deletion: it still records that a question was asked, by whom,
  and under which content versions, and it never held the words in the first
  place.

Nothing here is ever written to an application log.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

# JSONB in PostgreSQL, plain JSON everywhere else, so the in-memory SQLite the
# test suite uses builds the same schema.
JsonList = JSON().with_variant(JSONB(), "postgresql")

from app.db.base import Base

# Authors of a stored message. The caller's hospital role is deliberately not
# stored: it is resolved from the token on every request and would only go stale
# here.
AUTHOR_USER = "user"
AUTHOR_ASSISTANT = "assistant"


class AssistantConversation(Base):
    """One conversation thread belonging to one staff member."""

    __tablename__ = "assistant_conversations"

    conversation_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    actor_sub = Column(String(255), nullable=False, index=True)

    # Derived on the server from the opening question. Never accepted from the
    # browser, so a title cannot carry markup or another user's text.
    title = Column(String(120), nullable=False)
    message_count = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_message_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    messages = relationship(
        "AssistantMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AssistantMessage(Base):
    """One question or one answer inside a conversation.

    The answer text is stored as the sanitized text that was actually shown on
    screen, not the raw provider output, so nothing can be reintroduced later
    that the answer sanitizer already refused.
    """

    __tablename__ = "assistant_messages"

    message_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(64), nullable=False, index=True)
    actor_sub = Column(String(255), nullable=False, index=True)

    author = Column(String(16), nullable=False)
    # Position in the thread. A question and its answer are written in one
    # transaction and share a timestamp to the microsecond, so time alone does
    # not order them; this does.
    sequence = Column(Integer, nullable=False, default=0)
    body = Column(Text, nullable=False)

    # Set on an assistant message only.
    answer_status = Column(String(20), nullable=True)
    sources = Column(JsonList, nullable=False, default=list)
    request_id = Column(String(64), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    conversation = relationship("AssistantConversation", back_populates="messages")
