"""Subscription plan catalog and plan-ranking utilities.

Plans, prices, and feature gates are server-side constants. Clients can only
choose a plan slug and a billing cycle; they cannot invent plans or prices.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SubscriptionPlan(str, Enum):
    """Canonical plan identifiers."""

    FREE_TRIAL = "free_trial"
    BASIC = "basic"
    STANDARD = "standard"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


# Deterministic UUIDs for canonical plans so they stay stable across restarts.
PLAN_UUIDS: dict[SubscriptionPlan, uuid.UUID] = {
    SubscriptionPlan.FREE_TRIAL: uuid.UUID("11111111-1111-1111-1111-111111111111"),
    SubscriptionPlan.BASIC: uuid.UUID("22222222-2222-2222-2222-222222222222"),
    SubscriptionPlan.STANDARD: uuid.UUID("33333333-3333-3333-3333-333333333333"),
    SubscriptionPlan.PREMIUM: uuid.UUID("44444444-4444-4444-4444-444444444444"),
    SubscriptionPlan.ENTERPRISE: uuid.UUID("55555555-5555-5555-5555-555555555555"),
}


def plan_uuid(plan: str | SubscriptionPlan) -> uuid.UUID:
    if isinstance(plan, str):
        plan = SubscriptionPlan(plan)
    return PLAN_UUIDS[plan]


class BillingCycle(str, Enum):
    """Supported billing cycles."""

    MONTHLY = "monthly"
    ANNUAL = "annual"


class SubscriptionStatus(str, Enum):
    """Tenant subscription lifecycle states."""

    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    TERMINATED = "terminated"


@dataclass(frozen=True)
class PlanDetails:
    """Immutable plan definition."""

    display_name: str
    monthly_price: int  # whole currency units, e.g. USD cents or major units
    annual_price: int
    trial_days: int
    max_users: int
    features: frozenset[str] = field(default_factory=frozenset)


# Plan hierarchy used for upgrade/downgrade validation.
PLAN_RANK: dict[SubscriptionPlan, int] = {
    SubscriptionPlan.FREE_TRIAL: 0,
    SubscriptionPlan.BASIC: 1,
    SubscriptionPlan.STANDARD: 2,
    SubscriptionPlan.PREMIUM: 3,
    SubscriptionPlan.ENTERPRISE: 4,
}


PLAN_CATALOG: dict[SubscriptionPlan, PlanDetails] = {
    SubscriptionPlan.FREE_TRIAL: PlanDetails(
        display_name="Free Trial",
        monthly_price=0,
        annual_price=0,
        trial_days=14,
        max_users=3,
        features=frozenset({"reception", "triage"}),
    ),
    SubscriptionPlan.BASIC: PlanDetails(
        display_name="Basic",
        monthly_price=99,
        annual_price=990,
        trial_days=0,
        max_users=20,
        features=frozenset({"reception", "triage", "consultation"}),
    ),
    SubscriptionPlan.STANDARD: PlanDetails(
        display_name="Standard",
        monthly_price=299,
        annual_price=2990,
        trial_days=0,
        max_users=100,
        features=frozenset({"reception", "triage", "consultation", "laboratory", "radiology", "pharmacy"}),
    ),
    SubscriptionPlan.PREMIUM: PlanDetails(
        display_name="Premium",
        monthly_price=599,
        annual_price=5990,
        trial_days=0,
        max_users=500,
        features=frozenset({
            "reception", "triage", "consultation", "laboratory", "radiology",
            "pharmacy", "billing", "ward", "reports",
        }),
    ),
    SubscriptionPlan.ENTERPRISE: PlanDetails(
        display_name="Enterprise",
        monthly_price=0,
        annual_price=0,
        trial_days=0,
        max_users=0,  # unlimited
        features=frozenset({"*"}),  # all features
    ),
}


def get_plan(plan: str | SubscriptionPlan) -> PlanDetails:
    """Return plan details or raise ValueError for unknown plans."""
    if isinstance(plan, str):
        plan = SubscriptionPlan(plan)
    return PLAN_CATALOG[plan]


def plan_rank(plan: str | SubscriptionPlan) -> int:
    """Return numeric rank of a plan (higher == more privileged)."""
    if isinstance(plan, str):
        plan = SubscriptionPlan(plan)
    return PLAN_RANK[plan]


def is_upgrade(from_plan: str | SubscriptionPlan, to_plan: str | SubscriptionPlan) -> bool:
    """True if to_plan is strictly higher rank than from_plan."""
    return plan_rank(to_plan) > plan_rank(from_plan)


def is_downgrade(from_plan: str | SubscriptionPlan, to_plan: str | SubscriptionPlan) -> bool:
    """True if to_plan is strictly lower rank than from_plan."""
    return plan_rank(to_plan) < plan_rank(from_plan)


def valid_plan_transition(from_plan: str | SubscriptionPlan, to_plan: str | SubscriptionPlan) -> bool:
    """A transition is valid if it is not to free_trial (trials are one-time only)."""
    if isinstance(to_plan, str):
        to_plan = SubscriptionPlan(to_plan)
    return to_plan is not SubscriptionPlan.FREE_TRIAL


def subscription_duration_days(billing_cycle: BillingCycle, plan: SubscriptionPlan | None = None) -> int:
    """Return the number of days a subscription/renewal should last.

    Annual subscriptions always grant 365 days. Trials use the plan's trial_days.
    """
    if billing_cycle is BillingCycle.ANNUAL:
        return 365
    if billing_cycle is BillingCycle.MONTHLY:
        return 30
    raise ValueError(f"Unsupported billing cycle: {billing_cycle}")


def plan_to_json(plan: SubscriptionPlan) -> dict[str, Any]:
    """Serialize a plan definition for API responses."""
    details = PLAN_CATALOG[plan]
    return {
        "plan": plan.value,
        "plan_id": str(plan_uuid(plan)),
        "display_name": details.display_name,
        "monthly_price": details.monthly_price,
        "annual_price": details.annual_price,
        "trial_days": details.trial_days,
        "max_users": details.max_users,
        "features": sorted(details.features),
        "rank": PLAN_RANK[plan],
    }


def sync_plans_to_db(db) -> None:
    """Upsert canonical plans into the subscription_plans table."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    for plan in SubscriptionPlan:
        details = PLAN_CATALOG[plan]
        db.execute(
            text(
                """
                INSERT INTO subscription_plans (
                    plan_id, plan_name, description, max_users, max_patients,
                    storage_gb, modules_included, monthly_price, annual_price,
                    annual_discount_pct, uptime_sla_pct, backup_frequency_hours, is_active,
                    created_at
                ) VALUES (
                    :plan_id, :plan_name, :description, :max_users, :max_patients,
                    :storage_gb, to_jsonb(:modules), :monthly_price, :annual_price,
                    :annual_discount_pct, :uptime_sla_pct, :backup_frequency_hours, :is_active,
                    :created_at
                )
                ON CONFLICT (plan_id) DO UPDATE SET
                    plan_name = EXCLUDED.plan_name,
                    description = EXCLUDED.description,
                    max_users = EXCLUDED.max_users,
                    max_patients = EXCLUDED.max_patients,
                    storage_gb = EXCLUDED.storage_gb,
                    modules_included = EXCLUDED.modules_included,
                    monthly_price = EXCLUDED.monthly_price,
                    annual_price = EXCLUDED.annual_price,
                    annual_discount_pct = EXCLUDED.annual_discount_pct,
                    uptime_sla_pct = EXCLUDED.uptime_sla_pct,
                    backup_frequency_hours = EXCLUDED.backup_frequency_hours,
                    is_active = EXCLUDED.is_active,
                    created_at = EXCLUDED.created_at
                """
            ),
            {
                "plan_id": str(plan_uuid(plan)),
                "plan_name": plan.value,
                "description": details.display_name,
                "max_users": details.max_users,
                "max_patients": None,
                "storage_gb": 0 if plan is not SubscriptionPlan.ENTERPRISE else 0,
                "modules": list(details.features),
                "monthly_price": details.monthly_price,
                "annual_price": details.annual_price,
                "annual_discount_pct": 0.0 if details.monthly_price == 0 else round(
                    (1 - details.annual_price / (details.monthly_price * 12)) * 100, 1
                ),
                "uptime_sla_pct": 99.9,
                "backup_frequency_hours": 24,
                "is_active": True,
                "created_at": now,
            },
        )
    db.commit()
