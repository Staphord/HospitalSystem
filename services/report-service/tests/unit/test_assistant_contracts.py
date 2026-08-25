from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.assistant.contracts import (
    FORBIDDEN_CLIENT_FIELDS,
    AssistantAnswerStatus,
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantErrorCode,
    AssistantErrorResponse,
    AssistantFeedbackRequest,
    ClinicalDifferentialSupport,
    ClinicalSuggestion,
    MedicationAlert,
    MedicationCheckResult,
    MedicationCheckStatus,
    VoiceTranscriptMetadata,
)


class TestClientControlledFieldsAreRejected:
    def test_valid_chat_request_is_accepted(self):
        req = AssistantChatRequest(question="Where do I find the discharge report?")
        assert req.question == "Where do I find the discharge report?"
        assert req.conversation_id is None

    @pytest.mark.parametrize(
        "field",
        [
            "tenant_id",
            "hospital_id",
            "database_url",
            "db_dsn",
            "x_tenant_db",
            "role",
            "roles",
            "scope",
            "is_super_admin",
            "api_key",
            "groq_api_key",
            "system_prompt",
            "tools",
            "sql",
            "url",
        ],
    )
    def test_server_authoritative_field_is_refused(self, field):
        with pytest.raises(ValidationError) as exc:
            AssistantChatRequest(**{"question": "hello", field: "attacker-supplied"})
        assert "FORBIDDEN_CLIENT_FIELD" in str(exc.value)

    def test_forbidden_field_check_is_case_insensitive(self):
        with pytest.raises(ValidationError) as exc:
            AssistantChatRequest(**{"question": "hello", "Tenant_ID": "hosp-000"})
        assert "FORBIDDEN_CLIENT_FIELD" in str(exc.value)

    def test_unknown_field_is_refused(self):
        with pytest.raises(ValidationError):
            AssistantChatRequest(question="hello", surprise="value")

    def test_tenant_and_credential_names_are_covered(self):
        for name in ("tenant_id", "database_url", "groq_api_key", "roles"):
            assert name in FORBIDDEN_CLIENT_FIELDS

    def test_oversized_question_is_refused(self):
        with pytest.raises(ValidationError):
            AssistantChatRequest(question="x" * 2001)

    def test_empty_question_is_refused(self):
        with pytest.raises(ValidationError):
            AssistantChatRequest(question="   ")


class TestChatResponseEnvelope:
    def test_supported_answer_may_cite_sources(self):
        resp = AssistantChatResponse(
            request_id="req-1",
            status=AssistantAnswerStatus.SUPPORTED,
            answer="Open the Reports screen.",
            sources=[{"label": "Help centre", "kind": "help", "version": "2024-01"}],
        )
        assert resp.sources[0].label == "Help centre"

    def test_unsupported_answer_must_not_cite_sources(self):
        with pytest.raises(ValidationError):
            AssistantChatResponse(
                request_id="req-1",
                status=AssistantAnswerStatus.UNSUPPORTED,
                answer="I cannot help with that.",
                sources=[{"label": "Help centre", "kind": "help"}],
            )

    def test_unavailable_answer_needs_no_sources(self):
        resp = AssistantChatResponse(
            request_id="req-1",
            status=AssistantAnswerStatus.UNAVAILABLE,
            answer="The assistant is not available right now.",
        )
        assert resp.sources == []


class TestFeedbackContract:
    def test_feedback_is_scoped_to_a_request_id(self):
        fb = AssistantFeedbackRequest(request_id="req-1", rating="not_helpful")
        assert fb.request_id == "req-1"
        assert fb.comment is None

    def test_feedback_rejects_unknown_rating(self):
        with pytest.raises(ValidationError):
            AssistantFeedbackRequest(request_id="req-1", rating="excellent")


class TestVoiceMetadata:
    def test_metadata_defaults_to_no_retention(self):
        meta = VoiceTranscriptMetadata(
            duration_ms=4000, mime_type="audio/webm", sample_rate_hz=48000, byte_size=32000
        )
        assert meta.audio_retained is False
        assert meta.transcript_confirmed_by_user is False

    def test_audio_retention_cannot_be_switched_on_through_the_contract(self):
        with pytest.raises(ValidationError):
            VoiceTranscriptMetadata(
                duration_ms=4000,
                mime_type="audio/webm",
                sample_rate_hz=48000,
                byte_size=32000,
                audio_retained=True,
            )

    def test_oversized_capture_is_refused(self):
        with pytest.raises(ValidationError):
            VoiceTranscriptMetadata(
                duration_ms=4000,
                mime_type="audio/webm",
                sample_rate_hz=48000,
                byte_size=11 * 1024 * 1024,
            )


class TestMedicationCheckFailsClosed:
    def test_default_status_is_needs_review(self):
        result = MedicationCheckResult(needs_review_reason="terminology source unavailable")
        assert result.status is MedicationCheckStatus.NEEDS_REVIEW
        assert result.alerts == []

    def test_needs_review_requires_a_reason(self):
        with pytest.raises(ValidationError):
            MedicationCheckResult(status=MedicationCheckStatus.NEEDS_REVIEW)

    def test_concluded_check_requires_a_ruleset_version(self):
        with pytest.raises(ValidationError):
            MedicationCheckResult(
                status=MedicationCheckStatus.NO_ALERTS_FROM_RULESET,
                checked_at=datetime.now(timezone.utc),
            )

    def test_no_alerts_result_is_attributable_to_a_ruleset_version(self):
        result = MedicationCheckResult(
            status=MedicationCheckStatus.NO_ALERTS_FROM_RULESET,
            ruleset_version="rules-2026.02",
            checked_at=datetime.now(timezone.utc),
        )
        assert result.ruleset_version == "rules-2026.02"

    def test_alerts_found_requires_at_least_one_alert(self):
        with pytest.raises(ValidationError):
            MedicationCheckResult(
                status=MedicationCheckStatus.ALERTS_FOUND, ruleset_version="rules-2026.02"
            )

    def test_no_alerts_status_must_not_carry_alerts(self):
        alert = MedicationAlert(
            kind="drug_drug",
            severity="high",
            detail="detail",
            recommendation="recommendation",
            ruleset_version="rules-2026.02",
        )
        with pytest.raises(ValidationError):
            MedicationCheckResult(
                status=MedicationCheckStatus.NO_ALERTS_FROM_RULESET,
                ruleset_version="rules-2026.02",
                alerts=[alert],
            )

    def test_every_alert_carries_its_ruleset_version(self):
        with pytest.raises(ValidationError):
            MedicationAlert(
                kind="drug_drug", severity="high", detail="d", recommendation="r"
            )

    def test_there_is_no_status_that_asserts_safety(self):
        values = {status.value for status in MedicationCheckStatus}
        assert "safe" not in values
        assert "no_interaction_found" not in values


class TestClinicalDifferentialSupport:
    def _valid(self, **overrides):
        payload = {
            "request_id": "req-1",
            "inputs_used": ["presenting complaint", "recorded vitals"],
            "suggestions": [
                ClinicalSuggestion(
                    consideration="Consider further testing",
                    supporting_findings=["fever"],
                    evidence_version="evidence-2026.01",
                )
            ],
            "limitations": ["Not a diagnosis. Clinician review required."],
            "ruleset_version": "rules-2026.02",
        }
        payload.update(overrides)
        return ClinicalDifferentialSupport(**payload)

    def test_human_review_is_mandatory(self):
        with pytest.raises(ValidationError):
            self._valid(requires_human_review=False)

    def test_limitations_and_inputs_are_required(self):
        with pytest.raises(ValidationError):
            self._valid(limitations=[])
        with pytest.raises(ValidationError):
            self._valid(inputs_used=[])

    def test_contract_has_no_treatment_or_write_fields(self):
        fields = set(ClinicalDifferentialSupport.model_fields)
        for forbidden in ("prescription", "dose", "medication", "order", "treatment"):
            assert forbidden not in fields


class TestErrorEnvelope:
    def test_error_carries_a_stable_code_and_short_message(self):
        err = AssistantErrorResponse(
            request_id="req-1",
            code=AssistantErrorCode.PROVIDER_TIMEOUT,
            message="The assistant took too long to respond.",
        )
        assert err.code is AssistantErrorCode.PROVIDER_TIMEOUT

    def test_error_envelope_has_no_field_for_internal_detail(self):
        fields = set(AssistantErrorResponse.model_fields)
        for forbidden in ("stack_trace", "traceback", "detail", "provider_payload"):
            assert forbidden not in fields
