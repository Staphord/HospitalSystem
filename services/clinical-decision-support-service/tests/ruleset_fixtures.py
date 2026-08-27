"""Synthetic ruleset artifacts for tests.

These are not clinical content and must never be used anywhere but a test. Two
things keep them there: the source name says so out loud, and every artifact is
approved only for the "test" environment, which FileRulesetSource enforces by
refusing to load an artifact that is not approved for the environment it is
running in.

Dates are computed relative to today rather than hard-coded, so a fixture
cannot silently go stale and start failing the suite a year from now, and so
the staleness tests are exercising the real date logic.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SYNTHETIC_SOURCE = "synthetic-test-fixture-not-clinical-content"


def rule(
    rule_id: str,
    rule_type: str,
    severity: str,
    ingredients: list[str],
    *,
    allergen: str | None = None,
    blocking: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rule_id": rule_id,
        "type": rule_type,
        "severity": severity,
        "ingredients": ingredients,
        "explanation": f"Synthetic test rule {rule_id}. Not clinical content.",
        "review_action": "Synthetic test action. Not clinical advice.",
        "blocking": blocking,
        "limitations": ["Synthetic fixture with no clinical standing."],
    }
    if allergen:
        payload["allergen"] = allergen
    return payload


def artifact(
    *,
    rules: list[dict[str, Any]] | None = None,
    effective_days_ago: int = 30,
    review_days_ahead: int | None = 365,
    environments: list[str] | None = None,
    version: str = "test-1.0.0",
    **overrides: Any,
) -> dict[str, Any]:
    today = date.today()
    payload: dict[str, Any] = {
        "source_name": SYNTHETIC_SOURCE,
        "ruleset_version": version,
        "effective_date": (today - timedelta(days=effective_days_ago)).isoformat(),
        "approval": {
            "approved_by": "Test Fixture, not a pharmacist",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approved_for_environments": environments if environments is not None else ["test"],
        },
        "rules": rules if rules is not None else [],
    }
    if review_days_ahead is not None:
        payload["review_date"] = (today + timedelta(days=review_days_ahead)).isoformat()
    payload.update(overrides)
    return payload


def write_artifact(tmp_path: Path, payload: dict[str, Any] | str, name: str = "ruleset.json") -> str:
    path = tmp_path / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# The pair every test that needs a firing drug-drug rule uses. Ingredient keys
# match what the offline normalizer derives from the fixture inventory.
WARFARIN_IBUPROFEN = rule(
    "TEST-DDI-0001", "drug_drug", "high", ["warfarin", "ibuprofen"], blocking=True
)
PENICILLIN_ALLERGY = rule(
    "TEST-ALG-0001", "drug_allergy", "critical", ["amoxicillin"], allergen="penicillin"
)
FORMULARY_DICLOFENAC = rule(
    "TEST-FRM-0001", "formulary_restriction", "moderate", ["diclofenac"]
)
