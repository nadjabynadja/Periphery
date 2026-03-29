"""API key CRUD operations.

Keys are stored in the auth database (periphery_auth.db), separate from
the main document database, to avoid write-lock contention.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from periphery.auth.classification import ALL_CLASSIFICATIONS
from periphery.auth.models import APIKey, APIKeyResponse, CreateAPIKeyRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — added to auth DB alongside organizations/users/sessions
# ---------------------------------------------------------------------------

API_KEYS_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL,
    label TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'analyst', 'ingest')),
    classification_scope TEXT NOT NULL DEFAULT '["PUBLIC"]',
    rate_limit_rpm INTEGER DEFAULT 600,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    last_used TIMESTAMP,
    is_active INTEGER DEFAULT 1,
    created_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_key_role ON api_keys(role);
CREATE INDEX IF NOT EXISTS idx_api_key_active ON api_keys(is_active);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_key(role: str) -> str:
    """Generate a prefixed API key: pk_{role}_{random}."""
    token = secrets.token_urlsafe(32)
    return f"pk_{role}_{token}"


def _generate_key_id() -> str:
    """Generate a short unique key ID."""
    return f"key_{secrets.token_urlsafe(12)}"


def _hash_key(raw_key: str) -> str:
    """Bcrypt-hash an API key for storage."""
    return bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()


def _verify_key(raw_key: str, hashed: str) -> bool:
    """Verify a raw API key against its bcrypt hash."""
    try:
        return bcrypt.checkpw(raw_key.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Auth DB connection (reuses the persistence module's connection manager)
# ---------------------------------------------------------------------------

async def _get_auth_conn():
    """Import lazily to avoid circular imports."""
    from periphery.auth.persistence import _auth_connection
    return _auth_connection()


async def ensure_api_keys_table() -> None:
    """Create the api_keys table if it doesn't exist."""
    from periphery.auth.persistence import _auth_connection, _ensure_auth_db
    await _ensure_auth_db()
    async with _auth_connection() as db:
        await db.executescript(API_KEYS_SCHEMA)
        await db.commit()
    logger.info("api_keys table ensured")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_api_key(
    request: CreateAPIKeyRequest,
    created_by: str | None = None,
) -> APIKeyResponse:
    """Create a new API key. Returns the key value ONCE (not stored in cleartext)."""
    from periphery.auth.persistence import _auth_connection

    # Validate role
    if request.role not in ("admin", "analyst", "ingest"):
        raise ValueError(f"Invalid role: {request.role}")

    # Validate classification scope
    for scope in request.classification_scope:
        if scope not in ALL_CLASSIFICATIONS:
            raise ValueError(f"Invalid classification: {scope}")

    key_id = _generate_key_id()
    raw_key = _generate_key(request.role)
    key_hash = _hash_key(raw_key)
    now = _now()

    expires_at = None
    if request.expires_in_days is not None:
        expires_at = now + timedelta(days=request.expires_in_days)

    scope_json = json.dumps(request.classification_scope)

    async with _auth_connection() as db:
        await db.execute(
            """INSERT INTO api_keys
               (key_id, key_hash, label, role, classification_scope,
                rate_limit_rpm, created_at, expires_at, is_active, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                key_id, key_hash, request.label, request.role, scope_json,
                request.rate_limit_rpm, now.isoformat(),
                expires_at.isoformat() if expires_at else None,
                created_by,
            ),
        )
        await db.commit()

    logger.info(
        "api_key_created key_id=%s label=%s role=%s created_by=%s",
        key_id, request.label, request.role, created_by,
    )

    return APIKeyResponse(
        key_id=key_id,
        key=raw_key,
        label=request.label,
        role=request.role,
        classification_scope=request.classification_scope,
        rate_limit_rpm=request.rate_limit_rpm,
        created_at=now,
        expires_at=expires_at,
    )


async def validate_api_key(raw_key: str) -> APIKey | None:
    """Validate a raw API key. Returns the APIKey if valid, None otherwise.

    Also updates last_used timestamp.
    """
    from periphery.auth.persistence import _auth_connection

    async with _auth_connection() as db:
        cursor = await db.execute(
            """SELECT key_id, key_hash, label, role, classification_scope,
                      rate_limit_rpm, created_at, expires_at, last_used,
                      is_active, created_by
               FROM api_keys WHERE is_active = 1""",
        )
        rows = await cursor.fetchall()

        for row in rows:
            if _verify_key(raw_key, row["key_hash"]):
                # Check expiration
                if row["expires_at"]:
                    expires = datetime.fromisoformat(row["expires_at"])
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires < _now():
                        logger.warning("api_key_expired key_id=%s", row["key_id"])
                        return None

                # Update last_used
                await db.execute(
                    "UPDATE api_keys SET last_used = ? WHERE key_id = ?",
                    (_now().isoformat(), row["key_id"]),
                )
                await db.commit()

                scope = json.loads(row["classification_scope"]) if row["classification_scope"] else ["PUBLIC"]

                return APIKey(
                    key_id=row["key_id"],
                    key_hash=row["key_hash"],
                    label=row["label"],
                    role=row["role"],
                    classification_scope=scope,
                    rate_limit_rpm=row["rate_limit_rpm"],
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    last_used=_now(),
                    is_active=True,
                    created_by=row["created_by"],
                )

    return None


async def revoke_api_key(key_id: str) -> bool:
    """Deactivate an API key. Returns True if found and revoked."""
    from periphery.auth.persistence import _auth_connection

    async with _auth_connection() as db:
        cursor = await db.execute(
            "UPDATE api_keys SET is_active = 0 WHERE key_id = ?",
            (key_id,),
        )
        await db.commit()
        revoked = cursor.rowcount > 0

    if revoked:
        logger.info("api_key_revoked key_id=%s", key_id)
    return revoked


async def list_api_keys() -> list[dict]:
    """List all API keys (without hashes or raw key values)."""
    from periphery.auth.persistence import _auth_connection

    async with _auth_connection() as db:
        cursor = await db.execute(
            """SELECT key_id, label, role, classification_scope,
                      rate_limit_rpm, created_at, expires_at, last_used,
                      is_active, created_by
               FROM api_keys ORDER BY created_at DESC""",
        )
        rows = await cursor.fetchall()

    return [
        {
            "key_id": r["key_id"],
            "label": r["label"],
            "role": r["role"],
            "classification_scope": json.loads(r["classification_scope"]) if r["classification_scope"] else ["PUBLIC"],
            "rate_limit_rpm": r["rate_limit_rpm"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "last_used": r["last_used"],
            "is_active": bool(r["is_active"]),
            "created_by": r["created_by"],
        }
        for r in rows
    ]


async def get_api_key_by_id(key_id: str) -> dict | None:
    """Get API key details by key_id (without hash)."""
    from periphery.auth.persistence import _auth_connection

    async with _auth_connection() as db:
        cursor = await db.execute(
            """SELECT key_id, label, role, classification_scope,
                      rate_limit_rpm, created_at, expires_at, last_used,
                      is_active, created_by
               FROM api_keys WHERE key_id = ?""",
            (key_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None

    return {
        "key_id": row["key_id"],
        "label": row["label"],
        "role": row["role"],
        "classification_scope": json.loads(row["classification_scope"]) if row["classification_scope"] else ["PUBLIC"],
        "rate_limit_rpm": row["rate_limit_rpm"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "last_used": row["last_used"],
        "is_active": bool(row["is_active"]),
        "created_by": row["created_by"],
    }
