"""CRUD operations for organizations, users, sessions, and challenges.

Auth uses its own isolated SQLite database (auth.db) to avoid write-lock
contention with the main document/enrichment database, which can block
for seconds during heavy pipeline processing.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from periphery.auth.models import (
    AuthChallenge,
    AuthSession,
    Organization,
    User,
)
from periphery.auth.utils import (
    generate_challenge_code,
    generate_challenge_id,
    generate_org_id,
    generate_session_token,
    generate_user_id,
)

logger = logging.getLogger(__name__)

# ── Isolated auth database ──────────────────────────────────────────────
# Auth tables live in their own SQLite file so write-lock contention from
# the enrichment pipeline never blocks login/session operations.

_AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    org_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    settings JSON DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'analyst',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP,
    settings JSON DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_user_org ON users(org_id);
CREATE TABLE IF NOT EXISTS auth_sessions (
    session_token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_seen TIMESTAMP,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_session_expires ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_challenges (
    challenge_id TEXT PRIMARY KEY,
    challenge_code TEXT NOT NULL,
    qr_payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    user_id TEXT,
    org_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    session_token TEXT
);
CREATE INDEX IF NOT EXISTS idx_challenge_status ON auth_challenges(status, expires_at);
CREATE TABLE IF NOT EXISTS approved_emails (
    email TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    org_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'analyst',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_approved_email_org ON approved_emails(org_id);
"""

_auth_db_path: str | None = None
_auth_initialized = False


def set_auth_db_path(path: str) -> None:
    """Set the auth database path (called during app startup)."""
    global _auth_db_path
    _auth_db_path = path


def _get_auth_db_path() -> str:
    """Resolve auth DB path, defaulting next to the main DB."""
    if _auth_db_path:
        return _auth_db_path
    # Default: /app/data/periphery_auth.db (or ./data/periphery_auth.db)
    from periphery.config import get_settings
    main_db = get_settings().pipeline_db_path
    return str(Path(main_db).parent / "periphery_auth.db")


async def _ensure_auth_db() -> None:
    """Create auth DB file and schema if needed, migrate data from main DB."""
    global _auth_initialized
    if _auth_initialized:
        return
    db_path = _get_auth_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    is_new = not Path(db_path).exists() or Path(db_path).stat().st_size == 0
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.executescript(_AUTH_SCHEMA)
        await db.commit()

    # One-time migration: copy auth data from main DB if auth DB is fresh
    if is_new:
        await _migrate_from_main_db(db_path)

    _auth_initialized = True
    logger.info("Auth database initialized: %s", db_path)


async def _migrate_from_main_db(auth_db_path: str) -> None:
    """Copy organizations, users, sessions, challenges, approved_emails from main DB."""
    try:
        from periphery.config import get_settings
        main_db_path = get_settings().pipeline_db_path
        if not Path(main_db_path).exists():
            return

        tables = ["organizations", "users", "auth_sessions", "auth_challenges", "approved_emails"]
        migrated = {}

        main_db = await aiosqlite.connect(main_db_path)
        main_db.row_factory = aiosqlite.Row
        auth_db = await aiosqlite.connect(auth_db_path)
        try:
            for table in tables:
                try:
                    cursor = await main_db.execute(f"SELECT * FROM {table}")
                    rows = await cursor.fetchall()
                    if not rows:
                        continue
                    cols = [d[0] for d in cursor.description]
                    placeholders = ",".join("?" for _ in cols)
                    col_names = ",".join(cols)
                    for row in rows:
                        try:
                            await auth_db.execute(
                                f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})",
                                tuple(row),
                            )
                        except Exception:
                            pass
                    migrated[table] = len(rows)
                except Exception:
                    pass
            await auth_db.commit()
        finally:
            await main_db.close()
            await auth_db.close()

        if migrated:
            logger.info("Migrated auth data from main DB: %s", migrated)
    except Exception:
        logger.warning("Auth migration from main DB failed (non-fatal)", exc_info=True)


@asynccontextmanager
async def _auth_connection():
    """Acquire a short-lived connection to the auth database."""
    await _ensure_auth_db()
    db = await aiosqlite.connect(_get_auth_db_path())
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA synchronous=NORMAL")
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

async def create_organization(name: str, settings: dict | None = None) -> Organization:
    org = Organization(
        org_id=generate_org_id(),
        name=name,
        created_at=_now(),
        settings=settings or {},
    )
    async with _auth_connection() as db:
        await db.execute(
            "INSERT INTO organizations (org_id, name, created_at, settings) VALUES (?, ?, ?, ?)",
            (org.org_id, org.name, org.created_at.isoformat(), json.dumps(org.settings)),
        )
        await db.commit()
    logger.info("organization_created org_id=%s name=%s", org.org_id, org.name)
    return org


async def get_organization(org_id: str) -> Organization | None:
    async with _auth_connection() as db:
        cursor = await db.execute(
            "SELECT org_id, name, created_at, settings FROM organizations WHERE org_id = ?",
            (org_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return Organization(
        org_id=row["org_id"],
        name=row["name"],
        created_at=row["created_at"],
        settings=json.loads(row["settings"]) if row["settings"] else {},
    )


async def list_organizations() -> list[Organization]:
    async with _auth_connection() as db:
        cursor = await db.execute(
            "SELECT org_id, name, created_at, settings FROM organizations ORDER BY created_at"
        )
        rows = await cursor.fetchall()
    return [
        Organization(
            org_id=r["org_id"],
            name=r["name"],
            created_at=r["created_at"],
            settings=json.loads(r["settings"]) if r["settings"] else {},
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def create_user(org_id: str, display_name: str, role: str = "analyst") -> User:
    user = User(
        user_id=generate_user_id(),
        org_id=org_id,
        display_name=display_name,
        role=role,
        created_at=_now(),
    )
    async with _auth_connection() as db:
        await db.execute(
            """INSERT INTO users (user_id, org_id, display_name, role, created_at, settings)
               VALUES (?, ?, ?, ?, ?, '{}')""",
            (user.user_id, user.org_id, user.display_name, user.role,
             user.created_at.isoformat()),
        )
        await db.commit()
    logger.info("user_created user_id=%s org_id=%s", user.user_id, user.org_id)
    return user


async def get_user(user_id: str) -> User | None:
    async with _auth_connection() as db:
        cursor = await db.execute(
            "SELECT user_id, org_id, display_name, role, created_at, last_active, settings FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return User(
        user_id=row["user_id"],
        org_id=row["org_id"],
        display_name=row["display_name"],
        role=row["role"],
        created_at=row["created_at"],
        last_active=row["last_active"],
        settings=json.loads(row["settings"]) if row["settings"] else {},
    )


async def list_users(org_id: str) -> list[User]:
    async with _auth_connection() as db:
        cursor = await db.execute(
            "SELECT user_id, org_id, display_name, role, created_at, last_active, settings FROM users WHERE org_id = ? ORDER BY created_at",
            (org_id,),
        )
        rows = await cursor.fetchall()
    return [
        User(
            user_id=r["user_id"],
            org_id=r["org_id"],
            display_name=r["display_name"],
            role=r["role"],
            created_at=r["created_at"],
            last_active=r["last_active"],
            settings=json.loads(r["settings"]) if r["settings"] else {},
        )
        for r in rows
    ]


async def update_user_last_active(user_id: str) -> None:
    async with _auth_connection() as db:
        await db.execute(
            "UPDATE users SET last_active = ? WHERE user_id = ?",
            (_now().isoformat(), user_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Auth Sessions
# ---------------------------------------------------------------------------

async def create_session(
    user_id: str,
    org_id: str,
    ttl_hours: int = 720,
    user_agent: str | None = None,
) -> AuthSession:
    now = _now()
    session = AuthSession(
        session_token=generate_session_token(),
        user_id=user_id,
        org_id=org_id,
        created_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
        last_seen=now,
        user_agent=user_agent,
    )
    async with _auth_connection() as db:
        await db.execute(
            """INSERT INTO auth_sessions
               (session_token, user_id, org_id, created_at, expires_at, last_seen, user_agent)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session.session_token, session.user_id, session.org_id,
                session.created_at.isoformat(), session.expires_at.isoformat(),
                session.last_seen.isoformat(), session.user_agent,
            ),
        )
        await db.commit()
    return session


async def validate_session(token: str) -> AuthSession | None:
    async with _auth_connection() as db:
        cursor = await db.execute(
            """SELECT session_token, user_id, org_id, created_at, expires_at, last_seen, user_agent
               FROM auth_sessions
               WHERE session_token = ? AND expires_at > datetime('now')""",
            (token,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        # Update last_seen
        await db.execute(
            "UPDATE auth_sessions SET last_seen = ? WHERE session_token = ?",
            (_now().isoformat(), token),
        )
        await db.commit()
    return AuthSession(
        session_token=row["session_token"],
        user_id=row["user_id"],
        org_id=row["org_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        last_seen=row["last_seen"],
        user_agent=row["user_agent"],
    )


async def delete_session(token: str) -> None:
    async with _auth_connection() as db:
        await db.execute("DELETE FROM auth_sessions WHERE session_token = ?", (token,))
        await db.commit()


# ---------------------------------------------------------------------------
# Auth Challenges (QR flow)
# ---------------------------------------------------------------------------

async def create_challenge(server_url: str, ttl_minutes: int = 5) -> AuthChallenge:
    now = _now()
    challenge_id = generate_challenge_id()
    challenge_code = generate_challenge_code()
    # QR encodes a URL that opens the mobile auth page directly
    qr_payload = f"{server_url}/app/?challenge={challenge_id}"
    challenge = AuthChallenge(
        challenge_id=challenge_id,
        challenge_code=challenge_code,
        qr_payload=qr_payload,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    async with _auth_connection() as db:
        await db.execute(
            """INSERT INTO auth_challenges
               (challenge_id, challenge_code, qr_payload, status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                challenge.challenge_id, challenge.challenge_code,
                challenge.qr_payload, challenge.status,
                challenge.created_at.isoformat(), challenge.expires_at.isoformat(),
            ),
        )
        await db.commit()
    return challenge


async def get_challenge(challenge_id: str) -> AuthChallenge | None:
    async with _auth_connection() as db:
        cursor = await db.execute(
            """SELECT challenge_id, challenge_code, qr_payload, status,
                      user_id, org_id, created_at, expires_at, completed_at, session_token
               FROM auth_challenges WHERE challenge_id = ?""",
            (challenge_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return AuthChallenge(
        challenge_id=row["challenge_id"],
        challenge_code=row["challenge_code"],
        qr_payload=row["qr_payload"],
        status=row["status"],
        user_id=row["user_id"],
        org_id=row["org_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        completed_at=row["completed_at"],
        session_token=row["session_token"],
    )


async def scan_challenge(challenge_id: str, user_id: str, org_id: str) -> AuthChallenge | None:
    async with _auth_connection() as db:
        cursor = await db.execute(
            """UPDATE auth_challenges
               SET status = 'scanned', user_id = ?, org_id = ?
               WHERE challenge_id = ? AND status = 'pending' AND expires_at > datetime('now')
               RETURNING challenge_id, challenge_code, qr_payload, status,
                         user_id, org_id, created_at, expires_at, completed_at, session_token""",
            (user_id, org_id, challenge_id),
        )
        row = await cursor.fetchone()
        if row:
            await db.commit()
        else:
            return None
    return AuthChallenge(
        challenge_id=row["challenge_id"],
        challenge_code=row["challenge_code"],
        qr_payload=row["qr_payload"],
        status=row["status"],
        user_id=row["user_id"],
        org_id=row["org_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        completed_at=row["completed_at"],
        session_token=row["session_token"],
    )


async def complete_challenge(
    challenge_id: str,
    code: str,
    session_token: str,
) -> AuthChallenge | None:
    async with _auth_connection() as db:
        cursor = await db.execute(
            """UPDATE auth_challenges
               SET status = 'completed', completed_at = ?, session_token = ?
               WHERE challenge_id = ? AND challenge_code = ? AND status = 'scanned'
                 AND expires_at > datetime('now')
               RETURNING challenge_id, challenge_code, qr_payload, status,
                         user_id, org_id, created_at, expires_at, completed_at, session_token""",
            (_now().isoformat(), session_token, challenge_id, code),
        )
        row = await cursor.fetchone()
        if row:
            await db.commit()
        else:
            return None
    return AuthChallenge(
        challenge_id=row["challenge_id"],
        challenge_code=row["challenge_code"],
        qr_payload=row["qr_payload"],
        status=row["status"],
        user_id=row["user_id"],
        org_id=row["org_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        completed_at=row["completed_at"],
        session_token=row["session_token"],
    )


# ---------------------------------------------------------------------------
# Approved Emails (simple allowlist for QR login)
# ---------------------------------------------------------------------------

async def add_approved_email(
    email: str,
    display_name: str,
    org_id: str,
    role: str = "analyst",
    added_by: str | None = None,
) -> dict:
    """Add or update an approved email in the allowlist."""
    email = email.lower().strip()
    async with _auth_connection() as db:
        await db.execute(
            """INSERT INTO approved_emails (email, display_name, org_id, role, added_at, added_by)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE
               SET display_name = excluded.display_name,
                   org_id = excluded.org_id,
                   role = excluded.role,
                   added_by = excluded.added_by""",
            (email, display_name, org_id, role, _now().isoformat(), added_by),
        )
        await db.commit()
    logger.info("approved_email_added email=%s org_id=%s role=%s", email, org_id, role)
    return {"email": email, "display_name": display_name, "org_id": org_id, "role": role}


async def remove_approved_email(email: str) -> bool:
    email = email.lower().strip()
    async with _auth_connection() as db:
        cursor = await db.execute("DELETE FROM approved_emails WHERE email = ?", (email,))
        await db.commit()
    return cursor.rowcount > 0


async def list_approved_emails(org_id: str | None = None) -> list[dict]:
    async with _auth_connection() as db:
        if org_id:
            cursor = await db.execute(
                "SELECT email, display_name, org_id, role, added_at, added_by FROM approved_emails WHERE org_id = ? ORDER BY added_at",
                (org_id,),
            )
        else:
            cursor = await db.execute(
                "SELECT email, display_name, org_id, role, added_at, added_by FROM approved_emails ORDER BY added_at"
            )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_approved_email(email: str) -> dict | None:
    email = email.lower().strip()
    async with _auth_connection() as db:
        cursor = await db.execute(
            "SELECT email, display_name, org_id, role, added_at, added_by FROM approved_emails WHERE email = ?",
            (email,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_or_create_user_for_email(email: str) -> User | None:
    """Look up approved email, find/create corresponding user, return it."""
    entry = await get_approved_email(email)
    if not entry:
        return None

    # Find existing user by display_name + org_id (simple matching)
    async with _auth_connection() as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE org_id = ? AND display_name = ? LIMIT 1",
            (entry["org_id"], entry["display_name"]),
        )
        row = await cursor.fetchone()

    if row:
        return await get_user(row["user_id"])

    # Auto-provision the user
    return await create_user(entry["org_id"], entry["display_name"], entry["role"])
