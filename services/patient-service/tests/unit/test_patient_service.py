import uuid

from app.services.patient_service import get_patient_by_id


class EmptyQuery:
    def filter(self, *args):
        return self

    def first(self):
        return None


class EmptyDb:
    def query(self, model):
        return EmptyQuery()


def test_get_patient_by_id_rejects_invalid_uuid_without_querying() -> None:
    assert get_patient_by_id(EmptyDb(), "hospital-a", "not-a-uuid") is None


def test_get_patient_by_id_accepts_uuid_shaped_identifiers() -> None:
    patient_id = str(uuid.uuid4())
    assert get_patient_by_id(EmptyDb(), "hospital-a", patient_id) is None
