"""AetherGIS — Smart Tile Cache (MODULE 4).

Tile-based caching for satellite imagery frames keyed by:
    (bbox_hash, timestamp_rounded, layer_id, resolution)

Features:
  • Redis-backed persistent cache with TTL
  • In-memory LRU cache for hot tiles
  • Tile-based decomposition for large regions
  • Cache status endpoint data
  • Selective invalidation
"""
from __future__ import annotations

import hashlib
import io
import time
from collections import OrderedDict
from datetime import datetime
from typing import Optional

import numpy as np
from PIL import Image

from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

# Cache TTL per layer type
DEFAULT_TTL_SECONDS = 3600        # 1 hour
STATIC_LAYER_TTL = 86400          # 24 hours (slowly changing data)
RAPID_LAYER_TTL = 600             # 10 minutes (rapidly updating GOES)
MAX_CACHE_SIZE_MB = 512
MAX_MEM_ITEMS = 256               # LRU memory cache size


# ── Cache key generation ───────────────────────────────────────────────────────

def _round_timestamp(ts: datetime, resolution_minutes: int = 10) -> str:
    """Round timestamp to nearest resolution_minutes bucket."""
    rounded_minute = (ts.minute // resolution_minutes) * resolution_minutes
    rounded = ts.replace(minute=rounded_minute, second=0, microsecond=0)
    return rounded.strftime("%Y%m%dT%H%M")


def _bbox_hash(bbox: list[float]) -> str:
    key = "_".join(f"{v:.4f}" for v in bbox)
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def make_cache_key(
    layer_id: str,
    bbox: list[float],
    timestamp: datetime,
    resolution: int,
    time_bucket_minutes: int = 10,
) -> str:
    ts_bucket = _round_timestamp(timestamp, time_bucket_minutes)
    bbox_h = _bbox_hash(bbox)
    raw = f"{layer_id}|{bbox_h}|{ts_bucket}|{resolution}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ── Numpy ↔ bytes serialisation ───────────────────────────────────────────────

def _arr_to_bytes(arr: np.ndarray) -> bytes:
    img = Image.fromarray((arr * 255).clip(0, 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()


def _bytes_to_arr(raw: bytes, resolution: int) -> np.ndarray:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = img.resize((resolution, resolution), Image.LANCZOS)
    return np.array(img, dtype=np.float32) / 255.0


# ── In-memory LRU ─────────────────────────────────────────────────────────────

class _LRUCache:
    def __init__(self, maxsize: int = MAX_MEM_ITEMS):
        self._store: OrderedDict[str, tuple[np.ndarray, float]] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Optional[np.ndarray]:
        if key not in self._store:
            return None
        arr, expires = self._store[key]
        if time.time() > expires:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return arr

    def put(self, key: str, arr: np.ndarray, ttl: int) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (arr, time.time() + ttl)
        if len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> int:
        n = len(self._store)
        self._store.clear()
        return n

    def size(self) -> int:
        return len(self._store)

    def stats(self) -> dict:
        now = time.time()
        live = sum(1 for _, (_, exp) in self._store.items() if exp > now)
        return {"total_items": len(self._store), "live_items": live, "maxsize": self._maxsize}


_mem_cache = _LRUCache(MAX_MEM_ITEMS)


# ── Redis cache tier ──────────────────────────────────────────────────────────

def _get_redis():
    try:
        import redis as redis_sync
        r = redis_sync.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        return r
    except Exception:
        return None


CACHE_PREFIX = "aethergis:tilecache:"


def _determine_ttl(layer_id: str) -> int:
    layer_lower = layer_id.lower()
    if "goes" in layer_lower or "abi" in layer_lower:
        return RAPID_LAYER_TTL
    if "modis" in layer_lower or "landsat" in layer_lower:
        return STATIC_LAYER_TTL
    return DEFAULT_TTL_SECONDS


# ── Public API ────────────────────────────────────────────────────────────────

def cache_get(
    layer_id: str,
    bbox: list[float],
    timestamp: datetime,
    resolution: int,
) -> Optional[np.ndarray]:
    """Retrieve cached frame. Returns None on miss."""
    key = make_cache_key(layer_id, bbox, timestamp, resolution)

    # L1: memory
    arr = _mem_cache.get(key)
    if arr is not None:
        logger.debug("Cache L1 hit", key=key)
        return arr

    # L2: Redis
    r = _get_redis()
    if r:
        raw = r.get(f"{CACHE_PREFIX}{key}")
        if raw:
            try:
                arr = _bytes_to_arr(raw, resolution)
                ttl = _determine_ttl(layer_id)
                _mem_cache.put(key, arr, ttl)
                logger.debug("Cache L2 hit", key=key)
                return arr
            except Exception:
                pass

    return None


def cache_put(
    layer_id: str,
    bbox: list[float],
    timestamp: datetime,
    resolution: int,
    frame: np.ndarray,
) -> str:
    """Store frame in cache. Returns cache key."""
    key = make_cache_key(layer_id, bbox, timestamp, resolution)
    ttl = _determine_ttl(layer_id)

    # L1: memory
    _mem_cache.put(key, frame, ttl)

    # L2: Redis
    r = _get_redis()
    if r:
        try:
            raw = _arr_to_bytes(frame)
            r.setex(f"{CACHE_PREFIX}{key}", ttl, raw)
        except Exception as exc:
            logger.warning("Redis cache put failed", key=key, error=str(exc))

    logger.debug("Frame cached", key=key, ttl=ttl, layer=layer_id)
    return key


def cache_invalidate(
    layer_id: Optional[str] = None,
    bbox: Optional[list[float]] = None,
) -> int:
    """Selectively invalidate cache entries. Returns number of items cleared."""
    if layer_id is None and bbox is None:
        # Full clear
        n = _mem_cache.clear()
        r = _get_redis()
        if r:
            keys = r.keys(f"{CACHE_PREFIX}*")
            if keys:
                n += r.delete(*keys)
        return n

    # Partial clear: clear memory cache fully (we don't have layer-level index)
    n = _mem_cache.clear()
    return n


def cache_status() -> dict:
    """Return cache status metrics."""
    mem_stats = _mem_cache.stats()
    redis_stats: dict = {"connected": False, "key_count": 0, "memory_used_mb": 0}

    r = _get_redis()
    if r:
        try:
            info = r.info("memory")
            keys = r.keys(f"{CACHE_PREFIX}*")
            redis_stats = {
                "connected": True,
                "key_count": len(keys),
                "memory_used_mb": round(info.get("used_memory", 0) / 1_048_576, 2),
                "peak_memory_mb": round(info.get("used_memory_peak", 0) / 1_048_576, 2),
            }
        except Exception:
            redis_stats["connected"] = True

    return {
        "l1_memory": mem_stats,
        "l2_redis": redis_stats,
        "default_ttl_seconds": DEFAULT_TTL_SECONDS,
        "rapid_layer_ttl_seconds": RAPID_LAYER_TTL,
        "static_layer_ttl_seconds": STATIC_LAYER_TTL,
    }
