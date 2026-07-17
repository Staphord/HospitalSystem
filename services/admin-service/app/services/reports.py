"""Hospital-admin reports (FR-57)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.admin import Bed
from app.models.user import User
from app.services.admin import beds_summary


def _parse_range(from_date: date | None, to_date: date | None) -> tuple[date, date]:
    today = date.today()
    end = to_date or today
    start = from_date or (end - timedelta(days=30))
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="'from' must be <= 'to'")
    if (end - start).days > 366:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Date range cannot exceed 12 months")
    return start, end


def patient_census(db: Session, from_date: date | None, to_date: date | None) -> dict[str, Any]:
    start, end = _parse_range(from_date, to_date)
    try:
        patients = db.execute(
            text(
                "SELECT COUNT(*) FROM patients WHERE is_active = true"
            )
        ).scalar() or 0
    except Exception:
        patients = 0
    try:
        rows = db.execute(
            text(
                """
                SELECT visit_date::date AS d, COUNT(*) AS c
                FROM visits
                WHERE visit_date >= :start AND visit_date <= :end
                GROUP BY visit_date::date
                ORDER BY d
                """
            ),
            {"start": start, "end": end},
        ).fetchall()
        by_day = [{"date": str(r[0]), "visits": int(r[1])} for r in rows]
        visit_total = sum(x["visits"] for x in by_day)
    except Exception:
        by_day = []
        visit_total = 0
    return {
        "from": str(start),
        "to": str(end),
        "active_patients": int(patients),
        "total_visits": visit_total,
        "visits_by_day": by_day,
    }


def wait_times(db: Session, from_date: date | None, to_date: date | None) -> dict[str, Any]:
    start, end = _parse_range(from_date, to_date)
    try:
        rows = db.execute(
            text(
                """
                SELECT queue_type,
                       AVG(EXTRACT(EPOCH FROM (called_at - created_at))) AS avg_wait_seconds,
                       COUNT(*) AS samples
                FROM queues
                WHERE called_at IS NOT NULL
                  AND created_at::date >= :start
                  AND created_at::date <= :end
                GROUP BY queue_type
                ORDER BY queue_type
                """
            ),
            {"start": start, "end": end},
        ).fetchall()
        items = [
            {
                "queue_type": r[0],
                "avg_wait_seconds": float(r[1]) if r[1] is not None else None,
                "samples": int(r[2]),
            }
            for r in rows
        ]
    except Exception:
        items = []
    return {"from": str(start), "to": str(end), "by_queue_type": items}


def discharges(db: Session, from_date: date | None, to_date: date | None) -> dict[str, Any]:
    start, end = _parse_range(from_date, to_date)
    # Prefer ward admissions when table exists
    try:
        row = db.execute(
            text(
                """
                SELECT COUNT(*) FROM admissions
                WHERE status = 'discharged'
                  AND COALESCE(discharge_date::date, admission_date::date) >= :start
                  AND COALESCE(discharge_date::date, admission_date::date) <= :end
                """
            ),
            {"start": start, "end": end},
        ).scalar()
        return {
            "from": str(start),
            "to": str(end),
            "discharged": int(row or 0),
            "source": "admissions",
        }
    except Exception:
        pass
    try:
        rows = db.execute(
            text(
                """
                SELECT status, COUNT(*) AS c
                FROM visits
                WHERE visit_date >= :start AND visit_date <= :end
                  AND status IN ('completed', 'cancelled', 'discharged')
                GROUP BY status
                """
            ),
            {"start": start, "end": end},
        ).fetchall()
        by_status = {r[0]: int(r[1]) for r in rows}
    except Exception:
        by_status = {}
    return {
        "from": str(start),
        "to": str(end),
        "completed": by_status.get("completed", 0),
        "cancelled": by_status.get("cancelled", 0),
        "discharged": by_status.get("discharged", 0),
        "note": "Proxy from visit status (admissions table unavailable)",
        "source": "visits",
    }


def bed_occupancy(db: Session) -> dict[str, Any]:
    summary = beds_summary(db)
    try:
        by_ward = db.execute(
            text(
                """
                SELECT ward_name,
                       COUNT(*) FILTER (WHERE is_active) AS total,
                       COUNT(*) FILTER (WHERE is_active AND is_available) AS available
                FROM beds
                GROUP BY ward_name
                ORDER BY ward_name
                """
            )
        ).fetchall()
        wards = [
            {
                "ward_name": r[0],
                "total": int(r[1]),
                "available": int(r[2]),
                "occupied": int(r[1]) - int(r[2]),
            }
            for r in by_ward
        ]
    except Exception:
        wards = []
    return {**summary, "by_ward": wards}


def revenue_summary() -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Revenue reporting requires billing tables which are not yet implemented",
    )


def dashboard(db: Session) -> dict[str, Any]:
    today = date.today()
    users_active = (
        db.query(User)
        .filter(User.is_active.is_(True), User.deleted_at.is_(None))
        .count()
    )
    try:
        visits_today = db.execute(
            text("SELECT COUNT(*) FROM visits WHERE visit_date = :d"),
            {"d": today},
        ).scalar() or 0
    except Exception:
        visits_today = 0
    try:
        open_queues = db.execute(
            text(
                "SELECT COUNT(*) FROM queues WHERE status IN ('waiting', 'in_progress')"
            )
        ).scalar() or 0
    except Exception:
        open_queues = 0
    beds = beds_summary(db)
    return {
        "active_users": users_active,
        "visits_today": int(visits_today),
        "open_queue_entries": int(open_queues),
        "beds": beds,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
