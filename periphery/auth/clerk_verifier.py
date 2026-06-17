"""Clerk JWT verification.

Verifies Clerk-issued session JWTs (RS256) against Clerk's JWKS endpoint and
maps the verified claims into the existing :class:`AuthContext` model so Clerk
human logins are accepted alongside the existing API-key / session-token auth.

Design (per architecture decision A):
- Any validly-signed, non-expired Clerk token from the configured issuer is
  trusted as a human user.
- Default role is ``analyst`` with full classification scope, matching how
  existing session users are treated.
- An optional role claim (``public_metadata.role`` or top-level ``role``) is
  honoured if present and valid, so the deployment can grow into Clerk-managed
  roles without a code change.

JWKS keys are cached in-process and refreshed on a cache miss (handles Clerk
key rotation) with a hard floor between refreshes to avoid hammering the JWKS
endpoint under a burst of invalid ``kid``s.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import jwt
from jwt import InvalidTokenError, PyJWKClient

from periphery.auth.classification import ALL_CLASSIFICATIONS
from periphery.auth.models import AuthContext
from periphery.config import get_settings

logger = logging.getLogger(__name__)

# Roles recognised by the existing system (see auth/models.py).
_VALID_ROLES = {"admin", "analyst", "ingest", "viewer"}
_DEFAULT_ROLE = "analyst"

# Minimum seconds between forced JWKS refreshes (rotation handling) to avoid
# unbounded outbound requests when bogus tokens with unknown kids arrive.
_JWKS_MIN_REFRESH_INTERVAL = 60.0


class ClerkConfigError(RuntimeError):
    """Raised when Clerk verification is requested but not configured."""


class _JWKSCache:
    """Lazily-built, refreshable PyJWKClient wrapper keyed off the JWKS URL."""

    def __init__(self) -> None:
        self._client: PyJWKClient | None = None
        self._jwks_url: str | None = None
        self._last_refresh: float = 0.0

    def _build(self, jwks_url: str) -> PyJWKClient:
        # PyJWKClient maintains its own short-lived cache of fetched keys.
        return PyJWKClient(jwks_url, cache_keys=True, lifespan=300)

    def get_signing_key(self, token: str, jwks_url: str):
        # Rebuild if the configured URL changed (e.g. test vs live instance).
        if self._client is None or self._jwks_url != jwks_url:
            self._client = self._build(jwks_url)
            self._jwks_url = jwks_url
            self._last_refresh = time.monotonic()

        try:
            return self._client.get_signing_key_from_jwt(token)
        except Exception:
            # Possible key rotation: rebuild once if we're past the cooldown.
            now = time.monotonic()
            if now - self._last_refresh >= _JWKS_MIN_REFRESH_INTERVAL:
                logger.info("clerk_jwks_refresh url=%s", jwks_url)
                self._client = self._build(jwks_url)
                self._last_refresh = now
                return self._client.get_signing_key_from_jwt(token)
            raise


_jwks_cache = _JWKSCache()


def clerk_enabled() -> bool:
    """True when enough config is present to attempt Clerk verification."""
    settings = get_settings()
    return bool(getattr(settings, "clerk_issuer", "") and getattr(settings, "clerk_jwks_url", ""))


def looks_like_clerk_token(token: str) -> bool:
    """Cheap pre-check: is this a 3-segment JWT signed with RS256?

    Local session tokens are opaque ``secrets.token_urlsafe`` strings (no dots),
    so this lets ``get_auth_context`` try the local-session path first and only
    fall through to Clerk for things that are actually JWTs. We never trust the
    header for auth decisions — this is purely routing.
    """
    if token.count(".") != 2:
        return False
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError:
        return False
    return header.get("alg", "").upper().startswith("RS")


def _extract_role(claims: dict[str, Any]) -> str:
    """Pull an optional role claim, falling back to the default.

    Looks at ``public_metadata.role`` first (Clerk's recommended place for
    app-managed roles), then a top-level ``role`` claim. Unknown/invalid values
    fall back to the default rather than erroring, so a misconfigured claim can
    never escalate privilege beyond the default.
    """
    pub = claims.get("public_metadata")
    if isinstance(pub, dict):
        role = pub.get("role")
        if isinstance(role, str) and role in _VALID_ROLES:
            return role
    role = claims.get("role")
    if isinstance(role, str) and role in _VALID_ROLES:
        return role
    return _DEFAULT_ROLE


def verify_clerk_token(token: str) -> AuthContext | None:
    """Verify a Clerk JWT and map it to an AuthContext.

    Returns ``None`` if the token is not a valid Clerk token (bad signature,
    expired, wrong issuer, etc.) so the caller can treat it as an auth failure.
    Returns an ``AuthContext`` on success.
    """
    if not clerk_enabled():
        return None

    settings = get_settings()
    issuer: str = settings.clerk_issuer
    jwks_url: str = settings.clerk_jwks_url
    audience: str = getattr(settings, "clerk_audience", "") or None
    leeway = getattr(settings, "clerk_leeway_seconds", 10)

    try:
        signing_key = _jwks_cache.get_signing_key(token, jwks_url)
    except Exception as exc:  # noqa: BLE001 — any JWKS failure => not verified
        logger.warning("clerk_jwks_lookup_failed err=%s", exc)
        return None

    decode_kwargs: dict[str, Any] = {
        "algorithms": ["RS256"],
        "issuer": issuer,
        "leeway": leeway,
        "options": {"require": ["exp", "iat", "iss"]},
    }
    if audience:
        decode_kwargs["audience"] = audience
    else:
        # Clerk default session tokens have no `aud`; don't require/verify it.
        decode_kwargs["options"]["verify_aud"] = False

    try:
        claims = jwt.decode(token, signing_key.key, **decode_kwargs)
    except InvalidTokenError as exc:
        logger.info("clerk_token_invalid err=%s", exc)
        return None

    # Optional defence-in-depth: if `azp` (authorized party) claim is present and
    # an allowlist is configured, enforce it. Clerk sets `azp` to the origin.
    allowed_parties = getattr(settings, "clerk_authorized_parties", "")
    azp = claims.get("azp")
    if allowed_parties and azp is not None:
        allow = {p.strip() for p in allowed_parties.split(",") if p.strip()}
        if azp not in allow:
            logger.warning("clerk_azp_rejected azp=%s", azp)
            return None

    subject = claims.get("sub")
    if not subject:
        logger.info("clerk_token_missing_sub")
        return None

    role = _extract_role(claims)
    # Display label: prefer email/name claims if a JWT template adds them.
    label = (
        claims.get("email")
        or claims.get("name")
        or claims.get("username")
        or f"clerk:{subject}"
    )

    return AuthContext(
        auth_type="clerk",
        user_id=subject,
        role=role,
        classification_scope=ALL_CLASSIFICATIONS,
        label=str(label),
    )
