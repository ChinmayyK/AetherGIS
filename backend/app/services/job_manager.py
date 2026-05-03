"""AetherGIS — Production Job Manager (MODULE 1 + 2 + 15).

Replaces the simple in-memory job store with a Redis-backed persistent store
that supports:
  • Priority queues (high / normal / low)
  • Stage-level progress tracking
  • ETA calculation
  • Queue position reporting
  • Full audit trail persistence
  • Job manifest for reproducibility
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import redis as redis_sync

from backend.app.config import get_settings
from backend.app.services.persistence import create_run, delete_run as delete_persisted_run, update_run_state
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


# ── Priority ─────────────────────────────────────────────────────────────────

class JobPriority(str, Enum):
    high = "high"
    normal = "normal"
    low = "low"


_PRIORITY_SCORE = {
    JobPriority.high: 0,
    JobPriority.normal: 5,
    JobPriority.low: 10,
}


# ── Stage definitions ─────────────────────────────────────────────────────────

PIPELINE_STAGES = [
    "queued",
    "ingestion",
    "preprocessing",
    "interpolation",
    "confidence",
    "export",
    "completed",
]


# ── Job data model ────────────────────────────────────────────────────────────

@dataclass
class JobRecord:
    job_id: str
    user_id: Optional[str] = None
    status: str = "QUEUED"
    priority: str = "normal"
    progress: float = 0.0
    current_stage: str = "queued"
    stage_index: int = 0
    message: Optional[str] = None
    error: Optional[str] = None
    result: Optional[dict] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    estimated_completion: Optional[str] = None
    queue_position: int = 0
    logs: list[dict] = field(default_factory=list)
    checkpoints: dict[str, Any] = field(default_factory=dict)
    manifest: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ── Redis helpers ─────────────────────────────────────────────────────────────

_redis_client: Optional[redis_sync.Redis] = None


def _get_redis() -> Optional[redis_sync.Redis]:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis_sync.from_url(
                settings.redis_url,
                socket_connect_timeout=2,
                decode_responses=True,
            )
            _redis_client.ping()
        except Exception as exc:
            logger.warning("Redis unavailable — falling back to in-memory job store", error=str(exc))
            _redis_client = None
    return _redis_client


# ── In-memory fallback ────────────────────────────────────────────────────────

_mem_jobs: dict[str, dict] = {}

JOB_TTL_SECONDS = int(settings.run_artifact_ttl_hours) * 3600


def _save_job(record: JobRecord) -> None:
    data = json.dumps(record.to_dict())
    r = _get_redis()
    if r:
        r.setex(f"aethergis:job:{record.job_id}", JOB_TTL_SECONDS, data)
    else:
        _mem_jobs[record.job_id] = record.to_dict()


def _load_job(job_id: str) -> Optional[JobRecord]:
    r = _get_redis()
    if r:
        raw = r.get(f"aethergis:job:{job_id}")
        if raw:
            return JobRecord.from_dict(json.loads(raw))
    else:
        data = _mem_jobs.get(job_id)
        if data:
            return JobRecord.from_dict(data)
    return None


# ── Queue management ──────────────────────────────────────────────────────────

QUEUE_KEY = "aethergis:queue"
RUNNING_KEY = "aethergis:running"


def _enqueue(job_id: str, priority: JobPriority) -> int:
    """Add job to priority queue. Returns queue position."""
    score = _PRIORITY_SCORE[priority] * 1e12 + time.time()
    r = _get_redis()
    if r:
        r.zadd(QUEUE_KEY, {job_id: score})
        pos = r.zrank(QUEUE_KEY, job_id)
        return int(pos) + 1 if pos is not None else 1
    return 1


def _dequeue(job_id: str) -> None:
    r = _get_redis()
    if r:
        r.zrem(QUEUE_KEY, job_id)


def _mark_running(job_id: str) -> None:
    r = _get_redis()
    if r:
        r.sadd(RUNNING_KEY, job_id)


def _clear_running(job_id: str) -> None:
    r = _get_redis()
    if r:
        r.srem(RUNNING_KEY, job_id)


def get_queue_position(job_id: str) -> int:
    r = _get_redis()
    if r:
        pos = r.zrank(QUEUE_KEY, job_id)
        return int(pos) + 1 if pos is not None else 0
    return 0


def get_queue_depth() -> int:
    r = _get_redis()
    if r:
        return int(r.zcard(QUEUE_KEY))
    return 0


def get_active_job_count() -> int:
    r = _get_redis()
    if r:
        return int(r.scard(RUNNING_KEY))
    return sum(1 for job in _mem_jobs.values() if job.get("status") == "RUNNING")


def can_accept_new_job() -> tuple[bool, str | None]:
    active = get_active_job_count()
    if active >= settings.max_active_runs:
        return False, "Demo server is busy with another active run. Please wait for it to finish."
    queued = get_queue_depth()
    if queued >= settings.max_queued_runs:
        return False, "The queue is full right now. Please try again after the current run completes."
    return True, None


# ── ETA estimation ────────────────────────────────────────────────────────────

# Rolling average of recent job durations (seconds)
DURATIONS_KEY = "aethergis:durations"
MAX_DURATION_SAMPLES = 20
DEFAULT_DURATION_SEC = 120.0


def _record_duration(seconds: float) -> None:
    r = _get_redis()
    if r:
        r.lpush(DURATIONS_KEY, seconds)
        r.ltrim(DURATIONS_KEY, 0, MAX_DURATION_SAMPLES - 1)


def _avg_duration() -> float:
    r = _get_redis()
    if r:
        raw = r.lrange(DURATIONS_KEY, 0, -1)
        if raw:
            return sum(float(x) for x in raw) / len(raw)
    return DEFAULT_DURATION_SEC


def _estimate_completion(queue_pos: int) -> Optional[str]:
    avg = _avg_duration()
    eta_sec = avg * queue_pos
    from datetime import timedelta
    eta_dt = datetime.now(timezone.utc) + timedelta(seconds=eta_sec)
    return eta_dt.isoformat()


# ── Public API ────────────────────────────────────────────────────────────────

def create_job(
    job_id: str,
    manifest: Optional[dict] = None,
    priority: JobPriority = JobPriority.normal,
    message: str = "Queued",
    payload: Optional[dict] = None,
    session_id: Optional[str] = None,
    session_name: Optional[str] = None,
    user_id: Optional[str] = None,
) -> JobRecord:
    pos = _enqueue(job_id, priority)
    eta = _estimate_completion(pos)
    record = JobRecord(
        job_id=job_id,
        user_id=user_id,
        status="QUEUED",
        priority=priority.value,
        queue_position=pos,
        estimated_completion=eta,
        message=message,
        manifest=manifest,
    )
    _save_job(record)
    if payload:
        create_run(
            job_id=job_id,
            payload=payload,
            manifest=manifest,
            priority=priority.value,
            session_id=session_id,
            session_name=session_name,
            user_id=user_id,
        )
    _append_audit(job_id, "job_created", {"priority": priority.value, "queue_position": pos})
    logger.info("Job created", job_id=job_id, priority=priority.value, queue_pos=pos)
    return record


def update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    progress: Optional[float] = None,
    stage: Optional[str] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
    checkpoint: Optional[tuple[str, Any]] = None,
) -> Optional[JobRecord]:
    record = _load_job(job_id)
    if record is None:
        return None

    if status:
        record.status = status
        if status == "RUNNING" and record.started_at is None:
            record.started_at = datetime.now(timezone.utc).isoformat()
            _dequeue(job_id)
            _mark_running(job_id)
    if progress is not None:
        record.progress = max(0.0, min(1.0, progress))
    if stage:
        record.current_stage = stage
        record.stage_index = PIPELINE_STAGES.index(stage) if stage in PIPELINE_STAGES else record.stage_index
    if message:
        record.message = message
        # Also append to log stream
        record.logs.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "msg": message,
            "progress": record.progress,
            "stage": record.current_stage,
        })
        # Keep last 200 log lines
        if len(record.logs) > 200:
            record.logs = record.logs[-200:]
    if error:
        record.error = error
    if checkpoint:
        ck_key, ck_val = checkpoint
        record.checkpoints[ck_key] = ck_val

    _save_job(record)
    update_run_state(
        job_id,
        status=status,
        progress=record.progress,
        stage=record.current_stage,
        message=record.message,
        error=record.error,
    )
    return record


def complete_job(job_id: str, result: dict, message: str = "Pipeline completed") -> Optional[JobRecord]:
    record = _load_job(job_id)
    if record is None:
        return None

    now = datetime.now(timezone.utc)
    record.status = "COMPLETED"
    record.progress = 1.0
    record.current_stage = "completed"
    record.stage_index = len(PIPELINE_STAGES) - 1
    record.message = message
    record.result = result
    record.completed_at = now.isoformat()

    # Record duration for ETA calibration
    duration = None
    if record.started_at:
        started = datetime.fromisoformat(record.started_at)
        duration = (now - started).total_seconds()
        _record_duration(duration)

    _dequeue(job_id)
    _clear_running(job_id)
    _save_job(record)
    update_run_state(
        job_id,
        status=record.status,
        progress=record.progress,
        stage=record.current_stage,
        message=record.message,
        result=result,
        manifest=record.manifest,
    )
    _append_audit(job_id, "job_completed", {"duration_sec": duration})
    return record


def fail_job(job_id: str, error: str, message: str = "Pipeline failed") -> Optional[JobRecord]:
    record = _load_job(job_id)
    if record is None:
        return None

    record.status = "FAILED"
    record.message = message
    record.error = error
    record.completed_at = datetime.now(timezone.utc).isoformat()
    _dequeue(job_id)
    _clear_running(job_id)
    _save_job(record)
    update_run_state(
        job_id,
        status=record.status,
        progress=record.progress,
        stage=record.current_stage,
        message=record.message,
        error=error,
    )
    _append_audit(job_id, "job_failed", {"error": error})
    return record


def cancel_job(job_id: str) -> Optional[JobRecord]:
    record = _load_job(job_id)
    if record is None:
        return None

    record.status = "CANCELLED"
    record.message = "Cancelled by user"
    record.completed_at = datetime.now(timezone.utc).isoformat()
    _dequeue(job_id)
    _clear_running(job_id)
    _save_job(record)
    update_run_state(
        job_id,
        status=record.status,
        progress=record.progress,
        stage=record.current_stage,
        message=record.message,
    )
    _append_audit(job_id, "job_cancelled", {})
    return record


def get_job(job_id: str) -> Optional[JobRecord]:
    return _load_job(job_id)


def get_job_logs(job_id: str) -> list[dict]:
    record = _load_job(job_id)
    if record is None:
        return []
    return record.logs


def save_manifest(job_id: str, manifest: dict) -> None:
    """Persist reproducibility manifest to disk and job record."""
    runs_dir = settings.data_dir / "runs" / job_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    record = _load_job(job_id)
    if record:
        record.manifest = manifest
        _save_job(record)
        update_run_state(job_id, manifest=manifest)

    logger.info("Manifest saved", job_id=job_id, path=str(manifest_path))


def load_manifest(job_id: str) -> Optional[dict]:
    manifest_path = settings.data_dir / "runs" / job_id / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    record = _load_job(job_id)
    if record and record.manifest:
        return record.manifest
    return None


# ── Audit trail ───────────────────────────────────────────────────────────────

def _append_audit(job_id: str, event: str, data: dict) -> None:
    audit_dir = settings.data_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / f"{job_id}.json"

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **data,
    }

    existing: list[dict] = []
    if audit_file.exists():
        try:
            with open(audit_file) as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing.append(entry)
    with open(audit_file, "w") as f:
        json.dump(existing, f, indent=2, default=str)


def append_audit_event(job_id: str, event: str, data: dict) -> None:
    """Public API for pipeline stages to log audit events."""
    _append_audit(job_id, event, data)


def load_audit_trail(job_id: str) -> list[dict]:
    audit_file = settings.data_dir / "audit" / f"{job_id}.json"
    if not audit_file.exists():
        return []
    with open(audit_file) as f:
        return json.load(f)


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def save_checkpoint(job_id: str, stage: str, data: Any) -> None:
    """Save stage checkpoint for failure recovery."""
    ckpt_dir = settings.data_dir / "checkpoints" / job_id
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = ckpt_dir / f"{stage}.json"
    with open(ckpt_path, "w") as f:
        json.dump(data, f, default=str)
    update_job(job_id, checkpoint=(stage, str(ckpt_path)))
    logger.debug("Checkpoint saved", job_id=job_id, stage=stage)


def load_checkpoint(job_id: str, stage: str) -> Optional[Any]:
    """Load checkpoint data for a stage."""
    ckpt_path = settings.data_dir / "checkpoints" / job_id / f"{stage}.json"
    if not ckpt_path.exists():
        return None
    with open(ckpt_path) as f:
        return json.load(f)


def purge_job(job_id: str) -> None:
    r = _get_redis()
    if r:
        r.delete(f"aethergis:job:{job_id}")
        r.zrem(QUEUE_KEY, job_id)
        r.srem(RUNNING_KEY, job_id)
    _mem_jobs.pop(job_id, None)
    delete_persisted_run(job_id)
