import pytest
from pydantic import ValidationError

from app.assistant.audit import (
    PROHIBITED_AUDIT_FIELDS,
    AssistantAuditMetadata,
    AssistantOutcome,
    build_audit_metadata,
)
from app.assistant.flags import AssistantCapability


class TestAuditMetadataShape:
    def test_record_captures_actor_tenant_capability_and_outcome(self):
        record = build_audit_metadata(
            request_id="req-1",
            actor_sub="user-sub-1",
            capability=AssistantCapability.OPERATIONAL_CHAT,
            outcome=AssistantOutcome.SUCCESS,
            tenant_id="hosp-00000001",
            provider="groq",
            model_version="llama-3.3-70b-versatile",
            duration_ms=412,
        )

        assert record.request_id == "req-1"
        assert record.actor_sub == "user-sub-1"
        assert record.tenant_id == "hosp-00000001"
        assert record.capability is AssistantCapability.OPERATIONAL_CHAT
        assert record.outcome is AssistantOutcome.SUCCESS
        assert record.occurred_at is not None

    def test_patient_and_visit_references_are_optional(self):
        record = build_audit_metadata(
            request_id="req-1",
            actor_sub="user-sub-1",
            capability=AssistantCapability.OPERATIONAL_CHAT,
            outcome=AssistantOutcome.SUCCESS,
        )
        assert record.patient_ref is None
        assert record.visit_ref is None

    def test_ruleset_version_is_recorded_for_clinical_outcomes(self):
        record = build_audit_metadata(
            request_id="req-1",
            actor_sub="user-sub-1",
            capability=AssistantCapability.MEDICATION_CHECK,
            outcome=AssistantOutcome.NEEDS_REVIEW,
            ruleset_version="rules-2026.02",
        )
        assert record.ruleset_version == "rules-2026.02"

    def test_every_failure_mode_has_an_outcome(self):
        values = {outcome.value for outcome in AssistantOutcome}
        for expected in (
            "permission_denied",
            "capability_disabled",
            "provider_error",
            "needs_review",
        ):
            assert expected in values


class TestAuditRecordsCarryNoContent:
    def _build_with(self, field, value):
        return AssistantAuditMetadata(
            request_id="req-1",
            actor_sub="user-sub-1",
            capability=AssistantCapability.OPERATIONAL_CHAT,
            outcome=AssistantOutcome.SUCCESS,
            **{field: value},
        )

    @pytest.mark.parametrize(
        "field",
        [
            "question",
            "answer",
            "transcript",
            "audio",
            "notes",
            "diagnosis",
            "stack_trace",
            "traceback",
            "tool_payload",
        ],
    )
    def test_audit_specific_content_field_is_refused_by_the_audit_guard(self, field):
        """These names are guarded only by the audit record, so the audit guard
        must be the one that rejects them."""
        with pytest.raises(ValidationError) as exc:
            self._build_with(field, "sensitive content")
        assert "PROHIBITED_AUDIT_FIELD" in str(exc.value)

    @pytest.mark.parametrize(
        "field", ["prompt", "api_key", "groq_api_key", "database_url", "db_dsn"]
    )
    def test_secret_field_is_refused(self, field):
        """These names are guarded by both the contract base and the audit
        record; either guard rejecting them is correct."""
        with pytest.raises(ValidationError) as exc:
            self._build_with(field, "secret")
        message = str(exc.value)
        assert "PROHIBITED_AUDIT_FIELD" in message or "FORBIDDEN_CLIENT_FIELD" in message

    def test_declared_model_has_no_free_text_field(self):
        declared = set(AssistantAuditMetadata.model_fields)
        assert declared.isdisjoint(PROHIBITED_AUDIT_FIELDS)

    def test_unknown_field_is_refused(self):
        with pytest.raises(ValidationError):
            AssistantAuditMetadata(
                request_id="req-1",
                actor_sub="user-sub-1",
                capability=AssistantCapability.OPERATIONAL_CHAT,
                outcome=AssistantOutcome.SUCCESS,
                extra_field="value",
            )
