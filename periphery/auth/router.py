"""FastAPI router for authentication and user management endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Header, Request

from periphery.auth.middleware import get_current_user
from periphery.auth.models import (
    AuthenticatedUser,
    ChallengeResponse,
    ChallengeStatusResponse,
    ConfirmRequest,
    CreateOrgRequest,
    CreateUserRequest,
    MeResponse,
    ScanRequest,
    SessionResponse,
)
from pydantic import BaseModel

class EmailScanRequest(BaseModel):
    email: str

class ApprovedEmailRequest(BaseModel):
    email: str
    display_name: str
    org_id: str
    role: str = "analyst"
from periphery.auth.persistence import (
    add_approved_email,
    complete_challenge,
    create_challenge,
    create_organization,
    create_session,
    create_user,
    delete_session,
    get_approved_email,
    get_challenge,
    get_or_create_user_for_email,
    get_organization,
    get_user,
    list_approved_emails,
    list_organizations,
    list_users,
    remove_approved_email,
    scan_challenge,
)
from periphery.config import get_settings


def _check_admin_key(x_admin_key: str | None) -> None:
    """Raise HTTP 403 if admin key is missing or incorrect."""
    settings = get_settings()
    if not settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled (admin_api_key not configured)")
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# QR Challenge Flow
# ---------------------------------------------------------------------------

@router.post("/challenge", response_model=ChallengeResponse)
async def start_challenge(request: Request):
    """Create a new QR auth challenge. Desktop calls this to start login."""
    settings = get_settings()
    server_url = str(request.base_url).rstrip("/")
    challenge = await create_challenge(
        server_url=server_url,
        ttl_minutes=settings.auth_challenge_ttl_minutes,
    )
    return ChallengeResponse(
        challenge_id=challenge.challenge_id,
        qr_data=challenge.qr_payload,
        expires_at=challenge.expires_at,
    )


@router.get("/challenge/{challenge_id}/status", response_model=ChallengeStatusResponse)
async def poll_challenge_status(challenge_id: str):
    """Poll challenge status. Desktop calls this every 2s while showing QR."""
    challenge = await get_challenge(challenge_id)
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    user_display_name = None
    if challenge.user_id:
        user = await get_user(challenge.user_id)
        if user:
            user_display_name = user.display_name

    return ChallengeStatusResponse(
        status=challenge.status,
        user_display_name=user_display_name,
    )


@router.post("/challenge/{challenge_id}/scan")
async def scan_qr_challenge(challenge_id: str, body: ScanRequest):
    """Phone scans QR and submits user identity. Returns the 6-digit code to display."""
    user = await get_user(body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    challenge = await scan_challenge(challenge_id, user.user_id, user.org_id)
    if not challenge:
        raise HTTPException(status_code=400, detail="Challenge not available or expired")

    return {"challenge_code": challenge.challenge_code}


@router.post("/challenge/{challenge_id}/scan-by-email")
async def scan_qr_challenge_by_email(challenge_id: str, body: EmailScanRequest):
    """Phone scans QR and authenticates by email address.

    The email must be in the approved_emails allowlist.
    If the user doesn't exist yet, they are auto-provisioned.
    Returns the 6-digit code to display on the phone.
    """
    user = await get_or_create_user_for_email(body.email)
    if not user:
        raise HTTPException(status_code=403, detail="Email not approved for access")

    challenge = await scan_challenge(challenge_id, user.user_id, user.org_id)
    if not challenge:
        raise HTTPException(status_code=400, detail="Challenge not available or expired")

    return {
        "challenge_code": challenge.challenge_code,
        "display_name": user.display_name,
    }


@router.post("/challenge/{challenge_id}/confirm", response_model=SessionResponse)
async def confirm_challenge(
    challenge_id: str,
    body: ConfirmRequest,
    user_agent: str | None = Header(None),
):
    """Desktop submits the 6-digit passcode. Returns session token on success."""
    challenge = await get_challenge(challenge_id)
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if challenge.status != "scanned":
        raise HTTPException(status_code=400, detail="Challenge not in scanned state")
    if not challenge.user_id or not challenge.org_id:
        raise HTTPException(status_code=400, detail="Challenge has no user")

    settings = get_settings()
    session = await create_session(
        user_id=challenge.user_id,
        org_id=challenge.org_id,
        ttl_hours=settings.auth_session_ttl_hours,
        user_agent=user_agent,
    )

    completed = await complete_challenge(
        challenge_id=challenge_id,
        code=body.code,
        session_token=session.session_token,
    )
    if not completed:
        # Wrong code or expired — clean up the session we just made
        await delete_session(session.session_token)
        raise HTTPException(status_code=401, detail="Invalid passcode or challenge expired")

    user = await get_user(challenge.user_id)
    return SessionResponse(
        session_token=session.session_token,
        user_id=user.user_id,
        org_id=user.org_id,
        display_name=user.display_name,
        role=user.role,
        expires_at=session.expires_at,
    )


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout(authorization: str | None = Header(None)):
    """Invalidate the current session."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"ok": True}
    token = authorization[7:]
    await delete_session(token)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def get_me(user: AuthenticatedUser = Depends(get_current_user)):
    """Get the current authenticated user's info."""
    org = await get_organization(user.org_id)
    return MeResponse(
        user_id=user.user_id,
        org_id=user.org_id,
        org_name=org.name if org else "Unknown",
        display_name=user.display_name,
        role=user.role,
    )


# ---------------------------------------------------------------------------
# Organization & user management
# ---------------------------------------------------------------------------

@router.post("/orgs")
async def create_org(body: CreateOrgRequest, x_admin_key: str | None = Header(None)):
    """Create a new organization. Bootstrap endpoint. Requires X-Admin-Key header."""
    _check_admin_key(x_admin_key)
    org = await create_organization(body.name)
    return {"org_id": org.org_id, "name": org.name}


@router.get("/orgs")
async def list_orgs():
    """List all organizations."""
    orgs = await list_organizations()
    return [{"org_id": o.org_id, "name": o.name, "created_at": o.created_at} for o in orgs]


@router.post("/orgs/{org_id}/users")
async def create_org_user(
    org_id: str,
    body: CreateUserRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a user within an organization. Requires admin role."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create users")
    if user.org_id != org_id:
        raise HTTPException(status_code=403, detail="Cannot create users in other organizations")

    org = await get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    new_user = await create_user(org_id, body.display_name, body.role)
    return {
        "user_id": new_user.user_id,
        "org_id": new_user.org_id,
        "display_name": new_user.display_name,
        "role": new_user.role,
    }


@router.get("/orgs/{org_id}/users")
async def list_org_users(
    org_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List users in an organization."""
    if user.org_id != org_id:
        raise HTTPException(status_code=403, detail="Cannot view users in other organizations")
    users = await list_users(org_id)
    return [
        {
            "user_id": u.user_id,
            "display_name": u.display_name,
            "role": u.role,
            "created_at": u.created_at,
            "last_active": u.last_active,
        }
        for u in users
    ]


# ---------------------------------------------------------------------------
# Approved Emails — admin allowlist management
# ---------------------------------------------------------------------------

@router.get("/approved-emails")
async def list_approved(
    org_id: str | None = None,
    x_admin_key: str | None = Header(None),
):
    """List approved emails. Requires X-Admin-Key."""
    _check_admin_key(x_admin_key)
    return await list_approved_emails(org_id)


@router.post("/approved-emails")
async def add_approved(
    body: ApprovedEmailRequest,
    x_admin_key: str | None = Header(None),
):
    """Add or update an approved email. Requires X-Admin-Key."""
    _check_admin_key(x_admin_key)
    org = await get_organization(body.org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    result = await add_approved_email(
        email=body.email,
        display_name=body.display_name,
        org_id=body.org_id,
        role=body.role,
    )
    return result


@router.delete("/approved-emails/{email}")
async def remove_approved(
    email: str,
    x_admin_key: str | None = Header(None),
):
    """Remove an approved email. Requires X-Admin-Key."""
    _check_admin_key(x_admin_key)
    removed = await remove_approved_email(email)
    if not removed:
        raise HTTPException(status_code=404, detail="Email not found")
    return {"ok": True}
