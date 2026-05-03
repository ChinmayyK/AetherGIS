"""AetherGIS — Pytest tests: session lock service (critical security component)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


class TestSessionLockService:
    """Tests for the SessionLockService that manages exclusive GPU access."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis = MagicMock()
        redis.ping.return_value = True
        return redis

    @pytest.fixture
    def lock_service(self, mock_redis, monkeypatch):
        """Create a SessionLockService with mocked Redis."""
        # Mock settings
        monkeypatch.setattr(
            "backend.app.services.session_lock.settings",
            MagicMock(aether_mode="production", redis_url="redis://localhost:6379/0")
        )
        
        # Import after mocking
        from backend.app.services.session_lock import SessionLockService
        
        service = SessionLockService()
        service.r = mock_redis
        service.available = True
        return service

    def test_get_status_granted_when_no_active_user(self, lock_service, mock_redis):
        """Should grant lock when no user currently holds it."""
        mock_redis.get.return_value = None  # No active user
        mock_redis.set.return_value = True  # SET NX succeeds
        
        result = lock_service.get_status("user_123")
        
        assert result["status"] == "granted"
        assert result["is_active"] is True
        assert result["queue_pos"] == 0
        mock_redis.set.assert_called_once()

    def test_get_status_waiting_when_another_user_has_lock(self, lock_service, mock_redis):
        """Should return waiting status when another user holds the lock."""
        mock_redis.get.return_value = "other_user"
        mock_redis.lpos.return_value = None  # Not in waitlist yet
        mock_redis.lrange.return_value = []
        
        result = lock_service.get_status("user_123")
        
        assert result["status"] == "waiting"
        assert result["is_active"] is False
        assert result["queue_pos"] == 1
        mock_redis.rpush.assert_called_once()

    def test_get_status_granted_when_user_has_lock(self, lock_service, mock_redis):
        """Should return granted when requesting user already has the lock."""
        mock_redis.get.return_value = "user_123"
        
        result = lock_service.get_status("user_123")
        
        assert result["status"] == "granted"
        assert result["is_active"] is True
        mock_redis.expire.assert_called()  # Heartbeat refreshed

    def test_heartbeat_refreshes_lock_ttl(self, lock_service, mock_redis):
        """Heartbeat should extend the lock TTL."""
        mock_redis.get.return_value = "user_123"
        
        lock_service.heartbeat("user_123")
        
        mock_redis.expire.assert_called_once()
        mock_redis.setex.assert_called_once()

    def test_heartbeat_does_nothing_when_not_lock_holder(self, lock_service, mock_redis):
        """Heartbeat should not refresh if user doesn't hold the lock."""
        mock_redis.get.return_value = "other_user"
        
        lock_service.heartbeat("user_123")
        
        mock_redis.expire.assert_not_called()

    def test_release_removes_lock(self, lock_service, mock_redis):
        """Release should delete the lock key."""
        mock_redis.get.return_value = "user_123"
        
        result = lock_service.release("user_123")
        
        assert result is True
        mock_redis.delete.assert_called()

    def test_release_removes_from_waitlist_if_not_holder(self, lock_service, mock_redis):
        """Release should remove user from waitlist if they don't hold the lock."""
        mock_redis.get.return_value = "other_user"
        mock_redis.lrem.return_value = 1
        
        result = lock_service.release("user_123")
        
        assert result is True
        mock_redis.lrem.assert_called_once()

    def test_queue_position_calculation(self, lock_service, mock_redis):
        """Queue position should be 1-indexed."""
        mock_redis.get.return_value = "active_user"
        mock_redis.lpos.return_value = 2  # 0-indexed position
        
        result = lock_service.get_status("user_123")
        
        assert result["queue_pos"] == 3  # Should be 1-indexed (2 + 1)

    def test_development_mode_bypasses_lock(self, mock_redis, monkeypatch):
        """In development mode, lock should always be granted."""
        monkeypatch.setattr(
            "backend.app.services.session_lock.settings",
            MagicMock(aether_mode="development")
        )
        
        from backend.app.services.session_lock import SessionLockService
        service = SessionLockService()
        service.r = mock_redis
        service.available = True
        
        result = service.get_status("any_user")
        
        assert result["status"] == "granted"
        assert result["is_active"] is True

    def test_redis_unavailable_bypasses_lock(self, monkeypatch):
        """When Redis is unavailable, lock should be granted to prevent deadlock."""
        monkeypatch.setattr(
            "backend.app.services.session_lock.settings",
            MagicMock(aether_mode="production", redis_url="redis://invalid:6379/0")
        )
        
        from backend.app.services.session_lock import SessionLockService
        service = SessionLockService()
        # Service should detect Redis unavailable on init
        
        result = service.get_status("any_user")
        
        # Should grant access when Redis unavailable (fail-open for availability)
        assert result["status"] == "granted"

    def test_process_next_in_queue(self, lock_service, mock_redis):
        """When lock is released, next user in queue should get it."""
        mock_redis.get.return_value = "user_1"
        mock_redis.lpop.return_value = "user_2"
        mock_redis.set.return_value = True
        
        lock_service.release("user_1")
        
        mock_redis.lpop.assert_called_once()
        # Should claim lock for next user

    def test_force_release_admin_override(self, lock_service, mock_redis):
        """Force release should clear lock regardless of holder."""
        lock_service.force_release()
        
        mock_redis.delete.assert_called()

    def test_duplicate_add_to_waitlist_prevented(self, lock_service, mock_redis):
        """Should not add user to waitlist if already present."""
        mock_redis.get.return_value = "active_user"
        mock_redis.lrange.return_value = ["user_123"]  # Already in list
        mock_redis.lpos.return_value = 0
        
        lock_service.get_status("user_123")
        
        mock_redis.rpush.assert_not_called()  # Should not add duplicate


class TestSessionLockIntegration:
    """Integration tests for session lock with real Redis (if available)."""

    @pytest.fixture(scope="class")
    def real_redis_available(self):
        """Check if real Redis is available."""
        try:
            import redis as redis_sync
            r = redis_sync.from_url("redis://localhost:6379/0", socket_connect_timeout=1)
            r.ping()
            return True
        except Exception:
            return False

    @pytest.mark.skipif(not False, reason="Requires real Redis instance")
    def test_real_redis_lock_acquisition(self):
        """Test with real Redis if available."""
        # This test is skipped by default, enable when Redis is available
        pass
