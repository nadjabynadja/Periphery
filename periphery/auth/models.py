"""Pydantic models for auth, users, and organizations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------

class Organization(BaseModel):
    org_id: str
    name: str
    created_at: datetime | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class User(BaseModel):
    user_id: str
    org_id: str
    display_name: str
    role: str = "analyst"  # admin | analyst | viewer
    created_at: datetime | None = None
    last_active: datetime | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class AuthSession(BaseModel):
    session_token: str
    user_id: str
    org_id: str
    created_at: datetime | None = None
    expires_at: datetime | None = None
    last_seen: datetime | None = None
    user_agent: str | None = None


class AuthChallenge(BaseModel):
    challenge_id: str
    challenge_code: str
    qr_payload: str
    status: str = "pending"  # pending | scanned | completed | expired
    user_id: str | None = None
    org_id: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    completed_at: datetime | None = None
    session_token: str | None = None


# ---------------------------------------------------------------------------
# Authenticated user context (injected by middleware)
# ---------------------------------------------------------------------------

class AuthenticatedUser(BaseModel):
    user_id: str
    org_id: str
    display_name: str
    role: str


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class CreateOrgRequest(BaseModel):
    name: str


class CreateUserRequest(BaseModel):
    display_name: str
    role: str = "analyst"


class ChallengeResponse(BaseModel):
    challenge_id: str
    qr_data: str
    expires_at: datetime


class ChallengeStatusResponse(BaseModel):
    status: str
    user_display_name: str | None = None


class ScanRequest(BaseModel):
    user_id: str


class ConfirmRequest(BaseModel):
    code: str


class SessionResponse(BaseModel):
    session_token: str
    user_id: str
    org_id: str
    display_name: str
    role: str
    expires_at: datetime


class MeResponse(BaseModel):
    user_id: str
    org_id: str
    org_name: str
    display_name: str
    role: str


# ---------------------------------------------------------------------------
# Personal ontology models
# ---------------------------------------------------------------------------

class EntityAnnotation(BaseModel):
    canonical_id: str
    annotation_type: str  # pin | hide | tag | note
    annotation_data: dict[str, Any] = Field(default_factory=dict)


class EntityGroup(BaseModel):
    group_id: str
    name: str
    description: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class CreateGroupRequest(BaseModel):
    name: str
    description: str | None = None
    entity_ids: list[str] = Field(default_factory=list)


class UpdateGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    entity_ids: list[str] | None = None


class SavedView(BaseModel):
    view_id: str
    name: str
    filters: dict[str, Any] = Field(default_factory=dict)
    layout: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class CreateViewRequest(BaseModel):
    name: str
    filters: dict[str, Any] = Field(default_factory=dict)
    layout: dict[str, Any] = Field(default_factory=dict)


class PersonalOverlay(BaseModel):
    pinned_entity_ids: list[str] = Field(default_factory=list)
    hidden_entity_ids: list[str] = Field(default_factory=list)
    custom_groups: list[EntityGroup] = Field(default_factory=list)
    entity_annotations: dict[str, list[EntityAnnotation]] = Field(default_factory=dict)
    saved_views: list[SavedView] = Field(default_factory=list)
