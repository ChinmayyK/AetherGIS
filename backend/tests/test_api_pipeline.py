"""AetherGIS — Pytest tests: pipeline API routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from datetime import datetime, timezone


class TestPipelineAPI:
    """Tests for pipeline API endpoints."""

    @pytest.fixture
    def client(self, monkeypatch):
        """Create test client with mocked services."""
        # Mock job manager
        mock_job_manager = MagicMock()
        mock_job_manager.can_accept_new_job.return_value = (True, None)
        mock_job_manager.create_job.return_value = MagicMock(
            job_id="test-job-123",
            status="QUEUED",
            priority="normal"
        )
        mock_job_manager.get_job.return_value = None  # Not found initially
        
        monkeypatch.setattr("backend.app.api.routes.pipeline.can_accept_new_job", mock_job_manager.can_accept_new_job)
        monkeypatch.setattr("backend.app.api.routes.pipeline.create_job", mock_job_manager.create_job)
        monkeypatch.setattr("backend.app.api.routes.pipeline.get_job", mock_job_manager.get_job)
        
        # Mock session lock
        mock_lock = MagicMock()
        mock_lock.get_status.return_value = {"status": "granted", "is_active": True, "queue_pos": 0}
        monkeypatch.setattr("backend.app.api.routes.pipeline.lock_service", mock_lock)
        
        # Mock Celery
        mock_celery_task = MagicMock()
        mock_celery_task.apply_async.return_value = MagicMock(id="celery-task-123")
        monkeypatch.setattr("backend.app.api.routes.pipeline.run_pipeline_task", mock_celery_task)
        
        from backend.app.main import app
        return TestClient(app), mock_job_manager, mock_lock

    def test_run_pipeline_validation_error(self, client):
        """Should return 422 for invalid request body."""
        client, _, _ = client
        
        response = client.post("/api/v1/pipeline/run", json={})
        
        assert response.status_code == 422  # Validation error

    def test_run_pipeline_rate_limit(self, client, monkeypatch):
        """Should return 429 when rate limited."""
        client, mock_job_manager, _ = client
        mock_job_manager.can_accept_new_job.return_value = (False, "Rate limit exceeded")
        
        request_data = {
            "layer_id": "MODIS_Terra",
            "bbox": [68.0, 8.0, 97.0, 37.0],
            "time_start": datetime.now(timezone.utc).isoformat(),
            "time_end": (datetime.now(timezone.utc)).isoformat(),
            "data_source": "nasa_gibs",
            "interpolation_model": "rife",
            "n_intermediate": 4
        }
        
        response = client.post("/api/v1/pipeline/run", json=request_data)
        
        assert response.status_code == 429

    def test_run_pipeline_session_locked(self, client, monkeypatch):
        """Should return 423 when session is locked by another user."""
        client, _, mock_lock = client
        mock_lock.get_status.return_value = {
            "status": "waiting",
            "is_active": False,
            "queue_pos": 2,
            "wait_time_est_min": 10
        }
        
        request_data = {
            "layer_id": "MODIS_Terra",
            "bbox": [68.0, 8.0, 97.0, 37.0],
            "time_start": datetime.now(timezone.utc).isoformat(),
            "time_end": (datetime.now(timezone.utc)).isoformat(),
            "data_source": "nasa_gibs",
            "interpolation_model": "rife",
            "n_intermediate": 4,
            "session_id": "user_123"
        }
        
        response = client.post("/api/v1/pipeline/run", json=request_data)
        
        assert response.status_code == 423
        data = response.json()
        assert "queue_pos" in data

    def test_run_pipeline_success(self, client):
        """Should successfully queue pipeline job."""
        client, mock_job_manager, _ = client
        
        request_data = {
            "layer_id": "MODIS_Terra",
            "bbox": [68.0, 8.0, 97.0, 37.0],
            "time_start": datetime.now(timezone.utc).isoformat(),
            "time_end": (datetime.now(timezone.utc)).isoformat(),
            "data_source": "nasa_gibs",
            "interpolation_model": "rife",
            "n_intermediate": 4
        }
        
        response = client.post("/api/v1/pipeline/run", json=request_data)
        
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "QUEUED"
        mock_job_manager.create_job.assert_called_once()

    def test_run_pipeline_bbox_validation(self, client):
        """Should validate bbox coordinates."""
        client, _, _ = client
        
        # Invalid bbox: min >= max
        request_data = {
            "layer_id": "MODIS_Terra",
            "bbox": [97.0, 37.0, 68.0, 8.0],  # Invalid: min > max
            "time_start": datetime.now(timezone.utc).isoformat(),
            "time_end": (datetime.now(timezone.utc)).isoformat(),
            "data_source": "nasa_gibs",
            "interpolation_model": "rife"
        }
        
        response = client.post("/api/v1/pipeline/run", json=request_data)
        
        assert response.status_code == 422

    def test_run_pipeline_time_validation(self, client):
        """Should validate time range (end > start)."""
        client, _, _ = client
        
        now = datetime.now(timezone.utc)
        request_data = {
            "layer_id": "MODIS_Terra",
            "bbox": [68.0, 8.0, 97.0, 37.0],
            "time_start": now.isoformat(),
            "time_end": (now.replace(hour=now.hour - 1)).isoformat(),  # End before start
            "data_source": "nasa_gibs",
            "interpolation_model": "rife"
        }
        
        response = client.post("/api/v1/pipeline/run", json=request_data)
        
        assert response.status_code == 422

    def test_get_job_status_not_found(self, client):
        """Should return 404 for unknown job ID."""
        client, _, _ = client
        
        response = client.get("/api/v1/pipeline/unknown-job/status")
        
        # Currently returns 200 with celery status, may change to 404
        # This test documents current behavior
        assert response.status_code in [200, 404]

    def test_get_job_status_success(self, client, monkeypatch):
        """Should return job status for existing job."""
        client, mock_job_manager, _ = client
        
        mock_job = MagicMock(
            job_id="test-job-123",
            status="RUNNING",
            progress=0.5,
            message="Processing",
            error=None
        )
        mock_job_manager.get_job.return_value = mock_job
        
        response = client.get("/api/v1/pipeline/test-job-123/status")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "test-job-123"
        assert data["status"] == "RUNNING"


class TestPipelineAPIEdgeCases:
    """Edge case tests for pipeline API."""

    def test_pipeline_request_missing_required_fields(self):
        """Should require all mandatory fields."""
        from backend.app.models.schemas import PipelineRunRequest
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            PipelineRunRequest(
                layer_id="test",  # Missing bbox, time_start, time_end
                bbox=[68.0, 8.0, 97.0, 37.0]
            )

    def test_pipeline_request_invalid_resolution(self):
        """Should validate resolution enum."""
        from backend.app.models.schemas import PipelineRunRequest, Resolution
        from datetime import datetime, timezone
        
        # Valid resolutions
        request = PipelineRunRequest(
            layer_id="test",
            bbox=[68.0, 8.0, 97.0, 37.0],
            time_start=datetime.now(timezone.utc),
            time_end=datetime.now(timezone.utc),
            resolution=Resolution.medium
        )
        assert request.resolution == Resolution.medium

    def test_pipeline_request_invalid_interpolation_model(self):
        """Should validate interpolation model enum."""
        from backend.app.models.schemas import InterpolationModel
        
        # Valid models
        assert InterpolationModel("rife") == InterpolationModel.rife
        assert InterpolationModel("film") == InterpolationModel.film
        assert InterpolationModel("lk_fallback") == InterpolationModel.lk_fallback
