from __future__ import annotations
from dataclasses import dataclass
from threading import Lock
from typing import Any
from backend.app.models.schemas import JobStatus
@dataclass
class InProcessJob:
    job_id: str
    status: JobStatus = JobStatus.queued
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
_jobs: dict[str, InProcessJob] = {}
_jobs_lock = Lock()
def create_job(job_id: str, message: str | None = None) -> InProcessJob:
    job = InProcessJob(job_id=job_id, message=message)
    with _jobs_lock:
        _jobs[job_id] = job
    return job
def update_job(
    job_id: str,
    *,
    status: JobStatus | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: str | None = None,
) -> InProcessJob | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = max(0.0, min(1.0, progress))
        if message is not None:
            job.message = message
        if error is not None:
            job.error = error
        return job
def complete_job(job_id: str, result: dict[str, Any], message: str = 'Pipeline completed') -> InProcessJob | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job.status = JobStatus.completed
        job.progress = 1.0
        job.message = message
        job.result = result
        job.error = None
        return job
def fail_job(job_id: str, error: str, message: str = 'Pipeline failed') -> InProcessJob | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job.status = JobStatus.failed
        job.message = message
        job.error = error
        return job
def get_job(job_id: str) -> InProcessJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)
