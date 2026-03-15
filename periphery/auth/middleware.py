"""FastAPI dependencies for authentication."""

from __future__ import annotations

from fastapi import Header, HTTPException

from periphery.auth.models import AuthenticatedUser
from periphery.auth.persistence import get_user, validate_session


async def get_current_user(
    authorization: str | None = Header(None),
) -> AuthenticatedUser:
    """Extract and validate the Bearer token, returning the authenticated user.

    Use as a FastAPI dependency on protected routes.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    token = authorization[7:]
    session = await validate_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = await get_user(session.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return AuthenticatedUser(
        user_id=user.user_id,
        org_id=user.org_id,
        display_name=user.display_name,
        role=user.role,
    )


async def get_optional_user(
    authorization: str | None = Header(None),
) -> AuthenticatedUser | None:
    """Like get_current_user but returns None instead of raising 401.

    Use during migration period on routes that should work both
    authenticated and unauthenticated.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:]
    session = await validate_session(token)
    if not session:
        return None

    user = await get_user(session.user_id)
    if not user:
        return None

    return AuthenticatedUser(
        user_id=user.user_id,
        org_id=user.org_id,
        display_name=user.display_name,
        role=user.role,
    )


def require_role(*roles: str):
    """Return a dependency that checks the user has one of the given roles."""
    async def _check(
        user: AuthenticatedUser = Header(None),  # replaced at call-site
    ) -> AuthenticatedUser:
        # This is called after get_current_user via Depends chain
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return _check
