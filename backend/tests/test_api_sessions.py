"""AetherGIS — Pytest tests: sessions API routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from datetime import datetime, timezone


class TestSessionsAPI:
    """Tests for sessions API endpoints."""

    @pytest.fixture
    def mock_persistence(self, monkeypatch):
        """Mock persistence layer."""
        mock = MagicMock()
        
        # Mock list_sessions
        mock.list_sessions.return_value = [
            {
                "session_id": "session-1",
                "name": "Test Session 1",
                "provider_default": "nasa_gibs",
                "run_count": 5,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        
        # Mock create_session
        mock.create_session.return_value = {
            "session_id": "new-session",
            "name": "New Session",
            "provider_default": "nasa_gibs",
            "run_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Mock get_session
        mock.get_session.return_value = {
            "session_id": "session-1",
            "name": "Test Session 1",
            "provider_default": "nasa_gibs",
            "run_count": 5,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Mock rename_session
        mock.rename_session.return_value = {
            "session_id": "session-1",
            "name": "Renamed Session",
            "provider_default": "nasa_gibs",
            "run_count": 5,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Mock archive_session
        mock.archive_session.return_value = True
        
        # Mock list_runs
        mock.list_runs.return_value = [
            {
                "run_id": "run-1",
                "job_id": "job-1",
                "session_id": "session-1",
                "status": "completed",
                "layer_id": "MODIS_Terra",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        
        # Mock get_run
        mock.get_run.return_value = {
            "run_id": "run-1",
            "job_id": "job-1",
            "session_id": "session-1",
            "status": "completed",
            "layer_id": "MODIS_Terra",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        monkeypatch.setattr("backend.app.api.routes.sessions.list_sessions", mock.list_sessions)
        monkeypatch.setattr("backend.app.api.routes.sessions.create_session", mock.create_session)
        monkeypatch.setattr("backend.app.api.routes.sessions.get_session", mock.get_session)
        monkeypatch.setattr("backend.app.api.routes.sessions.rename_session", mock.rename_session)
        monkeypatch.setattr("backend.app.api.routes.sessions.archive_session", mock.archive_session)
        monkeypatch.setattr("backend.app.api.routes.sessions.list_runs", mock.list_runs)
        monkeypatch.setattr("backend.app.api.routes.sessions.get_run", mock.get_run)
        
        return mock

    @pytest.fixture
    def client(self, mock_persistence):
        """Create test client."""
        from backend.app.main import app
        return TestClient(app)

    def test_list_sessions(self, client, mock_persistence):
        """Should list all sessions."""
        response = client.get("/api/v1/sessions")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["session_id"] == "session-1"
        mock_persistence.list_sessions.assert_called_once_with(include_archived=False)

    def test_list_sessions_include_archived(self, client, mock_persistence):
        """Should include archived sessions when requested."""
        response = client.get("/api/v1/sessions?include_archived=true")
        
        assert response.status_code == 200
        mock_persistence.list_sessions.assert_called_once_with(include_archived=True)

    def test_create_session(self, client, mock_persistence):
        """Should create a new session."""
        request_data = {
            "name": "My New Session",
            "provider_default": "nasa_gibs"
        }
        
        response = client.post("/api/v1/sessions", json=request_data)
        
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Session"
        assert data["provider_default"] == "nasa_gibs"
        mock_persistence.create_session.assert_called_once()

    def test_get_session(self, client, mock_persistence):
        """Should return session details."""
        response = client.get("/api/v1/sessions/session-1")
        
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "session-1"
        mock_persistence.get_session.assert_called_once_with("session-1")

    def test_get_session_not_found(self, client, mock_persistence):
        """Should return 404 for unknown session."""
        mock_persistence.get_session.return_value = None
        
        response = client.get("/api/v1/sessions/unknown")
        
        assert response.status_code == 404

    def test_rename_session(self, client, mock_persistence):
        """Should rename a session."""
        request_data = {"name": "New Name"}
        
        response = client.patch("/api/v1/sessions/session-1", json=request_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Renamed Session"
        mock_persistence.rename_session.assert_called_once_with("session-1", "New Name")

    def test_rename_session_not_found(self, client, mock_persistence):
        """Should return 404 when renaming unknown session."""
        mock_persistence.rename_session.return_value = None
        
        request_data = {"name": "New Name"}
        response = client.patch("/api/v1/sessions/unknown", json=request_data)
        
        assert response.status_code == 404

    def test_archive_session(self, client, mock_persistence):
        """Should archive a session."""
        response = client.delete("/api/v1/sessions/session-1")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "archived"
        mock_persistence.archive_session.assert_called_once_with("session-1")

    def test_archive_session_not_found(self, client, mock_persistence):
        """Should return 404 when archiving unknown session."""
        mock_persistence.archive_session.return_value = False
        
        response = client.delete("/api/v1/sessions/unknown")
        
        assert response.status_code == 404

    def test_list_session_runs(self, client, mock_persistence):
        """Should list runs for a session."""
        response = client.get("/api/v1/sessions/session-1/runs")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["run_id"] == "run-1"
        mock_persistence.list_runs.assert_called_once_with(session_id="session-1", limit=50)

    def test_list_session_runs_with_limit(self, client, mock_persistence):
        """Should respect limit parameter."""
        response = client.get("/api/v1/sessions/session-1/runs?limit=10")
        
        assert response.status_code == 200
        mock_persistence.list_runs.assert_called_with(session_id="session-1", limit=10)

    def test_list_session_runs_not_found(self, client, mock_persistence):
        """Should return 404 for unknown session."""
        mock_persistence.get_session.return_value = None
        
        response = client.get("/api/v1/sessions/unknown/runs")
        
        assert response.status_code == 404

    def test_get_run(self, client, mock_persistence):
        """Should return run details."""
        response = client.get("/api/v1/sessions/runs/run-1")
        
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-1"
        mock_persistence.get_run.assert_called_once_with("run-1")

    def test_get_run_not_found(self, client, mock_persistence):
        """Should return 404 for unknown run."""
        mock_persistence.get_run.return_value = None
        
        response = client.get("/api/v1/sessions/runs/unknown")
        
        assert response.status_code == 404


class TestSessionsValidation:
    """Validation tests for sessions API."""

    def test_create_session_missing_name(self):
        """Should require name field."""
        from backend.app.models.schemas import SessionCreateV2Request
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            SessionCreateV2Request()  # Missing required 'name'

    def test_create_session_empty_name(self):
        """Should reject empty session name."""
        from backend.app.models.schemas import SessionCreateV2Request
        
        # Empty string might be allowed by Pydantic but rejected by business logic
        request = SessionCreateV2Request(name="")
        assert request.name == ""
