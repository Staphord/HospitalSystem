import pytest
from pydantic import ValidationError

from app.api.v1.auth.schemas import (
    FirstLoginChangePasswordRequest, PasswordResetConfirm, SignupRequest,
)


def valid_signup(**overrides):
    values = dict(hospital_name="Hospital", admin_username="admin_user", admin_password="N3w!CedarRiver", admin_email="admin@example.com")
    values.update(overrides)
    return values


def test_signup_schema_normalizes_and_validates():
    assert SignupRequest(**valid_signup(subscription_billing_cycle="ANNUAL")).subscription_billing_cycle == "annual"
    with pytest.raises(ValidationError): SignupRequest(**valid_signup(subscription_billing_cycle="weekly"))
    with pytest.raises(ValidationError): SignupRequest(**valid_signup(admin_username="ab"))
    with pytest.raises(ValidationError): SignupRequest(**valid_signup(admin_password="weak"))


def test_password_schemas_validate_strength():
    assert PasswordResetConfirm(token="t", new_password="N3w!CedarRiver").new_password
    assert FirstLoginChangePasswordRequest(username="u", temp_password="t", new_password="N3w!CedarRiver")
    with pytest.raises(ValidationError): PasswordResetConfirm(token="t", new_password="weak")
