"""AetherGIS — Celery task definitions — Production Grade."""
from __future__ import annotations

import asyncio
from celery import Celery

from backend.app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "AetherGIS",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=600,
    task_time_limit=900,
    # Priority queue support (MODULE 1)
    task_queue_max_priority=10,
    task_default_priority=5,
    worker_hijack_root_logger=False,
)


@celery_app.task(bind=True, name="AetherGIS.pipeline.run", max_retries=3)
def run_pipeline_task(self, job_payload: dict) -> dict:
    """Celery wrapper for the async pipeline with full audit + retry support."""
    from datetime import datetime
    from backend.app.services.pipeline import run_pipeline
    from backend.app.services.job_manager import (
        update_job, fail_job, complete_job, append_audit_event,
    )

    job_id = job_payload["job_id"]

    def on_progress(progress: float, message: str) -> None:
        stage = (
            "ingestion" if progress < 0.25 else
            "preprocessing" if progress < 0.35 else
            "interpolation" if progress < 0.76 else
            "confidence" if progress < 0.92 else
            "export"
        )
        self.update_state(state="RUNNING", meta={"progress": progress, "message": message, "stage": stage})
        update_job(job_id, status="RUNNING", progress=progress, stage=stage, message=message)

    self.update_state(state="RUNNING", meta={"progress": 0.0, "message": "Starting pipeline", "stage": "queued"})
    update_job(job_id, status="RUNNING", stage="ingestion", progress=0.02, message="Celery worker picked up job")
    append_audit_event(job_id, "celery_task_started", {"task_id": self.request.id, "worker": self.request.hostname})

    try:
        result = asyncio.run(run_pipeline(
            job_id=job_payload["job_id"],
            layer_id=job_payload["layer_id"],
            data_source=job_payload["data_source"],
            bbox=job_payload["bbox"],
            time_start=datetime.fromisoformat(job_payload["time_start"]),
            time_end=datetime.fromisoformat(job_payload["time_end"]),
            resolution=job_payload["resolution"],
            interpolation_model=job_payload["interpolation_model"],
            n_intermediate=job_payload["n_intermediate"],
            step_minutes=job_payload.get("step_minutes"),
            include_low_confidence=job_payload["include_low_confidence"],
            progress_callback=on_progress,
        ))
        result_dict = result.model_dump(mode="json")
        complete_job(job_id, result_dict)
        append_audit_event(job_id, "celery_task_completed", {"task_id": self.request.id})
        return result_dict

    except Exception as exc:
        append_audit_event(job_id, "celery_task_failed", {"error": str(exc), "attempt": self.request.retries + 1})

        # Retry with exponential backoff (MODULE 13)
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries * 10
            update_job(job_id, status="RUNNING", message=f"Retrying after error (attempt {self.request.retries + 2})")
            raise self.retry(exc=exc, countdown=countdown)

        fail_job(job_id, str(exc))
        # Let Celery handle the final FAILURE state with the original exception object
        # This prevents the 'ValueError: Exception information must include the exception type' error
        raise
