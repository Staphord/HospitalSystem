import pytest
from fastapi import HTTPException

from app.services.visit_status_service import get_visit_by_id, transition_visit_status


def test_transition_rejects_invalid_visit_id_before_database_access() -> None:
    with pytest.raises(HTTPException) as exc_info:
        transition_visit_status(None, "not-a-uuid", "completed")

    assert exc_info.value.status_code == 400


def test_get_visit_rejects_invalid_visit_id_before_database_access() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_visit_by_id(None, "not-a-uuid")

    assert exc_info.value.status_code == 400
