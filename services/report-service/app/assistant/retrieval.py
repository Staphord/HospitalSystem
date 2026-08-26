from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.assistant.content import (
    CONTENT_PACK_VERSION,
    OPERATIONAL_CONTENT,
    ROLE_DEPARTMENT,
    ApprovalState,
    ContentEntry,
    ContentKind,
)
from app.assistant.permissions import normalize_roles

# Hard ceiling on how much content may be assembled for one question. This
# bounds both the prompt sent to the provider and the work done per request.
MAX_ENTRIES_PER_RETRIEVAL = 6
MAX_BODY_CHARS_PER_ENTRY = 1200

_WORD = re.compile(r"[a-z0-9]+")

# Words too common in this domain to discriminate between entries.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "can", "do", "does", "for",
        "from", "get", "how", "i", "in", "is", "it", "me", "my", "of", "on",
        "or", "the", "to", "what", "when", "where", "which", "who", "why",
        "with", "you", "your", "hospital", "system", "patient", "patients",
    }
)


@dataclass(frozen=True)
class RetrievalContext:
    """Everything retrieval is allowed to filter on, all server-resolved.

    Built only from the verified token. A tenant, role, or department supplied
    in a request body, a prompt, or a model response is never accepted here.
    """

    tenant_id: str
    roles: frozenset[str]
    department: str | None
    today: date


def resolve_department(roles: frozenset[str]) -> str | None:
    """Resolve the caller's department from their verified roles.

    Keycloak tokens in this system carry no department claim, so the department
    is derived server-side from the role. A caller holding several roles is
    resolved to the first department in a stable order, so the result never
    depends on set iteration order.
    """
    for role in sorted(roles):
        department = ROLE_DEPARTMENT.get(role)
        if department:
            return department
    return None


def build_retrieval_context(
    tenant_id: str | None,
    roles: object,
    today: date | None = None,
) -> RetrievalContext | None:
    """Build a retrieval context, or None when it cannot be trusted.

    Fail-closed: without a tenant resolved from the token, and without at least
    one recognised role, no content may be retrieved at all.
    """
    if not tenant_id or not isinstance(tenant_id, str) or not tenant_id.strip():
        return None

    normalized = frozenset(normalize_roles(roles))  # type: ignore[arg-type]
    if not normalized:
        return None

    return RetrievalContext(
        tenant_id=tenant_id.strip(),
        roles=normalized,
        department=resolve_department(normalized),
        today=today or date.today(),
    )


def _version_key(version: str) -> tuple:
    parts = []
    for chunk in str(version).split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return tuple(parts)


def _is_visible(entry: ContentEntry, context: RetrievalContext) -> bool:
    """Apply every access filter to one entry. Deny by default."""
    # Approval state: drafts never reach a user or the model.
    if entry.approval_state is not ApprovalState.APPROVED:
        return False

    # Effective date: content that has not come into effect is not yet content.
    if entry.effective_from > context.today:
        return False

    # Tenant: an empty allowlist means the entry is not tenant-restricted.
    if entry.tenants and context.tenant_id not in entry.tenants:
        return False

    # Role: the entry must name at least one role the caller actually holds.
    if not (entry.roles & context.roles):
        return False

    # Department: an empty allowlist means the entry is not department-scoped.
    if entry.departments:
        if not context.department or context.department not in entry.departments:
            return False

    return True


def _current_versions(entries: list[ContentEntry]) -> list[ContentEntry]:
    """Keep only the highest effective version of each entry id."""
    latest: dict[str, ContentEntry] = {}
    for entry in entries:
        existing = latest.get(entry.entry_id)
        if existing is None or _version_key(entry.version) > _version_key(existing.version):
            latest[entry.entry_id] = entry
    return list(latest.values())


def _tokenize(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _score(entry: ContentEntry, terms: set[str]) -> int:
    if not terms:
        return 1
    title_terms = _tokenize(entry.title)
    body_terms = _tokenize(entry.body)
    # A title match is worth more than a body match.
    return 3 * len(terms & title_terms) + len(terms & body_terms)


def visible_entries(
    context: RetrievalContext,
    kinds: frozenset[ContentKind] | None = None,
) -> list[ContentEntry]:
    """Return every current, approved, in-effect entry this caller may see."""
    candidates = [e for e in OPERATIONAL_CONTENT if _is_visible(e, context)]
    if kinds is not None:
        candidates = [e for e in candidates if e.kind in kinds]
    return sorted(_current_versions(candidates), key=lambda e: e.entry_id)


def retrieve(
    context: RetrievalContext,
    query: str = "",
    kinds: frozenset[ContentKind] | None = None,
    limit: int = MAX_ENTRIES_PER_RETRIEVAL,
) -> list[ContentEntry]:
    """Return the most relevant entries this caller is permitted to see.

    Access filtering always runs before relevance ranking, so a low-scoring
    permitted entry can be dropped but a forbidden entry can never be promoted.
    """
    permitted = visible_entries(context, kinds)
    terms = _tokenize(query or "")

    scored = [(entry, _score(entry, terms)) for entry in permitted]
    matching = [(entry, score) for entry, score in scored if score > 0]

    matching.sort(key=lambda pair: (-pair[1], pair[0].entry_id))
    bounded = max(0, min(int(limit), MAX_ENTRIES_PER_RETRIEVAL))
    return [entry for entry, _ in matching[:bounded]]


def content_pack_version() -> str:
    """Return the version stamped on answers and audit records."""
    return CONTENT_PACK_VERSION
