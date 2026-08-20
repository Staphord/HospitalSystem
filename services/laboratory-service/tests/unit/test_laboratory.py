from app.core.security import TokenPayload
from app.services.laboratory import _user_identifier


def _user(**overrides: object) -> TokenPayload:
    values = {
        "sub": "subject-1",
        "preferred_username": None,
        "email": None,
        "realm_access": {"roles": []},
        "raw": {},
    }
    values.update(overrides)
    return TokenPayload(**values)


def test_user_identifier_prefers_username_then_email_then_subject() -> None:
    assert _user_identifier(_user(preferred_username="lab.tech", email="lab@example.com")) == "lab.tech"
    assert _user_identifier(_user(email="lab@example.com")) == "lab@example.com"
    assert _user_identifier(_user()) == "subject-1"
