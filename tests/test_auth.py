"""Tests for the auth system — ID generation, persistence, challenge flow, middleware."""

import json
import tempfile

import pytest
import pytest_asyncio

from periphery.auth.utils import (
    generate_challenge_code,
    generate_challenge_id,
    generate_org_id,
    generate_session_token,
    generate_user_id,
)
from periphery.db import DatabasePool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_pool(tmp_path):
    """Create a temporary database pool with schema."""
    db_path = tmp_path / "test_auth.db"
    pool = DatabasePool(str(db_path), pool_size=2)
    await pool.initialize()
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# ID Generation
# ---------------------------------------------------------------------------

class TestIDGeneration:
    def test_org_id_length(self):
        org_id = generate_org_id()
        assert len(org_id) == 16

    def test_org_id_base62(self):
        org_id = generate_org_id()
        assert org_id.isalnum()

    def test_user_id_length(self):
        user_id = generate_user_id()
        assert len(user_id) == 24

    def test_user_id_base62(self):
        user_id = generate_user_id()
        assert user_id.isalnum()

    def test_session_token_length(self):
        token = generate_session_token()
        assert len(token) == 64

    def test_challenge_code_format(self):
        code = generate_challenge_code()
        assert len(code) == 6
        assert code.isdigit()

    def test_uniqueness(self):
        ids = {generate_org_id() for _ in range(100)}
        assert len(ids) == 100

    def test_user_id_uniqueness(self):
        ids = {generate_user_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Persistence (requires database)
# ---------------------------------------------------------------------------

class TestPersistence:
    @pytest.mark.asyncio
    async def test_create_organization(self, db_pool):
        from periphery.auth.persistence import create_organization, get_organization
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Test Corp")
        assert len(org.org_id) == 16
        assert org.name == "Test Corp"

        fetched = await get_organization(org.org_id)
        assert fetched is not None
        assert fetched.name == "Test Corp"

    @pytest.mark.asyncio
    async def test_create_user(self, db_pool):
        from periphery.auth.persistence import create_organization, create_user, get_user
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Test Org")
        user = await create_user(org.org_id, "Alice", "admin")

        assert len(user.user_id) == 24
        assert user.org_id == org.org_id
        assert user.display_name == "Alice"
        assert user.role == "admin"

        fetched = await get_user(user.user_id)
        assert fetched is not None
        assert fetched.display_name == "Alice"

    @pytest.mark.asyncio
    async def test_list_users(self, db_pool):
        from periphery.auth.persistence import (
            create_organization, create_user, list_users,
        )
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Multi User Org")
        await create_user(org.org_id, "Alice", "admin")
        await create_user(org.org_id, "Bob", "analyst")

        users = await list_users(org.org_id)
        assert len(users) == 2
        names = {u.display_name for u in users}
        assert names == {"Alice", "Bob"}

    @pytest.mark.asyncio
    async def test_create_and_validate_session(self, db_pool):
        from periphery.auth.persistence import (
            create_organization, create_user, create_session, validate_session,
        )
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Session Org")
        user = await create_user(org.org_id, "Charlie")
        session = await create_session(user.user_id, org.org_id, ttl_hours=1)

        assert len(session.session_token) == 64

        validated = await validate_session(session.session_token)
        assert validated is not None
        assert validated.user_id == user.user_id
        assert validated.org_id == org.org_id

    @pytest.mark.asyncio
    async def test_invalid_session(self, db_pool):
        from periphery.auth.persistence import validate_session
        import periphery.db as db_mod
        db_mod._pool = db_pool

        result = await validate_session("nonexistent_token")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_session(self, db_pool):
        from periphery.auth.persistence import (
            create_organization, create_user, create_session,
            delete_session, validate_session,
        )
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Delete Session Org")
        user = await create_user(org.org_id, "Dave")
        session = await create_session(user.user_id, org.org_id)

        await delete_session(session.session_token)
        result = await validate_session(session.session_token)
        assert result is None


# ---------------------------------------------------------------------------
# Challenge Flow
# ---------------------------------------------------------------------------

class TestChallengeFlow:
    @pytest.mark.asyncio
    async def test_full_challenge_flow(self, db_pool):
        from periphery.auth.persistence import (
            create_organization, create_user, create_session,
            create_challenge, get_challenge, scan_challenge, complete_challenge,
        )
        import periphery.db as db_mod
        db_mod._pool = db_pool

        # Setup org + user
        org = await create_organization("Challenge Org")
        user = await create_user(org.org_id, "Eve", "admin")

        # Step 1: Desktop creates challenge
        challenge = await create_challenge("http://localhost:8000", ttl_minutes=5)
        assert challenge.status == "pending"
        assert len(challenge.challenge_code) == 6

        # Verify QR payload
        payload = json.loads(challenge.qr_payload)
        assert payload["challenge_id"] == challenge.challenge_id
        assert payload["server_url"] == "http://localhost:8000"

        # Step 2: Phone scans
        scanned = await scan_challenge(challenge.challenge_id, user.user_id, org.org_id)
        assert scanned is not None
        assert scanned.status == "scanned"
        assert scanned.user_id == user.user_id

        # Step 3: Desktop confirms with correct code
        session = await create_session(user.user_id, org.org_id)
        completed = await complete_challenge(
            challenge.challenge_id,
            challenge.challenge_code,
            session.session_token,
        )
        assert completed is not None
        assert completed.status == "completed"
        assert completed.session_token == session.session_token

    @pytest.mark.asyncio
    async def test_wrong_passcode(self, db_pool):
        from periphery.auth.persistence import (
            create_organization, create_user, create_session,
            create_challenge, scan_challenge, complete_challenge,
        )
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Wrong Code Org")
        user = await create_user(org.org_id, "Frank")

        challenge = await create_challenge("http://localhost:8000")
        await scan_challenge(challenge.challenge_id, user.user_id, org.org_id)

        session = await create_session(user.user_id, org.org_id)
        result = await complete_challenge(
            challenge.challenge_id,
            "000000",  # Wrong code
            session.session_token,
        )
        assert result is None  # Should fail

    @pytest.mark.asyncio
    async def test_scan_already_scanned(self, db_pool):
        from periphery.auth.persistence import (
            create_organization, create_user,
            create_challenge, scan_challenge,
        )
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Double Scan Org")
        user = await create_user(org.org_id, "Grace")

        challenge = await create_challenge("http://localhost:8000")
        result1 = await scan_challenge(challenge.challenge_id, user.user_id, org.org_id)
        assert result1 is not None

        # Second scan should fail (status is no longer 'pending')
        result2 = await scan_challenge(challenge.challenge_id, user.user_id, org.org_id)
        assert result2 is None


# ---------------------------------------------------------------------------
# Personal Ontology
# ---------------------------------------------------------------------------

class TestPersonalOntology:
    @pytest.mark.asyncio
    async def test_pin_and_hide(self, db_pool):
        from periphery.auth.persistence import create_organization, create_user
        from periphery.auth.personal import (
            set_annotation, remove_annotation, get_personal_overlay,
        )
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Personal Org")
        user = await create_user(org.org_id, "Heidi")

        await set_annotation(user.user_id, "ent-001", "pin")
        await set_annotation(user.user_id, "ent-002", "hide")
        await set_annotation(user.user_id, "ent-003", "pin")

        overlay = await get_personal_overlay(user.user_id)
        assert set(overlay.pinned_entity_ids) == {"ent-001", "ent-003"}
        assert overlay.hidden_entity_ids == ["ent-002"]

        await remove_annotation(user.user_id, "ent-001", "pin")
        overlay = await get_personal_overlay(user.user_id)
        assert overlay.pinned_entity_ids == ["ent-003"]

    @pytest.mark.asyncio
    async def test_entity_groups(self, db_pool):
        from periphery.auth.persistence import create_organization, create_user
        from periphery.auth.personal import (
            create_group, update_group, delete_group, list_groups,
        )
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Groups Org")
        user = await create_user(org.org_id, "Ivan")

        group = await create_group(
            user.user_id, "Watchlist", "Priority targets", ["ent-1", "ent-2"],
        )
        assert group.name == "Watchlist"
        assert group.entity_ids == ["ent-1", "ent-2"]

        updated = await update_group(
            user.user_id, group.group_id, entity_ids=["ent-1", "ent-2", "ent-3"],
        )
        assert updated is not None
        assert len(updated.entity_ids) == 3

        groups = await list_groups(user.user_id)
        assert len(groups) == 1

        deleted = await delete_group(user.user_id, group.group_id)
        assert deleted is True

        groups = await list_groups(user.user_id)
        assert len(groups) == 0

    @pytest.mark.asyncio
    async def test_saved_views(self, db_pool):
        from periphery.auth.persistence import create_organization, create_user
        from periphery.auth.personal import create_view, delete_view, list_views
        import periphery.db as db_mod
        db_mod._pool = db_pool

        org = await create_organization("Views Org")
        user = await create_user(org.org_id, "Judy")

        view = await create_view(
            user.user_id,
            "High Confidence",
            filters={"confidence_floor": 0.8},
            layout={"zoom": 1.5},
        )
        assert view.name == "High Confidence"
        assert view.filters["confidence_floor"] == 0.8

        views = await list_views(user.user_id)
        assert len(views) == 1

        deleted = await delete_view(user.user_id, view.view_id)
        assert deleted is True

        views = await list_views(user.user_id)
        assert len(views) == 0
