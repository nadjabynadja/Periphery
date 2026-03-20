"""CRUD operations for organizations, users, sessions, and challenges."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from periphery.db import get_pool
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
    pool = get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO organizations (org_id, name, created_at, settings) VALUES (?, ?, ?, ?)",
            (org.org_id, org.name, org.created_at.isoformat(), json.dumps(org.settings)),
        )
        await db.commit()
    logger.info("organization_created org_id=%s name=%s", org.org_id, org.name)
    return org


async def get_organization(org_id: str) -> Organization | None:
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
        await db.execute("DELETE FROM auth_sessions WHERE session_token = ?", (token,))
        await db.commit()


# ---------------------------------------------------------------------------
# Auth Challenges (QR flow)
# ---------------------------------------------------------------------------

async def create_challenge(server_url: str, ttl_minutes: int = 5) -> AuthChallenge:
    now = _now()
    challenge_id = generate_challenge_id()
    challenge_code = generate_challenge_code()
    qr_payload = json.dumps({
        "challenge_id": challenge_id,
        "server_url": server_url,
        "ts": int(now.timestamp()),
    })
    challenge = AuthChallenge(
        challenge_id=challenge_id,
        challenge_code=challenge_code,
        qr_payload=qr_payload,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
        cursor = await db.execute("DELETE FROM approved_emails WHERE email = ?", (email,))
        await db.commit()
    return cursor.rowcount > 0


async def list_approved_emails(org_id: str | None = None) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
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
    pool = get_pool()
    async with pool.acquire() as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE org_id = ? AND display_name = ? LIMIT 1",
            (entry["org_id"], entry["display_name"]),
        )
        row = await cursor.fetchone()

    if row:
        return await get_user(row["user_id"])

    # Auto-provision the user
    return await create_user(entry["org_id"], entry["display_name"], entry["role"])
