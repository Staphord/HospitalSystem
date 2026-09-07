from datetime import date

import pytest

from app.assistant.content import (
    CONTENT_PACK_VERSION,
    OPERATIONAL_CONTENT,
    ApprovalState,
    ContentEntry,
    ContentKind,
)
from app.assistant.retrieval import (
    MAX_ENTRIES_PER_RETRIEVAL,
    RetrievalContext,
    build_retrieval_context,
    resolve_department,
    retrieve,
    visible_entries,
)

TODAY = date(2026, 8, 25)


def ctx(tenant="hosp-aaaa1111", roles=("receptionist",), today=TODAY):
    built = build_retrieval_context(tenant, list(roles), today=today)
    assert built is not None
    return built


class TestContentPackIntegrity:
    def test_pack_is_not_empty_and_has_a_version(self):
        assert OPERATIONAL_CONTENT
        assert CONTENT_PACK_VERSION

    def test_every_entry_is_approved_and_in_effect(self):
        for entry in OPERATIONAL_CONTENT:
            assert entry.approval_state is ApprovalState.APPROVED
            assert entry.effective_from <= TODAY

    def test_entry_ids_are_unique(self):
        ids = [e.entry_id for e in OPERATIONAL_CONTENT]
        assert len(ids) == len(set(ids))

    def test_every_entry_names_an_audience_and_a_version(self):
        for entry in OPERATIONAL_CONTENT:
            assert entry.roles
            assert entry.version

    def test_no_entry_carries_clinical_or_patient_content(self):
        # The operational pack must never drift into clinical territory. These
        # are the words that would signal it had.
        #
        # One entry is exempt: policy.assistant.scope exists precisely to tell
        # staff that the assistant does not do these things, so it has to be
        # able to name them. It is checked separately below, and the exemption
        # is by exact entry id so no other entry can quietly claim it.
        forbidden = (
            # "prescription" as a workflow object is legitimate operational
            # content; instructing someone to prescribe is not.
            "mg ", "dose", "dosage", "prescribe", "prescribing", "diagnos",
            "contraindicat", "severity", "mmhg",
        )
        for entry in OPERATIONAL_CONTENT:
            if entry.entry_id == "policy.assistant.scope":
                continue
            body = entry.body.lower()
            for word in forbidden:
                assert word not in body, f"{entry.entry_id} contains {word!r}"

    def test_the_scope_policy_disclaims_clinical_capability(self):
        entry = next(
            e for e in OPERATIONAL_CONTENT if e.entry_id == "policy.assistant.scope"
        )
        body = entry.body.lower()
        # The exempt entry must actually be a disclaimer, phrased negatively.
        assert "does not" in body
        assert "diagnos" in body
        assert "read only" in body or "read-only" in body

    def test_an_entry_must_name_at_least_one_role(self):
        with pytest.raises(ValueError):
            ContentEntry(
                entry_id="bad",
                kind=ContentKind.HELP,
                title="t",
                body="b",
                version="1.0.0",
                effective_from=TODAY,
                approval_state=ApprovalState.APPROVED,
                roles=frozenset(),
            )


class TestRetrievalContext:
    def test_missing_tenant_is_refused(self):
        assert build_retrieval_context(None, ["doctor"]) is None
        assert build_retrieval_context("", ["doctor"]) is None
        assert build_retrieval_context("   ", ["doctor"]) is None

    def test_missing_roles_are_refused(self):
        assert build_retrieval_context("hosp-a", []) is None
        assert build_retrieval_context("hosp-a", None) is None

    def test_roles_are_normalized(self):
        built = build_retrieval_context("hosp-a", ["Triage Nurse"])
        assert built is not None
        assert "triage_nurse" in built.roles

    def test_department_is_derived_from_role_not_supplied(self):
        assert resolve_department(frozenset({"pharmacist"})) == "pharmacy"
        assert resolve_department(frozenset({"cashier"})) == "billing"
        assert resolve_department(frozenset({"unknown_role"})) is None

    def test_department_resolution_is_stable_for_multiple_roles(self):
        roles = frozenset({"pharmacist", "cashier", "doctor"})
        assert resolve_department(roles) == resolve_department(roles)


class TestAccessFiltering:
    def test_role_filter_excludes_admin_only_content_from_staff(self):
        receptionist = visible_entries(ctx(roles=("receptionist",)))
        ids = {e.entry_id for e in receptionist}
        assert not any(i.startswith("report.") for i in ids)

    def test_hospital_admin_sees_the_report_catalog(self):
        admin = visible_entries(ctx(roles=("hospital_admin",)))
        ids = {e.entry_id for e in admin}
        assert "report.patient-census" in ids

    def test_department_filter_separates_staff(self):
        pharmacist_ids = {e.entry_id for e in visible_entries(ctx(roles=("pharmacist",)))}
        cashier_ids = {e.entry_id for e in visible_entries(ctx(roles=("cashier",)))}
        assert "workflow.pharmacy.dispense" in pharmacist_ids
        assert "workflow.pharmacy.dispense" not in cashier_ids
        assert "workflow.billing.take-payment" in cashier_ids
        assert "workflow.billing.take-payment" not in pharmacist_ids

    def test_shared_help_reaches_every_staff_role(self):
        for role in ("receptionist", "doctor", "pharmacist", "cashier", "ward_nurse"):
            ids = {e.entry_id for e in visible_entries(ctx(roles=(role,)))}
            assert "help.navigation.overview" in ids

    def test_unknown_role_sees_nothing(self):
        built = build_retrieval_context("hosp-a", ["intruder"], today=TODAY)
        assert built is not None
        assert visible_entries(built) == []

    def test_draft_content_is_never_visible(self):
        entry = ContentEntry(
            entry_id="draft.entry",
            kind=ContentKind.HELP,
            title="Draft",
            body="unapproved",
            version="1.0.0",
            effective_from=date(2020, 1, 1),
            approval_state=ApprovalState.DRAFT,
            roles=frozenset({"doctor"}),
        )
        from app.assistant import retrieval

        assert retrieval._is_visible(entry, ctx(roles=("doctor",))) is False

    def test_content_not_yet_in_effect_is_not_visible(self):
        entry = ContentEntry(
            entry_id="future.entry",
            kind=ContentKind.HELP,
            title="Future",
            body="not yet",
            version="1.0.0",
            effective_from=date(2099, 1, 1),
            approval_state=ApprovalState.APPROVED,
            roles=frozenset({"doctor"}),
        )
        from app.assistant import retrieval

        assert retrieval._is_visible(entry, ctx(roles=("doctor",))) is False

    def test_tenant_allowlist_is_enforced(self):
        entry = ContentEntry(
            entry_id="tenant.scoped",
            kind=ContentKind.HELP,
            title="Scoped",
            body="only for one hospital",
            version="1.0.0",
            effective_from=date(2020, 1, 1),
            approval_state=ApprovalState.APPROVED,
            roles=frozenset({"doctor"}),
            tenants=frozenset({"hosp-aaaa1111"}),
        )
        from app.assistant import retrieval

        mine = ctx(tenant="hosp-aaaa1111", roles=("doctor",))
        theirs = ctx(tenant="hosp-bbbb2222", roles=("doctor",))
        assert retrieval._is_visible(entry, mine) is True
        assert retrieval._is_visible(entry, theirs) is False

    def test_only_the_current_version_of_an_entry_survives(self):
        from app.assistant import retrieval

        old = ContentEntry(
            entry_id="versioned",
            kind=ContentKind.HELP,
            title="Old",
            body="old text",
            version="1.0.0",
            effective_from=date(2020, 1, 1),
            approval_state=ApprovalState.APPROVED,
            roles=frozenset({"doctor"}),
        )
        new = ContentEntry(
            entry_id="versioned",
            kind=ContentKind.HELP,
            title="New",
            body="new text",
            version="2.1.0",
            effective_from=date(2020, 1, 1),
            approval_state=ApprovalState.APPROVED,
            roles=frozenset({"doctor"}),
        )
        current = retrieval._current_versions([old, new])
        assert len(current) == 1
        assert current[0].version == "2.1.0"


class TestRelevanceAndBounds:
    def test_query_matches_relevant_content(self):
        results = retrieve(ctx(roles=("receptionist",)), query="how do I register a patient")
        assert results
        assert results[0].entry_id == "workflow.reception.register-patient"

    def test_unmatched_query_returns_nothing_rather_than_guessing(self):
        results = retrieve(ctx(roles=("receptionist",)), query="zzzqqq unrelated nonsense")
        assert results == []

    def test_results_are_bounded(self):
        results = retrieve(ctx(roles=("hospital_admin",)), query="report", limit=99)
        assert len(results) <= MAX_ENTRIES_PER_RETRIEVAL

    def test_ranking_never_promotes_forbidden_content(self):
        # A receptionist asking precisely about a report still cannot see one.
        results = retrieve(ctx(roles=("receptionist",)), query="revenue report bed occupancy")
        assert all(not r.entry_id.startswith("report.") for r in results)

    def test_results_are_deterministic(self):
        first = retrieve(ctx(roles=("doctor",)), query="consultation encounter")
        second = retrieve(ctx(roles=("doctor",)), query="consultation encounter")
        assert [e.entry_id for e in first] == [e.entry_id for e in second]
