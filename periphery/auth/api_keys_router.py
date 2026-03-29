"""FastAPI router for API key management endpoints.

All endpoints require admin role (via legacy X-Admin-Key or authenticated admin session).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Header

from periphery.auth.api_keys import (
    create_api_key,
    get_api_key_by_id,
    list_api_keys,
    revoke_api_key,
)
from periphery.auth.models import CreateAPIKeyRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/keys", tags=["api-keys"])


def _check_admin_key(x_admin_key: str | None) -> None:
    """Raise HTTP 403 if admin key is missing or incorrect."""
    from periphery.config import get_settings
    settings = get_settings()
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=403,
            detail="Admin endpoints are disabled (admin_api_key not configured)",
        )
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header")


@router.post("")
async def create_key(
    body: CreateAPIKeyRequest,
    x_admin_key: str | None = Header(None),
):
    """Create a new API key. Admin only. Returns the key value ONCE."""
    _check_admin_key(x_admin_key)
    try:
        result = await create_api_key(body, created_by="admin")
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_keys(
    x_admin_key: str | None = Header(None),
):
    """List all API keys (without raw key values). Admin only."""
    _check_admin_key(x_admin_key)
    return await list_api_keys()


@router.get("/{key_id}")
async def get_key(
    key_id: str,
    x_admin_key: str | None = Header(None),
):
    """Get details for a specific API key. Admin only."""
    _check_admin_key(x_admin_key)
    key = await get_api_key_by_id(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    return key


@router.delete("/{key_id}")
async def delete_key(
    key_id: str,
    x_admin_key: str | None = Header(None),
):
    """Revoke an API key. Admin only."""
    _check_admin_key(x_admin_key)
    revoked = await revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"ok": True, "key_id": key_id, "revoked": True}
