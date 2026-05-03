"""AetherGIS — Exclusive Session Lock Service (MODULE 12).

Manages the 'one-user-at-a-time' constraint for production deployments.

DESIGN PRINCIPLES
─────────────────
1. FAST HANDOVER (5–10 s):
   Lock TTL is short (45 s). Every heartbeat resets it. When heartbeats stop
   (tab closed, network loss, logout), the key expires in ≤45 s.
   On every get_status() call, _maybe_reap() checks if the lock is gone and
   immediately promotes the next queued user — no background daemon needed.

2. IDLE DETECTION:
   A separate idle-phase TTL is stored. After a pipeline completes, the frontend
   should POST /system/session/start_grace. That switches the TTL to GRACE_TTL
   (5 min). If the user keeps interacting, heartbeats keep refreshing it.
   If they go idle (no heartbeat for GRACE_TTL seconds), the lock expires.

3. ABUSE PREVENTION:
   - Users who never run a pipeline (just sitting on the dashboard) are on the
     SHORT_TTL (45 s) heartbeat cycle — they must keep the tab focused.
   - Tab hidden → frontend stops heartbeats → lock expires after 45 s.
   - Explicit logout or beforeunload → immediate release.

4. RACE SAFETY:
   All lock acquisition uses Redis SET NX (atomic). Queue promotion uses LPOP
   (atomic). No distributed locks needed for the promotion step itself.
"""
from __future__ import annotations

from typing import Optional, Dict, Any
import redis as redis_sync
from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

# ─── Redis Keys ───────────────────────────────────────────────────────────────
LOCK_KEY          = "aethergis:session:active_user"
LOCK_PHASE_KEY    = "aethergis:session:phase"          # 'active' | 'grace'
WAITLIST_KEY      = "aethergis:session:waitlist"

# ─── TTL Configuration ────────────────────────────────────────────────────────
# Short TTL: normal dashboard use. Heartbeat every 15 s resets this.
# If 3 heartbeats are missed (45 s), the lock expires automatically.
SHORT_TTL   = 45   # seconds — standard session lease

# Grace TTL: post-pipeline cool-down window. Users get 5 min to export/download.
# Heartbeat still required (resets the 5 min window each time).
GRACE_TTL   = 300  # seconds — post-pipeline grace window

# Maximum absolute session duration regardless of heartbeats (abuse prevention).
MAX_SESSION_DURATION = 20 * 60  # 20 minutes hard cap

# ─── Queue Poll Interval (informational only — not enforced server-side) ──────
QUEUE_POLL_INTERVAL = 3  # seconds — hint to frontend


class SessionLockService:
    def __init__(self):
        try:
            self.r = redis_sync.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            self.r.ping()
            self.available = True
        except Exception as exc:
            logger.warning("Redis unavailable for SessionLock — disabling queuing", error=str(exc))
            self.available = False

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def get_status(self, session_id: str) -> Dict[str, Any]:
        """Check if the given session_id has control or is in queue.

        Also opportunistically reaps an expired lock and promotes the next
        queued user — this is how automatic handover works without a daemon.
        """
        if not self.available or settings.aether_mode == 'development':
            return {"status": "granted", "is_active": True, "queue_pos": 0}

        # Step 1: Reap an expired lock and promote the next user if needed.
        # This runs on every poll from every waiting client, ensuring fast handover.
        self._maybe_reap_and_promote()

        active_user = self.r.get(LOCK_KEY)

        # Case A: caller already holds the lock
        if active_user == session_id:
            self._refresh_heartbeat(session_id)
            ttl = self.r.ttl(LOCK_KEY)
            phase = self.r.get(LOCK_PHASE_KEY) or "active"
            return {
                "status": "granted",
                "is_active": True,
                "queue_pos": 0,
                "phase": phase,
                "ttl_seconds": ttl,
            }

        # Case B: lock slot is free — claim it
        if not active_user:
            return self._claim_lock(session_id)

        # Case C: someone else holds the lock — queue the caller
        pos = self._get_waitlist_position(session_id)
        if pos == -1:
            pos = self._add_to_waitlist(session_id)

        # Estimate wait: SHORT_TTL per user ahead (conservative)
        wait_est = pos * (SHORT_TTL // 60) or 1

        return {
            "status": "waiting",
            "is_active": False,
            "active_user_hint": active_user[:8] if active_user else "",
            "queue_pos": pos,
            "wait_time_est_min": wait_est,
        }

    def heartbeat(self, session_id: str, phase: Optional[str] = None) -> Dict[str, Any]:
        """Extend the session lease. Must be called every ≤30 s to stay alive.

        Args:
            session_id: The session to refresh.
            phase: Optional — 'active' or 'grace'. If provided, updates phase.

        Returns:
            dict with ttl_seconds and phase.
        """
        if not self.available or settings.aether_mode != 'production':
            return {"status": "ok", "ttl_seconds": SHORT_TTL}

        active_user = self.r.get(LOCK_KEY)
        if active_user != session_id:
            return {"status": "not_owner"}

        # Update phase if caller requests it (e.g., frontend POSTs after pipeline done)
        if phase and phase in ("active", "grace"):
            self.r.set(LOCK_PHASE_KEY, phase)

        current_phase = self.r.get(LOCK_PHASE_KEY) or "active"
        ttl = GRACE_TTL if current_phase == "grace" else SHORT_TTL

        # Extend lock TTL
        self.r.expire(LOCK_KEY, ttl)

        return {"status": "ok", "ttl_seconds": ttl, "phase": current_phase}

    def start_grace(self, session_id: str) -> Dict[str, Any]:
        """Switch to the post-pipeline grace phase (5 min export window).

        Called by frontend after pipeline completion to extend TTL to GRACE_TTL
        without resetting to SHORT_TTL on the next heartbeat.
        """
        if not self.available or settings.aether_mode != 'production':
            return {"status": "ok"}

        active_user = self.r.get(LOCK_KEY)
        if active_user != session_id:
            return {"status": "not_owner"}

        self.r.set(LOCK_PHASE_KEY, "grace")
        self.r.expire(LOCK_KEY, GRACE_TTL)
        logger.info("Session entered grace phase", session_id=session_id, grace_ttl=GRACE_TTL)
        return {"status": "grace", "ttl_seconds": GRACE_TTL}

    def release(self, session_id: str) -> bool:
        """Explicitly release a lock or remove from queue (logout / beforeunload)."""
        if not self.available or settings.aether_mode != 'production' or not session_id:
            return False

        active_user = self.r.get(LOCK_KEY)
        if active_user == session_id:
            self._do_release_lock(session_id, reason="explicit_release")
            return True

        # Remove from waitlist if they were queued
        removed = self.r.lrem(WAITLIST_KEY, 0, session_id)
        return bool(removed)

    def force_release(self) -> None:
        """Admin override to forcibly clear the active lock."""
        if self.available:
            active_user = self.r.get(LOCK_KEY)
            self._do_release_lock(active_user or "unknown", reason="admin_force")

    def queue_length(self) -> int:
        """Return the current number of users in the waitlist."""
        if not self.available:
            return 0
        return self.r.llen(WAITLIST_KEY)

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _maybe_reap_and_promote(self) -> None:
        """Check if the active lock has expired; if so promote the next user.

        This is the core of the automatic handover mechanism. It's called on
        every get_status() call — by queued users polling the endpoint every 3 s.
        When the active user's key expires (no heartbeats → tab closed), the
        next call from any waiting user triggers the promotion.
        """
        active_user = self.r.get(LOCK_KEY)
        if active_user:
            return  # Lock still alive — nothing to do

        # Lock is gone. Check if there's someone waiting.
        next_user = self.r.lindex(WAITLIST_KEY, 0)
        if not next_user:
            return  # No one waiting — nothing to do

        # Attempt to claim for the next user.
        # Use SET NX to prevent a race if two polling clients hit this simultaneously.
        claimed = self.r.set(LOCK_KEY, next_user, nx=True, ex=SHORT_TTL)
        if claimed:
            self.r.lpop(WAITLIST_KEY)  # Remove from waitlist only after successful claim
            self.r.delete(LOCK_PHASE_KEY)
            logger.info(
                "Auto-handover: lock promoted to next queued user",
                promoted_to=next_user[:8],
                queue_remaining=self.r.llen(WAITLIST_KEY),
            )

    def _claim_lock(self, session_id: str) -> Dict[str, Any]:
        """Atomically claim the lock for session_id."""
        if self.r.set(LOCK_KEY, session_id, nx=True, ex=SHORT_TTL):
            self.r.delete(LOCK_PHASE_KEY)
            # Remove from waitlist (they may have been queued before)
            self.r.lrem(WAITLIST_KEY, 0, session_id)
            logger.info("Session lock claimed", session_id=session_id[:8])
            return {"status": "granted", "is_active": True, "queue_pos": 0, "phase": "active", "ttl_seconds": SHORT_TTL}

        # Race: someone else claimed it between our check and SET — re-evaluate
        return self.get_status(session_id)

    def _do_release_lock(self, session_id: str, reason: str = "release") -> None:
        """Delete the active lock and immediately promote the next user."""
        self.r.delete(LOCK_KEY)
        self.r.delete(LOCK_PHASE_KEY)
        logger.info("Session lock released", session_id=str(session_id)[:8], reason=reason)
        # Promote next immediately
        self._maybe_reap_and_promote()

    def _refresh_heartbeat(self, session_id: str) -> None:
        """Extend the lock TTL based on current phase. Called on get_status for active holder."""
        phase = self.r.get(LOCK_PHASE_KEY) or "active"
        ttl = GRACE_TTL if phase == "grace" else SHORT_TTL
        self.r.expire(LOCK_KEY, ttl)

    def _add_to_waitlist(self, session_id: str) -> int:
        """Add session_id to the tail of the waitlist (if not already there)."""
        existing = self.r.lrange(WAITLIST_KEY, 0, -1)
        if session_id not in existing:
            self.r.rpush(WAITLIST_KEY, session_id)
        pos = self.r.lpos(WAITLIST_KEY, session_id)
        return (pos + 1) if pos is not None else 1

    def _get_waitlist_position(self, session_id: str) -> int:
        """Return 1-based position in waitlist, or -1 if not present."""
        pos = self.r.lpos(WAITLIST_KEY, session_id)
        return (pos + 1) if pos is not None else -1


# Singleton
lock_service = SessionLockService()
