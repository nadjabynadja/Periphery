"""FastAPI dependencies for authentication.

Supports three auth methods:
1. X-API-Key header → validated against api_keys table
2. Authorization: Bearer → validated as session token
3. X-API-Key matching legacy admin_api_key from config → backward compat
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from periphery.auth.classification import ALL_CLASSIFICATIONS, DataClassification
from periphery.auth.models import AuthContext, AuthenticatedUser
from periphery.auth.rate_limiter import failed_auth_tracker, rate_limiter

logger = logging.getLogger(__name__)


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_auth_context(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
) -> AuthContext:
    """Unified auth dependency. Tries API key, then session token, then legacy admin key.

    Returns an AuthContext with role, classification scope, and identity info.
    """
    client_ip = _get_client_ip(request)

    # Check if IP is blocked from too many failures
    if failed_auth_tracker.is_blocked(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many failed authentication attempts. Try again later.",
        )

    # --- Try 1: X-API-Key header (new API key system) ---
    if x_api_key:
        # Check legacy admin key first (fast path, no DB lookup)
        from periphery.config import get_settings
        settings = get_settings()
        if settings.admin_api_key and x_api_key == settings.admin_api_key:
            failed_auth_tracker.clear(client_ip)
            return AuthContext(
                auth_type="admin_key",
                role="admin",
                classification_scope=ALL_CLASSIFICATIONS,
                label="Legacy Admin Key",
            )

        # Validate against api_keys table
        from periphery.auth.api_keys import validate_api_key
        api_key = await validate_api_key(x_api_key)
        if api_key:
            # Rate limit check
            if not rate_limiter.check(api_key.key_id, api_key.rate_limit_rpm):
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded ({api_key.rate_limit_rpm} requests/minute)",
                )
            failed_auth_tracker.clear(client_ip)
            return AuthContext(
                auth_type="api_key",
                key_id=api_key.key_id,
                role=api_key.role,
                classification_scope=api_key.classification_scope,
                label=api_key.label,
            )

        # API key provided but invalid
        failed_auth_tracker.record_failure(client_ip)
        logger.warning("invalid_api_key ip=%s", client_ip)
        raise HTTPException(status_code=401, detail="Invalid API key")

    # --- Try 2: Bearer token (local session token OR Clerk JWT) ---
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        from periphery.auth.persistence import get_user, validate_session

        # 2a: local opaque session token (token_urlsafe — no JWT structure).
        # Try this first so existing logins keep their fast path. Clerk session
        # JWTs are 3-segment RS256 tokens and won't match a session row.
        session = await validate_session(token)
        if session:
            user = await get_user(session.user_id)
            if user:
                failed_auth_tracker.clear(client_ip)
                return AuthContext(
                    auth_type="session",
                    user_id=user.user_id,
                    role=user.role,
                    classification_scope=ALL_CLASSIFICATIONS,  # session users get all classifications
                    label=user.display_name,
                )

        # 2b: Clerk JWT (human login via Clerk). Only attempt when the token
        # actually looks like an RS256 JWT and Clerk is configured.
        from periphery.auth.clerk_verifier import clerk_enabled, looks_like_clerk_token, verify_clerk_token
        if clerk_enabled() and looks_like_clerk_token(token):
            ctx = verify_clerk_token(token)
            if ctx is not None:
                failed_auth_tracker.clear(client_ip)
                return ctx

        failed_auth_tracker.record_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # --- No credentials ---
    raise HTTPException(status_code=401, detail="Missing authentication credentials")


async def get_current_user(
    authorization: str | None = Header(None),
) -> AuthenticatedUser:
    """Extract and validate the Bearer token, returning the authenticated user.

    Use as a FastAPI dependency on protected routes.
    Backward-compatible: only checks Bearer tokens (not API keys).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    token = authorization[7:]
    from periphery.auth.persistence import get_user, validate_session
    session = await validate_session(token)
    if session:
        user = await get_user(session.user_id)
        if user:
            return AuthenticatedUser(
                user_id=user.user_id,
                org_id=user.org_id,
                display_name=user.display_name,
                role=user.role,
            )

    # Fall back to Clerk JWT (human login). Clerk users have no local org/user
    # row, so synthesize an AuthenticatedUser from verified claims.
    from periphery.auth.clerk_verifier import clerk_enabled, looks_like_clerk_token, verify_clerk_token
    if clerk_enabled() and looks_like_clerk_token(token):
        ctx = verify_clerk_token(token)
        if ctx is not None:
            return AuthenticatedUser(
                user_id=ctx.user_id or "clerk_unknown",
                org_id=ctx.user_id or "clerk_unknown",
                display_name=ctx.label,
                role=ctx.role,
            )

    raise HTTPException(status_code=401, detail="Invalid or expired session")


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
    from periphery.auth.persistence import get_user, validate_session
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
    """Return a FastAPI dependency that checks the user has one of the given roles.

    Usage::

        @router.get("/admin-only")
        async def admin_endpoint(user: AuthenticatedUser = Depends(require_role("admin"))):
            ...
    """

    async def _check(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return _check


def require_auth_context_role(*roles: str):
    """Return a dependency that checks the AuthContext has one of the given roles.

    Works with both API keys and session tokens.
    """

    async def _check(
        ctx: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        if ctx.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return ctx
    return _check


def require_classification(*classifications: str):
    """Return a dependency that checks the AuthContext has clearance for the given classifications.

    Usage::

        @router.get("/pii-data")
        async def pii_endpoint(ctx: AuthContext = Depends(require_classification("PII"))):
            ...
    """

    async def _check(
        ctx: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        for cls in classifications:
            if cls not in ctx.classification_scope:
                raise HTTPException(
                    status_code=403,
                    detail=f"Insufficient classification clearance for {cls}",
                )
        return ctx
    return _check
