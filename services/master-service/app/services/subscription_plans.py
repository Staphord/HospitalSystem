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


# Deterministic UUIDs for canonical plans so they stay stable across restarts.
PLAN_UUIDS: dict[SubscriptionPlan, uuid.UUID] = {
    SubscriptionPlan.FREE_TRIAL: uuid.UUID("11111111-1111-1111-1111-111111111111"),
    SubscriptionPlan.BASIC: uuid.UUID("22222222-2222-2222-2222-222222222222"),
    SubscriptionPlan.STANDARD: uuid.UUID("33333333-3333-3333-3333-333333333333"),
    SubscriptionPlan.PREMIUM: uuid.UUID("44444444-4444-4444-4444-444444444444"),
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
    max_users: int | None
    storage_gb: int
    features: frozenset[str] = field(default_factory=frozenset)


# Plan hierarchy used for upgrade/downgrade validation.
PLAN_RANK: dict[SubscriptionPlan, int] = {
    SubscriptionPlan.FREE_TRIAL: 0,
    SubscriptionPlan.BASIC: 1,
    SubscriptionPlan.STANDARD: 2,
    SubscriptionPlan.PREMIUM: 3,
}


PLAN_CATALOG: dict[SubscriptionPlan, PlanDetails] = {
    SubscriptionPlan.FREE_TRIAL: PlanDetails(
        display_name="Free Trial",
        monthly_price=0,
        annual_price=0,
        trial_days=30,  # 30-day default trial period
        max_users=3,
        storage_gb=10,
        features=frozenset({"reception", "triage", "consultation", "pharmacy", "billing", "laboratory", "radiology", "ward", "reports", "insurance", "analytics"}),
    ),
    SubscriptionPlan.BASIC: PlanDetails(
        display_name="Basic",
        monthly_price=99,
        annual_price=990,
        trial_days=0,
        max_users=20,
        storage_gb=10,
        features=frozenset({"reception", "triage", "consultation", "pharmacy", "billing"}),
    ),
    SubscriptionPlan.STANDARD: PlanDetails(
        display_name="Standard",
        monthly_price=299,
        annual_price=2990,
        trial_days=0,
        max_users=100,
        storage_gb=50,
        features=frozenset({"reception", "triage", "consultation", "laboratory", "radiology", "pharmacy", "billing", "reports"}),
    ),
    SubscriptionPlan.PREMIUM: PlanDetails(
        display_name="Premium",
        monthly_price=599,
        annual_price=5990,
        trial_days=0,
        max_users=None,  # unlimited
        storage_gb=200,
        features=frozenset({
            "reception", "triage", "consultation", "laboratory", "radiology",
            "pharmacy", "billing", "ward", "reports", "insurance", "analytics"
        }),
    ),
}


import logging
logger = logging.getLogger("hospital.subscriptions")

# Thread-safe local memory cache for plan specifications fetched from DB to avoid redundant reads during active sessions.
_db_plans_cache: dict[str, PlanDetails] = {}


def invalidate_plan_cache(plan_name: str) -> None:
    """Remove a specific plan from the database cache."""
    _db_plans_cache.pop(plan_name, None)


def get_plan(plan: str | SubscriptionPlan, db: Any = None) -> PlanDetails:
    """Return plan details from the database if db session is provided; otherwise, use the static catalog."""
    plan_val = plan.value if isinstance(plan, SubscriptionPlan) else plan
    
    # Check cache first
    if plan_val in _db_plans_cache:
        return _db_plans_cache[plan_val]
        
    if db is not None:
        try:
            from app.models.saas import SubscriptionPlan as DBPlan
            db_row = db.query(DBPlan).filter(DBPlan.plan_name == plan_val).first()
            if db_row:
                details = PlanDetails(
                    display_name=db_row.description or db_row.plan_name,
                    monthly_price=int(db_row.monthly_price),
                    annual_price=int(db_row.annual_price),
                    trial_days=30 if plan_val == "free_trial" else 0,
                    max_users=db_row.max_users or 0,
                    storage_gb=db_row.storage_gb,
                    features=frozenset(db_row.modules_included or []),
                )
                _db_plans_cache[plan_val] = details
                return details
        except Exception:
            logger.warning("Failed to query plan '%s' from database, using code catalog fallback", plan_val)

    # Fallback to local catalog
    try:
        canonical_plan = SubscriptionPlan(plan_val)
        return PLAN_CATALOG[canonical_plan]
    except ValueError:
        # Fallback for dynamic plan names not matching canonical enums
        return PlanDetails(
            display_name=plan_val.replace("_", " ").title(),
            monthly_price=0,
            annual_price=0,
            trial_days=0,
            max_users=0,
            storage_gb=0,
            features=frozenset(),
        )


def plan_rank(plan: str | SubscriptionPlan) -> int:
    """Return numeric rank of a plan (higher == more privileged)."""
    if isinstance(plan, str):
        try:
            plan = SubscriptionPlan(plan)
        except ValueError:
            return 0
    return PLAN_RANK.get(plan, 0)


def is_upgrade(from_plan: str | SubscriptionPlan, to_plan: str | SubscriptionPlan) -> bool:
    """True if to_plan is strictly higher rank than from_plan."""
    return plan_rank(to_plan) > plan_rank(from_plan)


def is_downgrade(from_plan: str | SubscriptionPlan, to_plan: str | SubscriptionPlan) -> bool:
    """True if to_plan is strictly lower rank than from_plan."""
    return plan_rank(to_plan) < plan_rank(from_plan)


def valid_plan_transition(from_plan: str | SubscriptionPlan, to_plan: str | SubscriptionPlan) -> bool:
    """A transition is valid if it is not to free_trial (trials are one-time only)."""
    if isinstance(to_plan, str):
        try:
            to_plan = SubscriptionPlan(to_plan)
        except ValueError:
            return True
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
        "storage_gb": details.storage_gb,
        "features": sorted(details.features),
        "rank": PLAN_RANK[plan],
    }


# Cache flag to avoid database sync checks once verified on startup.
_db_synced = False


def sync_plans_to_db(db) -> None:
    """Upsert canonical plans into the subscription_plans table."""
    global _db_synced
    if _db_synced:
        return

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
                ON CONFLICT (plan_id) DO NOTHING
                """
            ),
            {
                "plan_id": str(plan_uuid(plan)),
                "plan_name": plan.value,
                "description": details.display_name,
                "max_users": details.max_users,
                "max_patients": None,
                "storage_gb": details.storage_gb,
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
    _db_synced = True
