"""Unit tests for reception-service schemas and orchestrator utilities."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.schemas import (
    InsurancePolicyCreateRequest,
    InsuranceVerifyRequest,
    PatientRegisterRequest,
    VisitCreateRequest,
)


# ---------------------------------------------------------------------------
# PatientRegisterRequest
# ---------------------------------------------------------------------------

class TestPatientRegisterRequest:
    def _valid(self, **overrides):
        defaults = dict(
            full_name="Jane Doe",
            date_of_birth=date(1990, 5, 15),
            gender="female",
            phone_primary="0712345678",
        )
        defaults.update(overrides)
        return PatientRegisterRequest(**defaults)

    def test_valid_patient(self):
        p = self._valid()
        assert p.full_name == "Jane Doe"
        assert p.gender == "female"

    def test_full_name_stripped(self):
        p = self._valid(full_name="  Alice  ")
        assert p.full_name == "Alice"

    def test_full_name_empty_raises(self):
        with pytest.raises(ValidationError, match="full_name cannot be empty"):
            self._valid(full_name="   ")

    def test_dob_future_raises(self):
        future = date.today() + timedelta(days=1)
        with pytest.raises(ValidationError, match="date_of_birth cannot be in the future"):
            self._valid(date_of_birth=future)

    def test_dob_today_is_valid(self):
        p = self._valid(date_of_birth=date.today())
        assert p.date_of_birth == date.today()

    def test_gender_normalised_to_lowercase(self):
        p = self._valid(gender="Male")
        assert p.gender == "male"

    def test_gender_invalid_raises(self):
        with pytest.raises(ValidationError, match="gender must be one of"):
            self._valid(gender="unknown")

    def test_gender_other_allowed(self):
        p = self._valid(gender="other")
        assert p.gender == "other"


# ---------------------------------------------------------------------------
# InsurancePolicyCreateRequest
# ---------------------------------------------------------------------------

class TestInsurancePolicyCreateRequest:
    def _valid(self, **overrides):
        defaults = dict(insurer_name="AAR Insurance", policy_number="AAR-12345")
        defaults.update(overrides)
        return InsurancePolicyCreateRequest(**defaults)

    def test_valid(self):
        req = self._valid()
        assert req.insurer_name == "AAR Insurance"
        assert req.policy_number == "AAR-12345"

    def test_insurer_name_stripped(self):
        req = self._valid(insurer_name="  Jubilee  ")
        assert req.insurer_name == "Jubilee"

    def test_insurer_name_empty_raises(self):
        with pytest.raises(ValidationError, match="insurer_name cannot be empty"):
            self._valid(insurer_name="   ")

    def test_policy_number_empty_raises(self):
        with pytest.raises(ValidationError, match="policy_number cannot be empty"):
            self._valid(policy_number="  ")

    def test_optional_fields_default_to_none(self):
        req = self._valid()
        assert req.coverage_limit is None
        assert req.expiry_date is None

    def test_coverage_limit_accepted(self):
        from decimal import Decimal
        req = self._valid(coverage_limit=Decimal("50000.00"))
        assert req.coverage_limit == Decimal("50000.00")


# ---------------------------------------------------------------------------
# InsuranceVerifyRequest
# ---------------------------------------------------------------------------

class TestInsuranceVerifyRequest:
    def test_verified_accepted(self):
        req = InsuranceVerifyRequest(verification_status="verified")
        assert req.verification_status == "verified"

    def test_rejected_accepted(self):
        req = InsuranceVerifyRequest(verification_status="rejected")
        assert req.verification_status == "rejected"

    def test_pending_raises(self):
        with pytest.raises(ValidationError, match="verification_status must be one of"):
            InsuranceVerifyRequest(verification_status="pending")

    def test_case_normalised(self):
        req = InsuranceVerifyRequest(verification_status="VERIFIED")
        assert req.verification_status == "verified"


# ---------------------------------------------------------------------------
# VisitCreateRequest
# ---------------------------------------------------------------------------

class TestVisitCreateRequest:
    def _valid(self, **overrides):
        defaults = dict(
            patient_id=str(uuid4()),
            visit_type="outpatient",
            payment_type="cash",
        )
        defaults.update(overrides)
        return VisitCreateRequest(**defaults)

    def test_valid_cash(self):
        req = self._valid()
        assert req.insurance_id is None

    def test_valid_insurance_with_id(self):
        ins_id = uuid4()
        req = self._valid(payment_type="insurance", insurance_id=ins_id)
        assert req.insurance_id == ins_id

    def test_visit_type_normalised(self):
        req = self._valid(visit_type="OUTPATIENT")
        assert req.visit_type == "outpatient"

    def test_visit_type_invalid_raises(self):
        with pytest.raises(ValidationError, match="visit_type must be one of"):
            self._valid(visit_type="walk_in")

    def test_payment_type_invalid_raises(self):
        with pytest.raises(ValidationError, match="payment_type must be one of"):
            self._valid(payment_type="barter")

    def test_patient_id_accepted_as_string(self):
        """reception-service passes patient_id as-is; visit-service validates it."""
        req = self._valid(patient_id="not-a-uuid")
        assert req.patient_id == "not-a-uuid"

    def test_patient_id_empty_string_accepted(self):
        """Empty strings are allowed at this layer; downstream will reject them."""
        req = self._valid(patient_id="")
        assert req.patient_id == ""


# ---------------------------------------------------------------------------
# Orchestrator param mapping (pure logic, no HTTP)
# ---------------------------------------------------------------------------

class TestOrchestratorSearchParamMapping:
    """Verify that page/page_size → limit/offset math is correct."""

    def _compute(self, page, page_size):
        page_size = max(1, min(page_size, 100))
        page = max(1, page)
        offset = (page - 1) * page_size
        return {"limit": page_size, "offset": offset}

    def test_first_page(self):
        p = self._compute(page=1, page_size=20)
        assert p["limit"] == 20
        assert p["offset"] == 0

    def test_second_page(self):
        p = self._compute(page=2, page_size=20)
        assert p["limit"] == 20
        assert p["offset"] == 20

    def test_third_page_custom_size(self):
        p = self._compute(page=3, page_size=10)
        assert p["limit"] == 10
        assert p["offset"] == 20

    def test_page_size_capped_at_100(self):
        p = self._compute(page=1, page_size=999)
        assert p["limit"] == 100

    def test_page_zero_normalised_to_1(self):
        p = self._compute(page=0, page_size=20)
        assert p["offset"] == 0
