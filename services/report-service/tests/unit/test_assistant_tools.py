import inspect
from datetime import date

import pytest
from pydantic import ValidationError

from app.assistant import tools as tools_mod
from app.assistant.retrieval import build_retrieval_context
from app.assistant.tools import (
    LIST_SUPPORTED_REPORTS,
    SEARCH_OPERATIONAL_CONTENT,
    TOOL_REGISTRY,
    get_tool,
    permitted_tools,
)

TODAY = date(2026, 8, 25)


def ctx(tenant="hosp-aaaa1111", roles=("hospital_admin",)):
    built = build_retrieval_context(tenant, list(roles), today=TODAY)
    assert built is not None
    return built


# ---------------------------------------------------------------------------
# The allowlist guard.
#
# These two sets are the complete list of fields that may ever reach the model
# or the browser from each tool. If a field is added, removed, or renamed, this
# test fails and the change has to be re-approved. That is the point: a deny
# list would silently expose whatever a future developer adds.
# ---------------------------------------------------------------------------

EXPECTED_EXPOSED_FIELDS = {
    "list_supported_reports": {
        "entry_id",
        "title",
        "summary",
        "required_role",
        "location",
        "version",
    },
    "search_operational_content": {
        "entry_id",
        "title",
        "kind",
        "body",
        "location",
        "version",
    },
}


class TestFieldAllowlistIsPinned:
    def test_the_registry_contains_exactly_the_approved_tools(self):
        assert set(TOOL_REGISTRY) == set(EXPECTED_EXPOSED_FIELDS)

    @pytest.mark.parametrize("name", sorted(EXPECTED_EXPOSED_FIELDS))
    def test_exposed_field_set_has_not_changed(self, name):
        assert set(TOOL_REGISTRY[name].exposed_fields) == EXPECTED_EXPOSED_FIELDS[name]

    @pytest.mark.parametrize("name", sorted(EXPECTED_EXPOSED_FIELDS))
    def test_emitted_items_carry_exactly_the_allowlisted_fields(self, name):
        tool = TOOL_REGISTRY[name]
        result = tool.run(ctx(), tool.params_model(query=""))
        for item in result.items:
            assert set(item) == EXPECTED_EXPOSED_FIELDS[name]

    def test_projection_refuses_to_widen_beyond_the_allowlist(self):
        from app.assistant.content import OPERATIONAL_CONTENT

        entry = OPERATIONAL_CONTENT[0]
        with pytest.raises(KeyError):
            tools_mod._project(entry, frozenset({"entry_id", "not_a_field"}))

    def test_no_tool_exposes_a_field_that_is_not_a_content_attribute(self):
        allowed = {
            "entry_id", "title", "kind", "summary", "body", "required_role",
            "location", "version",
        }
        for tool in TOOL_REGISTRY.values():
            assert set(tool.exposed_fields) <= allowed


class TestToolsAreReadOnly:
    def test_no_tool_module_imports_a_database_or_http_client(self):
        source = inspect.getsource(tools_mod)
        for banned in ("sqlalchemy", "httpx", "requests", "psycopg", "asyncpg"):
            assert banned not in source

    def test_no_tool_module_contains_sql_or_write_verbs(self):
        source = inspect.getsource(tools_mod).lower()
        for banned in ("select ", "insert ", "update ", "delete ", "execute("):
            assert banned not in source

    def test_registry_holds_only_read_only_capability_tools(self):
        from app.assistant.flags import AssistantCapability

        for tool in TOOL_REGISTRY.values():
            assert tool.capability is AssistantCapability.OPERATIONAL_CHAT


class TestToolLookupFailsClosed:
    @pytest.mark.parametrize(
        "name",
        ["", None, "unknown_tool", "list_supported_reports ", "DROP TABLE", 42],
    )
    def test_unknown_tool_names_resolve_to_nothing(self, name):
        assert get_tool(name) is None

    def test_known_tool_resolves(self):
        assert get_tool("list_supported_reports") is LIST_SUPPORTED_REPORTS


class TestToolPermissions:
    def test_super_admin_reaches_no_tool(self):
        assert permitted_tools(frozenset({"doctor"}), is_super_admin=True) == []
        assert permitted_tools(frozenset({"super_admin"})) == []

    def test_unknown_role_reaches_no_tool(self):
        assert permitted_tools(frozenset({"intruder"})) == []

    @pytest.mark.parametrize(
        "role",
        [
            "hospital_admin", "receptionist", "triage_nurse", "ward_nurse",
            "doctor", "lab_technician", "radiographer", "pharmacist", "cashier",
        ],
    )
    def test_every_staff_role_reaches_both_tools(self, role):
        assert len(permitted_tools(frozenset({role}))) == 2


class TestReportCatalogNeverLeaksFigures:
    def test_report_items_carry_no_numeric_results(self):
        result = LIST_SUPPORTED_REPORTS.run(
            ctx(roles=("hospital_admin",)),
            LIST_SUPPORTED_REPORTS.params_model(query="report"),
        )
        assert result.items
        for item in result.items:
            # A catalog entry describes a report; it never carries its output.
            assert "total" not in item
            assert "count" not in item
            assert "rows" not in item
            assert "value" not in item

    def test_non_admin_gets_no_report_catalog_entries(self):
        result = LIST_SUPPORTED_REPORTS.run(
            ctx(roles=("receptionist",)),
            LIST_SUPPORTED_REPORTS.params_model(query="revenue report"),
        )
        assert result.items == ()


class TestToolParameterValidation:
    def test_unknown_parameters_are_rejected(self):
        with pytest.raises(ValidationError):
            LIST_SUPPORTED_REPORTS.params_model(query="x", tenant_id="hosp-b")

    def test_limit_is_bounded(self):
        with pytest.raises(ValidationError):
            SEARCH_OPERATIONAL_CONTENT.params_model(query="x", limit=999)
        with pytest.raises(ValidationError):
            SEARCH_OPERATIONAL_CONTENT.params_model(query="x", limit=0)


class TestSafeFailure:
    def test_a_failing_retrieval_returns_an_empty_result_not_an_exception(
        self, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError("connection string postgres://user:pw@host/db")

        monkeypatch.setattr(tools_mod, "retrieve", boom)
        result = SEARCH_OPERATIONAL_CONTENT.run(
            ctx(), SEARCH_OPERATIONAL_CONTENT.params_model(query="anything")
        )
        assert result.failed is True
        assert result.items == ()
        # The failure carries no detail that could leak a credential.
        assert "postgres" not in repr(result)
