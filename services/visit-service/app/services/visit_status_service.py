from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.visit import Visit

_VALID_TRANSITIONS = {
    "registered": ["triaged", "cancelled"],
    "triaged": ["in_consultation", "cancelled"],
    "in_consultation": ["in_lab", "in_pharmacy", "completed", "cancelled"],
    "in_lab": ["in_consultation", "completed", "cancelled"],
    "in_pharmacy": ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}


def transition_visit_status(db: Session, visit_id: str, new_status: str) -> Visit:
    import uuid as uuid_mod
    try:
        vid = uuid_mod.UUID(visit_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid visit_id UUID")

    visit = db.query(Visit).filter(Visit.visit_id == vid).first()
    if not visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")

    allowed = _VALID_TRANSITIONS.get(visit.status, [])
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition visit from '{visit.status}' to '{new_status}'",
        )

    visit.status = new_status
    visit.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(visit)
    return visit


def get_visit_by_id(db: Session, visit_id: str) -> Visit:
    import uuid as uuid_mod
    try:
        vid = uuid_mod.UUID(visit_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid visit_id UUID")
    visit = db.query(Visit).filter(Visit.visit_id == vid).first()
    if not visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")
    return visit
