"""Tests for the three new insurance_service functions added in the reception module.

Tests run against an in-memory SQLite DB (same setup as test_visit_service.py).
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.visit import PatientInsurance
from app.services.insurance_service import (
    create_insurance_policy,
    get_patient_policies,
    update_verification_status,
)


# ---------------------------------------------------------------------------
# create_insurance_policy
# ---------------------------------------------------------------------------

class TestCreateInsurancePolicy:
    def test_creates_with_pending_status(self, db_session: Session, sample_patient_id):
        policy = create_insurance_policy(
            db=db_session,
            patient_id=str(sample_patient_id),
            insurer_name="AAR Insurance",
            policy_number="AAR-001",
        )
        assert policy.verification_status == "pending"
        assert policy.is_active is True
        assert policy.verified_at is None

    def test_creates_with_correct_patient(self, db_session: Session, sample_patient_id):
        policy = create_insurance_policy(
            db=db_session,
            patient_id=str(sample_patient_id),
            insurer_name="Jubilee",
            policy_number="JUB-999",
        )
        assert policy.patient_id == sample_patient_id

    def test_stores_insurer_and_policy_number(self, db_session: Session, sample_patient_id):
        policy = create_insurance_policy(
            db=db_session,
            patient_id=str(sample_patient_id),
            insurer_name="Old Mutual",
            policy_number="OM-XYZ",
        )
        assert policy.insurer_name == "Old Mutual"
        assert policy.policy_number == "OM-XYZ"

    def test_optional_fields_stored(self, db_session: Session, sample_patient_id):
        exp = date.today() + timedelta(days=365)
        policy = create_insurance_policy(
            db=db_session,
            patient_id=str(sample_patient_id),
            insurer_name="Britam",
            policy_number="BRT-100",
            coverage_limit=75000.00,
            expiry_date=exp,
        )
        assert policy.coverage_limit == 75000.00
        assert policy.expiry_date == exp

    def test_optional_fields_default_to_none(self, db_session: Session, sample_patient_id):
        policy = create_insurance_policy(
            db=db_session,
            patient_id=str(sample_patient_id),
            insurer_name="CIC",
            policy_number="CIC-001",
        )
        assert policy.coverage_limit is None
        assert policy.expiry_date is None

    def test_insurance_id_is_auto_generated(self, db_session: Session, sample_patient_id):
        policy = create_insurance_policy(
            db=db_session,
            patient_id=str(sample_patient_id),
            insurer_name="Resolution",
            policy_number="RES-001",
        )
        assert policy.insurance_id is not None

    def test_persisted_to_db(self, db_session: Session, sample_patient_id):
        """Verify the record is actually committed and readable from DB."""
        policy = create_insurance_policy(
            db=db_session,
            patient_id=str(sample_patient_id),
            insurer_name="ICEA Lion",
            policy_number="ICEA-001",
        )
        fetched = db_session.query(PatientInsurance).filter(
            PatientInsurance.insurance_id == policy.insurance_id
        ).first()
        assert fetched is not None
        assert fetched.insurer_name == "ICEA Lion"

    def test_multiple_policies_for_same_patient(self, db_session: Session, sample_patient_id):
        p1 = create_insurance_policy(db_session, str(sample_patient_id), "Insurer A", "POL-A")
        p2 = create_insurance_policy(db_session, str(sample_patient_id), "Insurer B", "POL-B")
        assert p1.insurance_id != p2.insurance_id
        count = db_session.query(PatientInsurance).filter(
            PatientInsurance.patient_id == sample_patient_id
        ).count()
        assert count == 2


# ---------------------------------------------------------------------------
# get_patient_policies
# ---------------------------------------------------------------------------

class TestGetPatientPolicies:
    def test_returns_empty_for_no_policies(self, db_session: Session, sample_patient_id):
        policies = get_patient_policies(db=db_session, patient_id=str(sample_patient_id))
        assert policies == []

    def test_returns_all_policies_for_patient(self, db_session: Session, sample_patient_id):
        create_insurance_policy(db_session, str(sample_patient_id), "Insurer A", "P-001")
        create_insurance_policy(db_session, str(sample_patient_id), "Insurer B", "P-002")
        policies = get_patient_policies(db=db_session, patient_id=str(sample_patient_id))
        assert len(policies) == 2

    def test_returns_newest_first(self, db_session: Session, sample_patient_id):
        """Policies are ordered by created_at descending."""
        p1 = create_insurance_policy(db_session, str(sample_patient_id), "First", "P-001")
        p2 = create_insurance_policy(db_session, str(sample_patient_id), "Second", "P-002")
        policies = get_patient_policies(db=db_session, patient_id=str(sample_patient_id))
        # p2 was inserted last — should come first
        assert policies[0].insurance_id == p2.insurance_id
        assert policies[1].insurance_id == p1.insurance_id

    def test_does_not_return_other_patients_policies(
        self, db_session: Session, sample_patient_id, sample_patient
    ):
        # Second patient
        from app.models.visit import Patient
        other = Patient(id=uuid.uuid4(), hospital_id="hosp-001")
        db_session.add(other)
        db_session.commit()

        create_insurance_policy(db_session, str(sample_patient_id), "Mine", "P-001")
        create_insurance_policy(db_session, str(other.id), "Theirs", "P-999")

        policies = get_patient_policies(db=db_session, patient_id=str(sample_patient_id))
        assert len(policies) == 1
        assert policies[0].insurer_name == "Mine"

    def test_accepts_uuid_object_as_patient_id(self, db_session: Session, sample_patient_id):
        """patient_id can be passed as a UUID object, not just a string."""
        create_insurance_policy(db_session, str(sample_patient_id), "Insurer", "P-001")
        # Pass as UUID directly
        policies = get_patient_policies(db=db_session, patient_id=sample_patient_id)
        assert len(policies) == 1


# ---------------------------------------------------------------------------
# update_verification_status
# ---------------------------------------------------------------------------

class TestUpdateVerificationStatus:
    def _make_policy(self, db_session, sample_patient_id, **kwargs):
        return create_insurance_policy(
            db_session,
            str(sample_patient_id),
            kwargs.get("insurer_name", "Default Insurer"),
            kwargs.get("policy_number", "DEF-001"),
        )

    def test_sets_verified_status(self, db_session: Session, sample_patient_id):
        policy = self._make_policy(db_session, sample_patient_id)
        updated = update_verification_status(
            db=db_session,
            insurance_id=str(policy.insurance_id),
            verification_status="verified",
        )
        assert updated.verification_status == "verified"

    def test_sets_rejected_status(self, db_session: Session, sample_patient_id):
        policy = self._make_policy(db_session, sample_patient_id)
        updated = update_verification_status(
            db=db_session,
            insurance_id=str(policy.insurance_id),
            verification_status="rejected",
        )
        assert updated.verification_status == "rejected"

    def test_sets_verified_at_timestamp(self, db_session: Session, sample_patient_id):
        """verified_at must be populated on any status update."""
        policy = self._make_policy(db_session, sample_patient_id)
        assert policy.verified_at is None   # starts as None
        updated = update_verification_status(
            db=db_session,
            insurance_id=str(policy.insurance_id),
            verification_status="verified",
        )
        assert updated.verified_at is not None

    def test_sets_verified_at_on_rejection(self, db_session: Session, sample_patient_id):
        policy = self._make_policy(db_session, sample_patient_id)
        updated = update_verification_status(
            db=db_session,
            insurance_id=str(policy.insurance_id),
            verification_status="rejected",
        )
        assert updated.verified_at is not None

    def test_returns_none_for_non_existent_id(self, db_session: Session):
        result = update_verification_status(
            db=db_session,
            insurance_id=str(uuid.uuid4()),
            verification_status="verified",
        )
        assert result is None

    def test_change_is_persisted_to_db(self, db_session: Session, sample_patient_id):
        """Verify the change is actually committed and visible from a fresh query."""
        policy = self._make_policy(db_session, sample_patient_id)
        update_verification_status(db_session, str(policy.insurance_id), "verified")

        db_session.expire(policy)   # force reload from DB
        refreshed = db_session.query(PatientInsurance).filter(
            PatientInsurance.insurance_id == policy.insurance_id
        ).first()
        assert refreshed.verification_status == "verified"
        assert refreshed.verified_at is not None

    def test_can_update_status_twice(self, db_session: Session, sample_patient_id):
        """Idempotent — can be called again (e.g. reverting a mistake)."""
        policy = self._make_policy(db_session, sample_patient_id)
        update_verification_status(db_session, str(policy.insurance_id), "verified")
        updated = update_verification_status(db_session, str(policy.insurance_id), "rejected")
        assert updated.verification_status == "rejected"
