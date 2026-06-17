"""Shared security utilities for Hospital Flow microservices.

Provides password validation, input sanitization, and common security helpers.
"""

import re


class PasswordValidationError(ValueError):
    """Raised when a password does not meet the security policy."""


def validate_password(password: str) -> str:
    """Validate password against the hospital security policy.

    Policy:
    - Minimum 8 characters
    - Maximum 128 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)
    - No whitespace characters
    - No common weak patterns (e.g., "password", "123456", "qwerty")
    """
    if not password:
        raise PasswordValidationError("Password is required")
    if len(password) < 8:
        raise PasswordValidationError("Password must be at least 8 characters long")
    if len(password) > 128:
        raise PasswordValidationError("Password must not exceed 128 characters")
    if " " in password or "\t" in password or "\n" in password:
        raise PasswordValidationError("Password must not contain whitespace")
    if not re.search(r"[A-Z]", password):
        raise PasswordValidationError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        raise PasswordValidationError("Password must contain at least one lowercase letter")
    if not re.search(r"[0-9]", password):
        raise PasswordValidationError("Password must contain at least one digit")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", password):
        raise PasswordValidationError("Password must contain at least one special character")

    # Block common weak passwords
    lower = password.lower()
    common_weak = [
        "password", "123456", "12345678", "qwerty", "abc123",
        "letmein", "welcome", "admin", "login", "master",
    ]
    for weak in common_weak:
        if weak in lower:
            raise PasswordValidationError(f"Password contains a common weak pattern: '{weak}'")

    return password


def sanitize_username(username: str) -> str:
    """Sanitize a username by removing dangerous characters and normalizing.

    Rules:
    - Strip leading/trailing whitespace
    - Allow alphanumeric, underscore, hyphen, and dot
    - Must start with a letter or number
    - No consecutive special characters
    """
    username = username.strip()
    if not username:
        raise ValueError("Username is required")
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters")
    if len(username) > 255:
        raise ValueError("Username must not exceed 255 characters")
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", username):
        raise ValueError(
            "Username must start with a letter or number and contain only alphanumeric, underscore, hyphen, or dot"
        )
    if ".." in username or "--" in username or "__" in username:
        raise ValueError("Username must not contain consecutive special characters")
    return username


def redact_sensitive_data(data: dict) -> dict:
    """Return a copy of the dict with sensitive fields redacted.

    Useful for logging request/response bodies without leaking secrets.
    """
    sensitive_keys = {
        "password", "admin_password", "new_password", "old_password",
        "access_token", "refresh_token", "token", "secret_key",
        "client_secret", "api_key", "db_dsn", "db_dsn_encrypted",
        "mfa_secret", "password_hash",
    }
    result = {}
    for key, value in data.items():
        key_lower = key.lower()
        if any(sk in key_lower for sk in sensitive_keys):
            result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = redact_sensitive_data(value)
        elif isinstance(value, list):
            result[key] = [
                redact_sensitive_data(v) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            result[key] = value
    return result
