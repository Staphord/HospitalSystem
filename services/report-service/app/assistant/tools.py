from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field

from app.assistant.contracts import StrictModel
from app.assistant.flags import AssistantCapability
from app.assistant.permissions import TENANT_STAFF_ROLES, is_role_allowed
from app.assistant.retrieval import (
    MAX_BODY_CHARS_PER_ENTRY,
    MAX_ENTRIES_PER_RETRIEVAL,
    ContentEntry,
    RetrievalContext,
    retrieve,
)
from app.assistant.content import ContentKind

# The tool registry is the only way data may reach the model, and the server
# decides which tool runs. The model never selects a tool, never supplies tool
# arguments, and never sees a tool name, so there is no path by which model
# output or an injected instruction inside retrieved text can invoke anything.
#
# Every tool here is read-only over the repo-shipped operational content pack.
# No tool opens a database session, issues SQL, performs an HTTP request, or
# writes. That property is asserted by the tool registry tests.


def _clip(text: str, limit: int = MAX_BODY_CHARS_PER_ENTRY) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


class ListSupportedReportsParams(StrictModel):
    """Parameters for the report catalog tool."""

    query: str = Field(default="", max_length=2000)
    limit: int = Field(default=MAX_ENTRIES_PER_RETRIEVAL, ge=1, le=MAX_ENTRIES_PER_RETRIEVAL)


class SearchOperationalContentParams(StrictModel):
    """Parameters for the workflow, help, and policy tool."""

    query: str = Field(default="", max_length=2000)
    limit: int = Field(default=MAX_ENTRIES_PER_RETRIEVAL, ge=1, le=MAX_ENTRIES_PER_RETRIEVAL)


@dataclass(frozen=True)
class ToolResult:
    """The bounded, projected output of one tool run."""

    tool: str
    items: tuple[dict[str, Any], ...] = ()
    failed: bool = False
    sources: tuple[tuple[str, str], ...] = ()  # (label, version) pairs

    @property
    def is_empty(self) -> bool:
        return not self.items


def _project(entry: ContentEntry, exposed: frozenset[str]) -> dict[str, Any]:
    """Build a tool item containing exactly the allowlisted fields.

    Deny by default. Every value is taken from a named field, so a column or
    attribute added to ContentEntry later cannot appear here without a
    deliberate change to the tool's allowlist and to its test.
    """
    available: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "kind": entry.kind.value,
        "summary": _clip(entry.body),
        "body": _clip(entry.body),
        "required_role": entry.required_role,
        "location": entry.location,
        "version": entry.version,
    }
    item = {name: available[name] for name in sorted(exposed)}
    # Belt and braces: the projection may never widen beyond its allowlist. This
    # is a real check rather than an assert, so it survives python -O.
    if set(item) != set(exposed):
        raise RuntimeError("tool projection escaped its field allowlist")
    return item


@dataclass(frozen=True)
class AssistantTool:
    """A typed, read-only, permission-checked tool."""

    name: str
    description: str
    capability: AssistantCapability
    allowed_roles: frozenset[str]
    kinds: frozenset[ContentKind]
    exposed_fields: frozenset[str]
    params_model: type[StrictModel]
    source_kind: str

    def is_permitted(self, roles: frozenset[str], is_super_admin: bool = False) -> bool:
        """Endpoint-level permission check for this tool."""
        if is_super_admin:
            return False
        if not is_role_allowed(self.capability, roles, is_super_admin=is_super_admin):
            return False
        return bool(roles & self.allowed_roles)

    def run(self, context: RetrievalContext, params: StrictModel) -> ToolResult:
        """Run the tool. Never raises; a failure returns an empty safe result.

        The data-source-level permission check happens inside retrieve(), which
        re-applies tenant, role, department, version, effective date, and
        approval filters to every candidate entry.
        """
        try:
            query = str(getattr(params, "query", "") or "")
            limit = int(getattr(params, "limit", MAX_ENTRIES_PER_RETRIEVAL))
            entries = retrieve(context, query=query, kinds=self.kinds, limit=limit)
            items = tuple(_project(entry, self.exposed_fields) for entry in entries)
            sources = tuple((entry.title, entry.version) for entry in entries)
            return ToolResult(tool=self.name, items=items, sources=sources)
        except Exception:
            # Safe failure: no vendor payload, no database error, no stack trace
            # escapes a tool. The caller treats this as "no content available".
            return ToolResult(tool=self.name, failed=True)


LIST_SUPPORTED_REPORTS = AssistantTool(
    name="list_supported_reports",
    description=(
        "Describes which reports exist, who may run them, and where to find "
        "them. Returns no report figures."
    ),
    capability=AssistantCapability.OPERATIONAL_CHAT,
    allowed_roles=TENANT_STAFF_ROLES,
    kinds=frozenset({ContentKind.REPORT_CATALOG}),
    exposed_fields=frozenset(
        {"entry_id", "title", "summary", "required_role", "location", "version"}
    ),
    params_model=ListSupportedReportsParams,
    source_kind="report_catalog",
)

SEARCH_OPERATIONAL_CONTENT = AssistantTool(
    name="search_operational_content",
    description=(
        "Finds workflow navigation, help, and operational policy content for "
        "the caller's role and department."
    ),
    capability=AssistantCapability.OPERATIONAL_CHAT,
    allowed_roles=TENANT_STAFF_ROLES,
    kinds=frozenset({ContentKind.WORKFLOW, ContentKind.HELP, ContentKind.POLICY}),
    exposed_fields=frozenset(
        {"entry_id", "title", "kind", "body", "location", "version"}
    ),
    params_model=SearchOperationalContentParams,
    source_kind="operational_content",
)


TOOL_REGISTRY: dict[str, AssistantTool] = {
    LIST_SUPPORTED_REPORTS.name: LIST_SUPPORTED_REPORTS,
    SEARCH_OPERATIONAL_CONTENT.name: SEARCH_OPERATIONAL_CONTENT,
}


def get_tool(name: str) -> AssistantTool | None:
    """Look up a tool by name. Fail-closed on anything not in the allowlist."""
    if not name or not isinstance(name, str):
        return None
    return TOOL_REGISTRY.get(name)


def permitted_tools(
    roles: frozenset[str], is_super_admin: bool = False
) -> list[AssistantTool]:
    """Return the tools this caller may reach, in a stable order."""
    return [
        tool
        for _, tool in sorted(TOOL_REGISTRY.items())
        if tool.is_permitted(roles, is_super_admin=is_super_admin)
    ]
