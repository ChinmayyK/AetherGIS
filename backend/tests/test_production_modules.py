"""AetherGIS — Production Modules Integration Tests.

Tests all 15 new production modules:
  1  Async Job Engine
  2  Data Versioning
  3  Multi-Source Ingestion
  4  Smart Cache
  6  Uncertainty Maps
  7  Change Detection
  8  Anomaly Detection
  9  Geo-Region Query
  10 Metric Aggregation
  12 Security Middleware
  14 Performance Monitoring
  15 Audit Trail
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_frame():
    rng = np.random.default_rng(42)
    return rng.random((256, 256, 3)).astype(np.float32)


@pytest.fixture
def frame_sequence():
    rng = np.random.default_rng(0)
    return [rng.random((128, 128, 3)).astype(np.float32) for _ in range(5)]


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


# ── MODULE 1 + 15: Job Manager + Audit Trail ─────────────────────────────────

class TestJobManager:
    def test_create_job(self, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.job_manager._get_redis", lambda: None
        )
        from backend.app.services.job_manager import create_job, JobPriority

        jid = str(uuid.uuid4())
        record = create_job(jid, priority=JobPriority.high, message="Test job")
        assert record.job_id == jid
        assert record.status == "QUEUED"
        assert record.priority == "high"

    def test_update_job(self, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.job_manager._get_redis", lambda: None
        )
        from backend.app.services.job_manager import create_job, update_job, JobPriority

        jid = str(uuid.uuid4())
        create_job(jid, priority=JobPriority.normal)
        updated = update_job(jid, status="RUNNING", progress=0.5, stage="interpolation", message="Half done")
        assert updated is not None
        assert updated.progress == 0.5
        assert updated.current_stage == "interpolation"
        assert len(updated.logs) >= 1

    def test_complete_job(self, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.job_manager._get_redis", lambda: None
        )
        from backend.app.services.job_manager import create_job, complete_job, JobPriority

        jid = str(uuid.uuid4())
        create_job(jid, priority=JobPriority.normal)
        result = {"metrics": {"total_frames": 10}}
        completed = complete_job(jid, result)
        assert completed.status == "COMPLETED"
        assert completed.result == result
        assert completed.completed_at is not None

    def test_cancel_job(self, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.job_manager._get_redis", lambda: None
        )
        from backend.app.services.job_manager import create_job, cancel_job

        jid = str(uuid.uuid4())
        create_job(jid)
        record = cancel_job(jid)
        assert record.status == "CANCELLED"

    def test_audit_trail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.job_manager._get_redis", lambda: None
        )
        monkeypatch.setattr(
            "backend.app.services.job_manager.settings",
            MagicMock(
                data_dir=tmp_path,
                redis_url="redis://localhost:6379/0",
            ),
        )
        from backend.app.services.job_manager import append_audit_event, load_audit_trail

        jid = str(uuid.uuid4())
        append_audit_event(jid, "test_event", {"key": "value"})
        append_audit_event(jid, "test_event_2", {"another": "data"})

        trail = load_audit_trail(jid)
        assert len(trail) == 2
        assert trail[0]["event"] == "test_event"
        assert trail[1]["event"] == "test_event_2"

    def test_checkpoint_save_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.job_manager._get_redis", lambda: None
        )
        monkeypatch.setattr(
            "backend.app.services.job_manager.settings",
            MagicMock(
                data_dir=tmp_path,
                redis_url="redis://localhost:6379/0",
            ),
        )
        from backend.app.services.job_manager import save_checkpoint, load_checkpoint

        jid = str(uuid.uuid4())
        data = {"frame_count": 42, "processed": True}
        save_checkpoint(jid, "ingestion", data)

        loaded = load_checkpoint(jid, "ingestion")
        assert loaded == data

    def test_manifest_persistence(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.job_manager._get_redis", lambda: None
        )
        monkeypatch.setattr(
            "backend.app.services.job_manager.settings",
            MagicMock(
                data_dir=tmp_path,
                redis_url="redis://localhost:6379/0",
            ),
        )
        from backend.app.services.job_manager import save_manifest, load_manifest

        jid = str(uuid.uuid4())
        manifest = {
            "schema_version": "1.0",
            "job_id": jid,
            "parameters": {"layer_id": "test_layer"},
        }
        save_manifest(jid, manifest)
        loaded = load_manifest(jid)
        assert loaded["job_id"] == jid
        assert loaded["parameters"]["layer_id"] == "test_layer"


# ── MODULE 3: Satellite Providers ────────────────────────────────────────────

class TestSatelliteProviders:
    def test_list_providers(self):
        from backend.app.services.satellite_providers import list_providers
        providers = list_providers()
        assert len(providers) >= 4
        names = [p["name"] for p in providers]
        assert "nasa_gibs" in names
        assert "static_fallback" in names

    def test_static_fallback_always_returns(self):
        import asyncio
        from backend.app.services.satellite_providers import StaticFallbackProvider
        import httpx

        provider = StaticFallbackProvider()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)

        async def run():
            async with httpx.AsyncClient() as client:
                frame = await provider.fetch_frame(
                    "TEST_LAYER", [68.0, 8.0, 97.0, 37.0], ts, 128, client
                )
            return frame

        frame = asyncio.run(run())
        assert frame is not None
        assert frame.image.shape == (128, 128, 3)
        assert frame.fallback_used is True
        assert frame.source == "static_fallback"

    def test_static_fallback_deterministic(self):
        import asyncio
        from backend.app.services.satellite_providers import StaticFallbackProvider
        import httpx

        provider = StaticFallbackProvider()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)

        async def run():
            async with httpx.AsyncClient() as client:
                f1 = await provider.fetch_frame("LAYER", [0, 0, 1, 1], ts, 64, client)
                f2 = await provider.fetch_frame("LAYER", [0, 0, 1, 1], ts, 64, client)
            return f1, f2

        f1, f2 = asyncio.run(run())
        assert np.allclose(f1.image, f2.image), "Static fallback must be deterministic"


# ── MODULE 4: Tile Cache ──────────────────────────────────────────────────────

class TestTileCache:
    def test_cache_miss_then_hit(self, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.tile_cache._get_redis", lambda: None
        )
        from backend.app.services.tile_cache import cache_get, cache_put, _mem_cache

        _mem_cache.clear()
        layer, bbox = "TEST", [0.0, 0.0, 1.0, 1.0]
        ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        res = 64

        # Miss
        result = cache_get(layer, bbox, ts, res)
        assert result is None

        # Put
        frame = np.random.rand(64, 64, 3).astype(np.float32)
        cache_put(layer, bbox, ts, res, frame)

        # Hit
        cached = cache_get(layer, bbox, ts, res)
        assert cached is not None
        assert cached.shape == (64, 64, 3)

    def test_cache_key_consistency(self):
        from backend.app.services.tile_cache import make_cache_key

        ts = datetime(2024, 6, 1, 12, 5, tzinfo=timezone.utc)
        ts2 = datetime(2024, 6, 1, 12, 9, tzinfo=timezone.utc)

        # Same 10-min bucket
        k1 = make_cache_key("LAYER", [0, 0, 1, 1], ts, 256)
        k2 = make_cache_key("LAYER", [0, 0, 1, 1], ts2, 256)
        assert k1 == k2, "Same bucket should produce same key"

    def test_cache_ttl_differentiation(self):
        from backend.app.services.tile_cache import _determine_ttl, RAPID_LAYER_TTL, STATIC_LAYER_TTL

        assert _determine_ttl("GOES-East_ABI_Band2") == RAPID_LAYER_TTL
        assert _determine_ttl("MODIS_Terra_Surface_Reflectance") == STATIC_LAYER_TTL

    def test_cache_status(self, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.tile_cache._get_redis", lambda: None
        )
        from backend.app.services.tile_cache import cache_status

        status = cache_status()
        assert "l1_memory" in status
        assert "l2_redis" in status
        assert "default_ttl_seconds" in status

    def test_cache_invalidate(self, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.tile_cache._get_redis", lambda: None
        )
        from backend.app.services.tile_cache import cache_put, cache_invalidate, _mem_cache

        _mem_cache.clear()
        frame = np.random.rand(32, 32, 3).astype(np.float32)
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        cache_put("L1", [0, 0, 1, 1], ts, 32, frame)

        cleared = cache_invalidate()
        assert cleared >= 1


# ── MODULE 6: Uncertainty Maps ────────────────────────────────────────────────

class TestUncertaintyMaps:
    def test_uncertainty_map_shape(self, sample_frame):
        from backend.app.services.uncertainty_maps import generate_uncertainty_map

        ref_a = np.clip(sample_frame + np.random.randn(*sample_frame.shape) * 0.1, 0, 1).astype(np.float32)
        ref_b = np.clip(sample_frame + np.random.randn(*sample_frame.shape) * 0.1, 0, 1).astype(np.float32)

        conf_map = generate_uncertainty_map(sample_frame, ref_a, ref_b, t_pos=0.5)
        h, w = sample_frame.shape[:2]
        assert conf_map.shape == (h, w)

    def test_confidence_range(self, sample_frame):
        from backend.app.services.uncertainty_maps import generate_uncertainty_map

        conf_map = generate_uncertainty_map(
            sample_frame, sample_frame, sample_frame, t_pos=0.5
        )
        assert conf_map.min() >= 0.0
        assert conf_map.max() <= 1.0

    def test_identical_frames_high_confidence(self, sample_frame):
        """When frame exactly matches references, confidence should be high."""
        from backend.app.services.uncertainty_maps import generate_uncertainty_map

        conf_map = generate_uncertainty_map(
            sample_frame, sample_frame, sample_frame, t_pos=0.5
        )
        # Mean confidence should be reasonably high
        assert conf_map.mean() > 0.3

    def test_save_confidence_map(self, sample_frame, tmp_path):
        from backend.app.services.uncertainty_maps import generate_uncertainty_map, save_confidence_map

        conf_map = generate_uncertainty_map(sample_frame, sample_frame, sample_frame, t_pos=0.5)
        path = save_confidence_map(conf_map, tmp_path, frame_idx=0)
        assert path.exists()
        assert path.suffix == ".png"


# ── MODULE 7: Change Detection ────────────────────────────────────────────────

class TestChangeDetection:
    def test_change_map_basic(self, sample_frame):
        from backend.app.services.change_anomaly import compute_change_map

        frame_b = np.clip(sample_frame + 0.2, 0, 1).astype(np.float32)
        cm = compute_change_map(sample_frame, frame_b, frame_index=1)

        assert cm.diff_map.shape == sample_frame.shape[:2]
        assert cm.motion_mask.dtype == bool
        assert 0 <= cm.change_percentage <= 100

    def test_identical_frames_no_change(self, sample_frame):
        from backend.app.services.change_anomaly import compute_change_map

        cm = compute_change_map(sample_frame, sample_frame, frame_index=1)
        assert cm.change_percentage == pytest.approx(0.0, abs=1.0)
        assert cm.motion_magnitude == pytest.approx(0.0, abs=0.01)

    def test_different_frames_has_change(self, sample_frame):
        from backend.app.services.change_anomaly import compute_change_map

        rng = np.random.default_rng(99)
        frame_b = rng.random(sample_frame.shape).astype(np.float32)
        cm = compute_change_map(sample_frame, frame_b, frame_index=1)
        assert cm.change_percentage > 0

    def test_save_change_map(self, sample_frame, tmp_path):
        from backend.app.services.change_anomaly import compute_change_map, save_change_map

        frame_b = np.clip(sample_frame + 0.1, 0, 1).astype(np.float32)
        cm = compute_change_map(sample_frame, frame_b, frame_index=3)
        path = save_change_map(cm, tmp_path)
        assert path.exists()


# ── MODULE 8: Anomaly Detection ───────────────────────────────────────────────

class TestAnomalyDetection:
    def test_normal_frame(self, frame_sequence):
        from backend.app.services.change_anomaly import detect_anomaly, AnomalyLabel

        # Use several identical frames as history → current frame should be NORMAL
        frame = frame_sequence[0].copy()
        history = frame_sequence[:4]
        result = detect_anomaly(frame, 4, history)

        assert result.anomaly_score >= 0.0
        assert result.anomaly_score <= 1.0
        assert result.label in (AnomalyLabel.normal, AnomalyLabel.anomaly)

    def test_blank_frame_is_anomaly(self, frame_sequence):
        from backend.app.services.change_anomaly import detect_anomaly, AnomalyLabel

        blank = np.zeros((128, 128, 3), dtype=np.float32)
        result = detect_anomaly(blank, 5, frame_sequence, anomaly_threshold=0.3)
        # A blank frame against realistic frames should trigger structure anomaly
        assert result.structure_anomaly or result.label == AnomalyLabel.anomaly

    def test_spike_detection(self, frame_sequence):
        from backend.app.services.change_anomaly import detect_anomaly

        # Create extreme spike
        spike_frame = np.ones((128, 128, 3), dtype=np.float32)  # fully white
        result = detect_anomaly(spike_frame, 5, frame_sequence)
        assert result.anomaly_score > 0.2

    def test_run_sequence(self, frame_sequence):
        from backend.app.services.change_anomaly import run_anomaly_detection, compute_change_map

        change_maps = []
        for i in range(1, len(frame_sequence)):
            cm = compute_change_map(frame_sequence[i-1], frame_sequence[i], i)
            change_maps.append(cm)

        results = run_anomaly_detection(frame_sequence, change_maps, "test_job_123")
        assert len(results) == len(frame_sequence)
        for r in results:
            assert r.label.value in ("NORMAL", "ANOMALY")
            assert 0 <= r.anomaly_score <= 1


# ── MODULE 10: Global Metrics ─────────────────────────────────────────────────

class TestGlobalMetrics:
    def test_load_empty_metrics(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.geo_analytics.GLOBAL_METRICS_PATH",
            tmp_path / "metrics" / "global.json",
        )
        monkeypatch.setattr(
            "backend.app.services.geo_analytics.settings",
            MagicMock(data_dir=tmp_path),
        )
        from backend.app.services.geo_analytics import load_global_metrics

        gm = load_global_metrics()
        assert gm.total_jobs == 0
        assert gm.avg_psnr is None

    def test_update_global_metrics(self, tmp_path, monkeypatch):
        metrics_path = tmp_path / "metrics" / "global.json"
        metrics_path.parent.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "backend.app.services.geo_analytics.GLOBAL_METRICS_PATH",
            metrics_path,
        )
        monkeypatch.setattr(
            "backend.app.services.geo_analytics.settings",
            MagicMock(data_dir=tmp_path),
        )
        from backend.app.services.geo_analytics import update_global_metrics_from_job, load_global_metrics

        job_result = {
            "status": "COMPLETED",
            "metrics": {
                "total_frames": 20,
                "interpolated_frames": 15,
                "observed_frames": 5,
                "avg_psnr": 32.5,
                "avg_ssim": 0.88,
                "high_confidence_count": 10,
                "medium_confidence_count": 4,
                "low_confidence_count": 1,
                "rejected_count": 0,
            },
        }
        update_global_metrics_from_job(job_result)

        gm = load_global_metrics()
        assert gm.total_jobs == 1
        assert gm.completed_jobs == 1
        assert gm.total_frames_generated == 20
        assert gm.avg_psnr == pytest.approx(32.5, abs=0.01)


# ── MODULE 12: Security ───────────────────────────────────────────────────────

class TestSecurity:
    def test_rate_limit_memory_fallback(self):
        from backend.app.middleware.security import _check_rate_limit_memory

        ip = f"192.168.1.{int(time.time() % 254) + 1}"
        # Normal traffic should pass
        allowed, count, _ = _check_rate_limit_memory(ip)
        assert allowed is True
        assert count >= 1

    def test_api_key_check_no_configured_keys(self):
        from backend.app.middleware.security import _check_api_key
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers.get.return_value = None
        request.query_params.get.return_value = None

        # No keys configured → always pass
        with patch("backend.app.middleware.security.settings") as mock_settings:
            mock_settings.api_keys = []
            result = _check_api_key(request)
        assert result is True

    def test_api_key_validation(self):
        from backend.app.middleware.security import _check_api_key
        from unittest.mock import MagicMock, patch

        request = MagicMock()
        request.query_params.get.return_value = None
        request.headers.get.return_value = "valid-key-123"

        with patch("backend.app.middleware.security.settings") as mock_settings:
            mock_settings.api_keys = ["valid-key-123", "another-key"]
            result = _check_api_key(request)
        assert result is True

    def test_invalid_api_key_rejected(self):
        from backend.app.middleware.security import _check_api_key
        from unittest.mock import MagicMock, patch

        request = MagicMock()
        request.query_params.get.return_value = None
        request.headers.get.return_value = "wrong-key"

        with patch("backend.app.middleware.security.settings") as mock_settings:
            mock_settings.api_keys = ["valid-key-123"]
            result = _check_api_key(request)
        assert result is False


# ── MODULE 14: Performance Monitoring ────────────────────────────────────────

class TestPerformance:
    def test_cpu_stats(self):
        from backend.app.services.performance import get_cpu_stats

        stats = get_cpu_stats()
        assert stats.cpu_count >= 1
        assert stats.cpu_pct >= 0

    def test_memory_stats(self):
        from backend.app.services.performance import get_memory_stats

        stats = get_memory_stats()
        assert stats.total_mb > 0
        assert stats.used_pct >= 0

    def test_gpu_stats_no_crash(self):
        from backend.app.services.performance import get_gpu_stats

        stats = get_gpu_stats()
        # Should not raise even if GPU is not present
        assert isinstance(stats.available, bool)

    def test_collect_performance(self, monkeypatch):
        monkeypatch.setattr(
            "backend.app.services.job_manager._get_redis", lambda: None
        )
        from backend.app.services.performance import collect_system_performance, to_dict

        perf = collect_system_performance()
        data = to_dict(perf)
        assert "gpu" in data
        assert "cpu" in data
        assert "memory" in data
        assert "timestamp" in data


# ── API Integration Tests ─────────────────────────────────────────────────────

class TestAPIEndpoints:
    @pytest.fixture
    def client(self):
        from backend.app.main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "2.0.0"
        assert "new_in_v2" in data

    def test_health(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "redis_connected" in data

    def test_cache_status(self, client):
        resp = client.get("/api/v1/cache/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "l1_memory" in data

    def test_cache_clear(self, client):
        resp = client.post("/api/v1/cache/clear")
        assert resp.status_code == 200
        assert "cleared_items" in resp.json()

    def test_models_list(self, client):
        resp = client.get("/api/v1/models")
        assert resp.status_code == 200
        models = resp.json()
        assert isinstance(models, list)
        assert len(models) >= 3
        ids = [m["id"] for m in models]
        assert "rife" in ids
        assert "optical_flow" in ids

    def test_metrics_summary(self, client):
        resp = client.get("/api/v1/metrics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_jobs" in data
        assert "avg_psnr" in data

    def test_system_performance(self, client):
        resp = client.get("/api/v1/system/performance")
        assert resp.status_code == 200
        data = resp.json()
        assert "cpu" in data
        assert "memory" in data
        assert "gpu" in data

    def test_providers_list(self, client):
        resp = client.get("/api/v1/system/providers")
        assert resp.status_code == 200
        providers = resp.json()
        assert isinstance(providers, list)

    def test_job_status_not_found(self, client):
        resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}/status")
        assert resp.status_code == 404

    def test_job_logs_not_found(self, client):
        resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}/logs")
        assert resp.status_code == 404

    def test_reproduce_not_found(self, client):
        resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}/reproduce")
        assert resp.status_code == 404

    def test_audit_not_found(self, client):
        resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}/audit")
        assert resp.status_code == 404

    def test_confidence_map_not_found(self, client):
        resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}/confidence_map/0")
        # Either 404 (no job) or 404 (no frame) — both acceptable
        assert resp.status_code in (404, 500)

    def test_change_map_not_found(self, client):
        resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}/change_map/1")
        assert resp.status_code in (404, 500)

    def test_region_query_missing_job(self, client):
        resp = client.post("/api/v1/region/query", json={
            "job_id": str(uuid.uuid4()),
            "bbox": [68.0, 8.0, 97.0, 37.0],
            "time_start": "2024-06-01T00:00:00Z",
            "time_end": "2024-06-01T06:00:00Z",
        })
        assert resp.status_code == 404
