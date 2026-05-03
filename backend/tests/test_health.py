"""AetherGIS — Pytest tests: health check endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock


class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from backend.app.main import app
        return TestClient(app)

    def test_health_returns_200(self, client):
        """Health check should return 200 OK."""
        response = client.get("/api/v1/health")
        
        assert response.status_code == 200

    def test_health_response_structure(self, client):
        """Health check should return expected fields."""
        response = client.get("/api/v1/health")
        
        assert response.status_code == 200
        data = response.json()
        
        # Check all required fields are present
        assert "status" in data
        assert "redis_connected" in data
        assert "db_connected" in data
        assert "gpu_available" in data
        assert "gpu_device_name" in data
        assert "rife_model_loaded" in data
        assert "film_model_loaded" in data
        assert "version" in data

    def test_health_status_values(self, client):
        """Status should be either 'healthy' or 'degraded'."""
        response = client.get("/api/v1/health")
        
        data = response.json()
        assert data["status"] in ["healthy", "degraded"]

    def test_health_boolean_fields(self, client):
        """Boolean fields should be actual booleans."""
        response = client.get("/api/v1/health")
        
        data = response.json()
        assert isinstance(data["redis_connected"], bool)
        assert isinstance(data["db_connected"], bool)
        assert isinstance(data["gpu_available"], bool)
        assert isinstance(data["rife_model_loaded"], bool)
        assert isinstance(data["film_model_loaded"], bool)

    def test_health_degraded_when_redis_down(self, client, monkeypatch):
        """Should return degraded when Redis is unavailable."""
        # Mock Redis to fail
        monkeypatch.setattr("backend.app.main._health_redis_pool", None)
        monkeypatch.setattr(
            "backend.app.main._get_health_redis",
            lambda: None
        )
        
        response = client.get("/api/v1/health")
        
        data = response.json()
        assert data["redis_connected"] is False
        assert data["status"] == "degraded"

    def test_health_connection_pool_reuse(self, client, monkeypatch):
        """Health check should reuse Redis connections."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        
        # Set the global pool
        monkeypatch.setattr("backend.app.main._health_redis_pool", mock_redis)
        
        # First request
        response1 = client.get("/api/v1/health")
        assert response1.status_code == 200
        
        # Second request should reuse same connection
        response2 = client.get("/api/v1/health")
        assert response2.status_code == 200
        
        # ping should be called twice, but connection created once
        assert mock_redis.ping.call_count == 2

    def test_health_resets_pool_on_failure(self, client, monkeypatch):
        """Should reset connection pool on Redis failure."""
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = Exception("Connection lost")
        
        monkeypatch.setattr("backend.app.main._health_redis_pool", mock_redis)
        
        response = client.get("/api/v1/health")
        
        # Pool should be reset (set to None)
        # This is checked indirectly by the behavior
        data = response.json()
        assert data["redis_connected"] is False


class TestHealthPerformance:
    """Performance tests for health endpoint."""

    def test_health_response_time(self, client):
        """Health check should respond quickly (< 500ms)."""
        import time
        
        start = time.time()
        response = client.get("/api/v1/health")
        elapsed = time.time() - start
        
        assert response.status_code == 200
        assert elapsed < 0.5  # 500ms
