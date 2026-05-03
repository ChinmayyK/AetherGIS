"""AetherGIS — Performance Monitoring (MODULE 14).

Tracks:
  • GPU utilisation + memory
  • CPU / RAM
  • Batch processing stats
  • System-wide throughput
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from backend.app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GPUStats:
    available: bool
    device_name: Optional[str] = None
    utilization_pct: Optional[float] = None
    memory_used_mb: Optional[float] = None
    memory_total_mb: Optional[float] = None
    memory_free_mb: Optional[float] = None
    temperature_c: Optional[float] = None


@dataclass
class CPUStats:
    cpu_pct: float
    cpu_count: int
    load_avg_1m: Optional[float] = None


@dataclass
class MemoryStats:
    total_mb: float
    available_mb: float
    used_mb: float
    used_pct: float


@dataclass
class SystemPerformance:
    timestamp: str
    gpu: GPUStats
    cpu: CPUStats
    memory: MemoryStats
    active_jobs: int = 0
    queued_jobs: int = 0
    frames_processed_last_hour: int = 0
    avg_job_duration_sec: Optional[float] = None


def get_gpu_stats() -> GPUStats:
    try:
        import torch
        if not torch.cuda.is_available():
            return GPUStats(available=False)

        device = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(device)
        mem = torch.cuda.mem_get_info(device)
        free_bytes, total_bytes = mem

        util_pct = None
        temp_c = None
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            util_pct = float(util.gpu)
            temp_c = float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            pass

        return GPUStats(
            available=True,
            device_name=props.name,
            utilization_pct=util_pct,
            memory_used_mb=round((total_bytes - free_bytes) / 1_048_576, 1),
            memory_total_mb=round(total_bytes / 1_048_576, 1),
            memory_free_mb=round(free_bytes / 1_048_576, 1),
            temperature_c=temp_c,
        )
    except Exception as exc:
        logger.debug("GPU stats unavailable", error=str(exc))
        return GPUStats(available=False)


def get_cpu_stats() -> CPUStats:
    try:
        import psutil
        load = psutil.getloadavg()
        return CPUStats(
            cpu_pct=psutil.cpu_percent(interval=0.1),
            cpu_count=psutil.cpu_count(logical=True),
            load_avg_1m=round(load[0], 2),
        )
    except Exception:
        import os
        try:
            load = os.getloadavg()
            return CPUStats(cpu_pct=0.0, cpu_count=os.cpu_count() or 1, load_avg_1m=load[0])
        except Exception:
            return CPUStats(cpu_pct=0.0, cpu_count=1)


def get_memory_stats() -> MemoryStats:
    try:
        import psutil
        m = psutil.virtual_memory()
        return MemoryStats(
            total_mb=round(m.total / 1_048_576, 1),
            available_mb=round(m.available / 1_048_576, 1),
            used_mb=round(m.used / 1_048_576, 1),
            used_pct=round(m.percent, 1),
        )
    except Exception:
        return MemoryStats(total_mb=0, available_mb=0, used_mb=0, used_pct=0)


def collect_system_performance() -> SystemPerformance:
    from datetime import datetime, timezone
    from backend.app.services.job_manager import get_queue_depth

    gpu = get_gpu_stats()
    cpu = get_cpu_stats()
    mem = get_memory_stats()

    # Queue depth
    queued = get_queue_depth()

    # Average job duration
    from backend.app.services.job_manager import _avg_duration
    avg_dur = _avg_duration()

    return SystemPerformance(
        timestamp=datetime.now(timezone.utc).isoformat(),
        gpu=gpu,
        cpu=cpu,
        memory=mem,
        queued_jobs=queued,
        avg_job_duration_sec=round(avg_dur, 1),
    )


def to_dict(perf: SystemPerformance) -> dict:
    return asdict(perf)
