"""Endpoint-level tests.

These exercise the real FastAPI app, the real router, the real gates, and the
real response contracts against a real database session. Only the token
dependency is substituted, because Keycloak is not reachable from a test. No
outbound call is made anywhere in this file.
"""

import pytest
from sqlalchemy import select
from uuid import UUID

from app.cds.contracts import CheckStatus, ReviewReason
from app.core import config
from app.models.cds import CdsAlertAction, CdsMedicationCheck
from tests.conftest import CANCELLED_VISIT_ID, OTHER_TENANT_ID, VISIT_ID
from tests.ruleset_fixtures import WARFARIN_IBUPROFEN, artifact, write_artifact

CHECK_URL = "/api/v1/cds/medication/check"
NORMALIZE_URL = "/api/v1/cds/medication/normalize"
ACTION_URL = "/api/v1/cds/medication/alert-action"
RULESET_URL = "/api/v1/cds/rulesets/active"


@pytest.fixture
def with_ruleset(tmp_path, monkeypatch):
    """Point the service at a synthetic ruleset approved for test only."""
    path = write_artifact(tmp_path, artifact(rules=[WARFARIN_IBUPROFEN]))
    monkeypatch.setattr(config.settings, "cds_ruleset_source", "file", raising=False)
    monkeypatch.setattr(config.settings, "cds_ruleset_path", path, raising=False)
    monkeypatch.setattr(config.settings, "environment", "test", raising=False)
    return path


# Kill switches


def test_every_route_is_absent_when_the_service_is_switched_off(client, monkeypatch):
    monkeypatch.setattr(config.settings, "cds_enabled", False, raising=False)
    monkeypatch.setattr(config.settings, "cds_medication_check_enabled", True, raising=False)
    signed_in = client()

    response = signed_in.post(CHECK_URL, json={"visit_id": str(VISIT_ID)})

    # Switched off looks like absent, so pulling the switch does not advertise
    # that a clinical capability exists.
    assert response.status_code == 404
    assert response.json()["code"] == "capability_disabled"


def test_the_capability_switch_alone_also_closes_the_route(client, monkeypatch):
    monkeypatch.setattr(config.settings, "cds_enabled", True, raising=False)
    monkeypatch.setattr(config.settings, "cds_medication_check_enabled", False, raising=False)

    assert client().post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).status_code == 404


@pytest.mark.parametrize(
    "url,body",
    [
        (CHECK_URL, {"visit_id": str(VISIT_ID)}),
        (NORMALIZE_URL, {"medicines": [{"display_name": "Warfarin"}]}),
        (
            ACTION_URL,
            {
                "check_id": "00000000-0000-4000-8000-000000000000",
                "finding_id": "f1",
                "action": "acknowledge",
            },
        ),
    ],
)
def test_no_route_answers_once_the_service_is_switched_off(client, monkeypatch, url, body):
    monkeypatch.setattr(config.settings, "cds_enabled", False, raising=False)
    assert client().post(url, json=body).status_code == 404


def test_the_ruleset_route_is_gated_too(client, monkeypatch):
    # A route still answering after the kill switch is pulled would tell a
    # caller the capability exists and is merely switched off.
    monkeypatch.setattr(config.settings, "cds_enabled", False, raising=False)
    response = client().get(RULESET_URL)

    assert response.status_code == 404
    assert response.json()["code"] == "capability_disabled"


def test_the_ruleset_route_is_refused_for_a_non_clinical_role(client, enabled):
    response = client(roles=["hospital_admin"]).get(RULESET_URL)
    assert response.status_code == 403


# Authorization


@pytest.mark.parametrize("role", ["doctor", "pharmacist"])
def test_a_clinical_role_may_run_a_check(client, enabled, role):
    response = client(roles=[role]).post(CHECK_URL, json={"visit_id": str(VISIT_ID)})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "role", ["hospital_admin", "receptionist", "triage_nurse", "cashier", "hospital_user"]
)
def test_a_non_clinical_role_is_refused(client, enabled, role):
    response = client(roles=[role]).post(CHECK_URL, json={"visit_id": str(VISIT_ID)})

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_a_hospital_admin_is_refused_even_though_it_is_the_most_senior_tenant_role(
    client, enabled
):
    response = client(roles=["hospital_admin"]).post(CHECK_URL, json={"visit_id": str(VISIT_ID)})
    assert response.status_code == 403


def test_a_super_admin_is_refused_even_holding_doctor(client, enabled):
    response = client(roles=["super_admin", "doctor"], is_super_admin=True).post(
        CHECK_URL, json={"visit_id": str(VISIT_ID)}
    )
    assert response.status_code == 403


def test_a_token_with_no_tenant_is_refused(client, enabled):
    response = client(tenant_id=None).post(CHECK_URL, json={"visit_id": str(VISIT_ID)})
    assert response.status_code == 403


# Resource access


def test_a_visit_that_does_not_exist_is_not_available(client, enabled):
    response = client().post(
        CHECK_URL, json={"visit_id": "00000000-0000-4000-8000-000000000000"}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"


def test_a_cancelled_visit_is_not_available(client, enabled):
    response = client().post(CHECK_URL, json={"visit_id": str(CANCELLED_VISIT_ID)})
    assert response.status_code == 404


def test_the_answer_is_identical_for_missing_and_for_forbidden(client, enabled):
    # If the two differed, the endpoint would be an oracle for discovering
    # which visit ids exist somewhere else.
    missing = client().post(
        CHECK_URL, json={"visit_id": "00000000-0000-4000-8000-000000000000"}
    )
    cancelled = client().post(CHECK_URL, json={"visit_id": str(CANCELLED_VISIT_ID)})

    assert missing.json()["message"] == cancelled.json()["message"]
    assert missing.status_code == cancelled.status_code


def test_a_client_cannot_name_its_own_tenant(client, enabled):
    response = client().post(
        CHECK_URL, json={"visit_id": str(VISIT_ID), "tenant_id": OTHER_TENANT_ID}
    )
    assert response.status_code == 422


def test_a_client_cannot_name_its_own_ruleset_version(client, enabled):
    response = client().post(
        CHECK_URL, json={"visit_id": str(VISIT_ID), "ruleset_version": "9.9.9"}
    )
    assert response.status_code == 422


def test_a_client_cannot_assert_a_severity(client, enabled):
    response = client().post(
        CHECK_URL, json={"visit_id": str(VISIT_ID), "severity": "low"}
    )
    assert response.status_code == 422


# The check itself


def test_with_no_ruleset_the_check_needs_review(client, enabled):
    response = client().post(CHECK_URL, json={"visit_id": str(VISIT_ID)})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == CheckStatus.NEEDS_REVIEW.value
    assert ReviewReason.NO_APPROVED_RULESET.value in body["review_reasons"]
    assert body["ruleset"] is None
    assert body["requires_human_review"] is True


def test_the_response_never_says_anything_that_reads_as_safe(client, enabled):
    body = client().post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()
    serialized = str(body).lower()

    # No reassuring claim, in any wording, when nothing was actually checked.
    for forbidden in (
        "no interaction found",
        "no interactions found",
        "no known interaction",
        "no contraindication",
        "cleared",
    ):
        assert forbidden not in serialized

    # And the disclaimer that says so is present rather than merely implied.
    assert "not a statement that these medicines are safe" in serialized
    assert body["status"] != CheckStatus.NO_ALERTS_IN_ACTIVE_RULESET.value


def test_prescribed_medicines_are_loaded_from_the_visit(client, enabled):
    body = client().post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()

    submitted = [m["submitted_name"] for m in body["medicines"]]
    assert any("Warfarin" in name for name in submitted)


def test_an_unconfirmed_prescription_is_not_checked_silently(client, enabled):
    body = client().post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()

    # The prescription came from the database and nobody has confirmed the
    # identity match, so it must surface as needing review.
    assert body["status"] == CheckStatus.NEEDS_REVIEW.value
    assert ReviewReason.UNCONFIRMED_MEDICINE.value in body["review_reasons"]


def test_a_confirmed_pair_with_a_ruleset_produces_a_reproducible_alert(
    client, enabled, with_ruleset
):
    body = client().post(
        CHECK_URL,
        json={
            "visit_id": str(VISIT_ID),
            "include_prescribed": False,
            "additional_medicines": [
                {
                    "display_name": "Warfarin",
                    "dose": "5mg",
                    "route": "oral",
                    "form": "tablet",
                    "confirmed_key": "WAR-5",
                },
                {
                    "display_name": "Ibuprofen",
                    "dose": "400mg",
                    "route": "oral",
                    "form": "tablet",
                    "confirmed_key": "IBU-400",
                },
            ],
        },
    ).json()

    assert body["status"] == CheckStatus.ALERTS.value
    alert = body["findings"][0]
    assert alert["severity"] == "high"
    assert alert["rule_id"] == "TEST-DDI-0001"
    assert alert["ruleset_version"] == "test-1.0.0"
    assert alert["effective_date"]
    assert alert["evaluated_at"]
    assert body["ruleset"]["source_name"]


def test_too_many_medicines_is_refused(client, enabled, monkeypatch):
    monkeypatch.setattr(config.settings, "cds_max_medications_per_check", 2, raising=False)
    response = client().post(
        CHECK_URL,
        json={
            "visit_id": str(VISIT_ID),
            "include_prescribed": False,
            "additional_medicines": [
                {"display_name": f"Drug {n}", "dose": "1", "route": "oral", "form": "tablet"}
                for n in range(3)
            ],
        },
    )

    assert response.status_code == 413
    assert response.json()["code"] == "too_many_medicines"


# Normalization


def test_normalization_offers_candidates_for_confirmation(client, enabled):
    body = client().post(
        NORMALIZE_URL, json={"medicines": [{"display_name": "Warfarin 5mg tablet"}]}
    ).json()

    result = body["results"][0]
    assert result["resolution"] == "resolved"
    assert result["candidates"][0]["canonical_key"] == "WAR-5"
    assert result["requires_confirmation"] is True


def test_normalization_reports_an_unknown_name_as_unresolved(client, enabled):
    body = client().post(
        NORMALIZE_URL, json={"medicines": [{"display_name": "Mystery Pills"}]}
    ).json()

    assert body["results"][0]["resolution"] == "unresolved"
    assert body["results"][0]["candidates"] == []


def test_normalization_is_refused_for_a_non_clinical_role(client, enabled):
    response = client(roles=["receptionist"]).post(
        NORMALIZE_URL, json={"medicines": [{"display_name": "Warfarin"}]}
    )
    assert response.status_code == 403


# Ruleset reporting


def test_the_active_ruleset_endpoint_reports_unavailable_by_default(client, enabled):
    body = client().get(RULESET_URL).json()

    assert body["available"] is False
    assert body["unavailable_reason"] == ReviewReason.NO_APPROVED_RULESET.value
    assert body["descriptor"] is None


def test_the_active_ruleset_endpoint_reports_the_version_when_one_is_loaded(
    client, enabled, with_ruleset
):
    body = client().get(RULESET_URL).json()

    assert body["available"] is True
    assert body["descriptor"]["ruleset_version"] == "test-1.0.0"
    assert body["descriptor"]["approved_by"]
    # No rule content, only provenance.
    assert "rules" not in body["descriptor"]


# Audit persistence


@pytest.mark.asyncio
async def test_a_check_is_recorded_append_only(client, enabled, session_factory):
    body = client().post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()

    async with session_factory() as session:
        row = (
            await session.execute(
                select(CdsMedicationCheck).where(
                    CdsMedicationCheck.check_id == UUID(body["check_id"])
                )
            )
        ).scalar_one()

    assert row.tenant_id == "hosp-c5c8388b"
    assert row.actor_sub == "doctor-sub-1"
    assert row.actor_role == "doctor"
    assert row.status == body["status"]
    assert row.ruleset_version is None
    assert set(row.finding_index) == {f["finding_id"] for f in body["findings"]}


@pytest.mark.asyncio
async def test_an_acknowledgement_is_recorded_against_its_check(
    client, enabled, session_factory
):
    signed_in = client()
    check = signed_in.post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()
    finding_id = check["findings"][0]["finding_id"]

    response = signed_in.post(
        ACTION_URL,
        json={
            "check_id": check["check_id"],
            "finding_id": finding_id,
            "action": "acknowledge",
        },
    )
    assert response.status_code == 200

    async with session_factory() as session:
        row = (
            await session.execute(
                select(CdsAlertAction).where(
                    CdsAlertAction.action_id == UUID(response.json()["action_id"])
                )
            )
        ).scalar_one()

    assert row.action == "acknowledge"
    assert row.actor_sub == "doctor-sub-1"
    assert row.reason is None


def test_a_finding_from_another_check_cannot_be_acknowledged(client, enabled):
    signed_in = client()
    check = signed_in.post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()

    response = signed_in.post(
        ACTION_URL,
        json={
            "check_id": check["check_id"],
            "finding_id": "0" * 32,
            "action": "acknowledge",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"


def test_a_check_from_another_tenant_cannot_be_acknowledged(client, enabled):
    check = client().post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()
    finding_id = check["findings"][0]["finding_id"]

    other_tenant = client(tenant_id=OTHER_TENANT_ID)
    response = other_tenant.post(
        ACTION_URL,
        json={
            "check_id": check["check_id"],
            "finding_id": finding_id,
            "action": "acknowledge",
        },
    )

    assert response.status_code == 404


def test_an_override_without_a_reason_is_refused(client, enabled):
    signed_in = client()
    check = signed_in.post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()

    response = signed_in.post(
        ACTION_URL,
        json={
            "check_id": check["check_id"],
            "finding_id": check["findings"][0]["finding_id"],
            "action": "override",
        },
    )
    assert response.status_code == 422


def test_a_needs_review_finding_cannot_be_overridden(client, enabled):
    signed_in = client()
    check = signed_in.post(CHECK_URL, json={"visit_id": str(VISIT_ID)}).json()

    response = signed_in.post(
        ACTION_URL,
        json={
            "check_id": check["check_id"],
            "finding_id": check["findings"][0]["finding_id"],
            "action": "override",
            "reason": "Reviewed with the prescriber.",
        },
    )

    # An open question is not a decision to overrule. It has to be reviewed.
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_a_decided_alert_can_be_overridden_with_a_reason(
    client, enabled, with_ruleset, session_factory
):
    signed_in = client()
    check = signed_in.post(
        CHECK_URL,
        json={
            "visit_id": str(VISIT_ID),
            "include_prescribed": False,
            "additional_medicines": [
                {
                    "display_name": "Warfarin",
                    "dose": "5mg",
                    "route": "oral",
                    "form": "tablet",
                    "confirmed_key": "WAR-5",
                },
                {
                    "display_name": "Ibuprofen",
                    "dose": "400mg",
                    "route": "oral",
                    "form": "tablet",
                    "confirmed_key": "IBU-400",
                },
            ],
        },
    ).json()

    response = signed_in.post(
        ACTION_URL,
        json={
            "check_id": check["check_id"],
            "finding_id": check["findings"][0]["finding_id"],
            "action": "override",
            "reason": "Discussed with the prescriber; INR monitored daily.",
        },
    )
    assert response.status_code == 200

    async with session_factory() as session:
        row = (
            await session.execute(
                select(CdsAlertAction).where(
                    CdsAlertAction.action_id == UUID(response.json()["action_id"])
                )
            )
        ).scalar_one()

    assert row.action == "override"
    assert row.reason.startswith("Discussed with the prescriber")
    assert row.actor_role == "doctor"


def test_the_error_body_carries_no_stack_trace_or_database_detail(client, enabled):
    response = client().post(
        CHECK_URL, json={"visit_id": "00000000-0000-4000-8000-000000000000"}
    )
    body = response.json()

    assert set(body) == {"request_id", "code", "message"}
    serialized = str(body).lower()
    for forbidden in ("traceback", "sqlalchemy", "select ", "postgres", "asyncpg"):
        assert forbidden not in serialized
