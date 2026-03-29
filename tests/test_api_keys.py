"""Tests for API key authentication system."""

import pytest
import pytest_asyncio

from periphery.auth.api_keys import (
    create_api_key,
    ensure_api_keys_table,
    get_api_key_by_id,
    list_api_keys,
    revoke_api_key,
    validate_api_key,
)
from periphery.auth.classification import ALL_CLASSIFICATIONS
from periphery.auth.models import AuthContext, CreateAPIKeyRequest
from periphery.auth.rate_limiter import FailedAuthTracker, RateLimiter
from periphery.db import DatabasePool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_pool(tmp_path):
    """Create a temporary database pool with schema."""
    db_path = tmp_path / "test_api_keys.db"
    pool = DatabasePool(str(db_path), pool_size=2)
    await pool.initialize()

    # Also set up the auth DB path for the persistence module
    import periphery.auth.persistence as auth_persist
    import periphery.db as db_mod
    db_mod._pool = pool

    auth_db_path = str(tmp_path / "test_auth.db")
    auth_persist.set_auth_db_path(auth_db_path)
    auth_persist._auth_initialized = False  # Force re-init
    await auth_persist._ensure_auth_db()

    yield pool
    await pool.close()
    auth_persist._auth_initialized = False


# ---------------------------------------------------------------------------
# API Key CRUD
# ---------------------------------------------------------------------------

class TestAPIKeyCreation:
    @pytest.mark.asyncio
    async def test_create_api_key(self, db_pool):
        request = CreateAPIKeyRequest(
            label="Test Key",
            role="analyst",
            classification_scope=["PUBLIC", "PII"],
        )
        result = await create_api_key(request, created_by="test")

        assert result.key_id.startswith("key_")
        assert result.key.startswith("pk_analyst_")
        assert result.label == "Test Key"
        assert result.role == "analyst"
        assert result.classification_scope == ["PUBLIC", "PII"]
        assert result.created_at is not None
        assert result.expires_at is None

    @pytest.mark.asyncio
    async def test_create_api_key_with_expiration(self, db_pool):
        request = CreateAPIKeyRequest(
            label="Expiring Key",
            role="ingest",
            expires_in_days=30,
        )
        result = await create_api_key(request)

        assert result.expires_at is not None
        assert result.key.startswith("pk_ingest_")

    @pytest.mark.asyncio
    async def test_create_admin_key(self, db_pool):
        request = CreateAPIKeyRequest(
            label="Admin Key",
            role="admin",
            classification_scope=ALL_CLASSIFICATIONS,
        )
        result = await create_api_key(request)

        assert result.key.startswith("pk_admin_")
        assert result.role == "admin"

    @pytest.mark.asyncio
    async def test_invalid_role_rejected(self, db_pool):
        request = CreateAPIKeyRequest(
            label="Bad Key",
            role="superadmin",
        )
        with pytest.raises(ValueError, match="Invalid role"):
            await create_api_key(request)

    @pytest.mark.asyncio
    async def test_invalid_classification_rejected(self, db_pool):
        request = CreateAPIKeyRequest(
            label="Bad Scope",
            role="analyst",
            classification_scope=["PUBLIC", "TOP_SECRET"],
        )
        with pytest.raises(ValueError, match="Invalid classification"):
            await create_api_key(request)


class TestAPIKeyValidation:
    @pytest.mark.asyncio
    async def test_validate_valid_key(self, db_pool):
        request = CreateAPIKeyRequest(
            label="Validate Me",
            role="analyst",
            classification_scope=["PUBLIC"],
        )
        created = await create_api_key(request)

        validated = await validate_api_key(created.key)
        assert validated is not None
        assert validated.key_id == created.key_id
        assert validated.role == "analyst"
        assert validated.label == "Validate Me"

    @pytest.mark.asyncio
    async def test_validate_invalid_key(self, db_pool):
        result = await validate_api_key("pk_analyst_completely_invalid_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_revoked_key(self, db_pool):
        request = CreateAPIKeyRequest(label="Revoke Me", role="analyst")
        created = await create_api_key(request)

        await revoke_api_key(created.key_id)

        result = await validate_api_key(created.key)
        assert result is None


class TestAPIKeyManagement:
    @pytest.mark.asyncio
    async def test_list_keys(self, db_pool):
        for i in range(3):
            await create_api_key(CreateAPIKeyRequest(
                label=f"Key {i}",
                role="analyst",
            ))

        keys = await list_api_keys()
        assert len(keys) >= 3
        # Keys should not contain hashes
        for key in keys:
            assert "key_hash" not in key

    @pytest.mark.asyncio
    async def test_get_key_by_id(self, db_pool):
        created = await create_api_key(CreateAPIKeyRequest(
            label="Find Me",
            role="ingest",
        ))

        found = await get_api_key_by_id(created.key_id)
        assert found is not None
        assert found["label"] == "Find Me"
        assert found["role"] == "ingest"

    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, db_pool):
        found = await get_api_key_by_id("key_doesnotexist")
        assert found is None

    @pytest.mark.asyncio
    async def test_revoke_key(self, db_pool):
        created = await create_api_key(CreateAPIKeyRequest(
            label="Revocable",
            role="analyst",
        ))

        revoked = await revoke_api_key(created.key_id)
        assert revoked is True

        found = await get_api_key_by_id(created.key_id)
        assert found is not None
        assert found["is_active"] is False

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key(self, db_pool):
        revoked = await revoke_api_key("key_doesnotexist")
        assert revoked is False


# ---------------------------------------------------------------------------
# AuthContext model
# ---------------------------------------------------------------------------

class TestAuthContext:
    def test_session_context(self):
        ctx = AuthContext(
            auth_type="session",
            user_id="user123",
            role="admin",
            classification_scope=ALL_CLASSIFICATIONS,
            label="Alice",
        )
        assert ctx.auth_type == "session"
        assert ctx.role == "admin"
        assert "PUBLIC" in ctx.classification_scope

    def test_api_key_context(self):
        ctx = AuthContext(
            auth_type="api_key",
            key_id="key_abc123",
            role="analyst",
            classification_scope=["PUBLIC", "PII"],
            label="Analyst Key",
        )
        assert ctx.auth_type == "api_key"
        assert ctx.key_id == "key_abc123"

    def test_default_scope(self):
        ctx = AuthContext(
            auth_type="admin_key",
            role="admin",
            label="Legacy",
        )
        assert ctx.classification_scope == ["PUBLIC"]


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_under_limit(self):
        limiter = RateLimiter()
        for _ in range(10):
            assert limiter.check("test_key", 100) is True

    def test_blocks_over_limit(self):
        limiter = RateLimiter()
        for _ in range(5):
            limiter.check("test_key", 5)
        assert limiter.check("test_key", 5) is False

    def test_separate_keys(self):
        limiter = RateLimiter()
        for _ in range(5):
            limiter.check("key_a", 5)
        # key_a is blocked
        assert limiter.check("key_a", 5) is False
        # key_b should still work
        assert limiter.check("key_b", 5) is True

    def test_remaining(self):
        limiter = RateLimiter()
        assert limiter.remaining("test_key", 10) == 10
        limiter.check("test_key", 10)
        assert limiter.remaining("test_key", 10) == 9


# ---------------------------------------------------------------------------
# Failed Auth Tracker
# ---------------------------------------------------------------------------

class TestFailedAuthTracker:
    def test_not_blocked_initially(self):
        tracker = FailedAuthTracker(max_failures=3)
        assert tracker.is_blocked("192.168.1.1") is False

    def test_blocked_after_max_failures(self):
        tracker = FailedAuthTracker(max_failures=3, window_seconds=60)
        for _ in range(3):
            tracker.record_failure("192.168.1.1")
        assert tracker.is_blocked("192.168.1.1") is True

    def test_different_ips_independent(self):
        tracker = FailedAuthTracker(max_failures=3)
        for _ in range(3):
            tracker.record_failure("192.168.1.1")
        assert tracker.is_blocked("192.168.1.1") is True
        assert tracker.is_blocked("192.168.1.2") is False

    def test_clear_resets(self):
        tracker = FailedAuthTracker(max_failures=3)
        for _ in range(3):
            tracker.record_failure("192.168.1.1")
        assert tracker.is_blocked("192.168.1.1") is True

        tracker.clear("192.168.1.1")
        assert tracker.is_blocked("192.168.1.1") is False
