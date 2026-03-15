"""ID generators and token utilities for the auth system."""

from __future__ import annotations

import secrets
import string

_BASE62 = string.ascii_letters + string.digits


def generate_org_id() -> str:
    """Generate a 16-character base62 organization ID."""
    return "".join(secrets.choice(_BASE62) for _ in range(16))


def generate_user_id() -> str:
    """Generate a 24-character base62 user ID."""
    return "".join(secrets.choice(_BASE62) for _ in range(24))


def generate_session_token() -> str:
    """Generate a 64-character URL-safe session token."""
    return secrets.token_urlsafe(48)


def generate_challenge_id() -> str:
    """Generate a challenge identifier for QR auth."""
    return secrets.token_urlsafe(24)


def generate_challenge_code() -> str:
    """Generate a 6-digit numeric passcode for QR auth."""
    return "".join(secrets.choice(string.digits) for _ in range(6))
