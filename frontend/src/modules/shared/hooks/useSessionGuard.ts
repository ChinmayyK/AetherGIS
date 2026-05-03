/**
 * useSessionGuard — manages the full session lifecycle for an active holder.
 *
 * RESPONSIBILITIES:
 * ─────────────────
 * 1. HEARTBEAT (every 15 s):
 *    - Sends POST /system/session/heartbeat to keep the lock alive.
 *    - Pauses when the tab is hidden (Page Visibility API) so idle background
 *      tabs don't hold the lock indefinitely.
 *    - Resumes on tab focus restore.
 *
 * 2. GRACE PHASE:
 *    - Watches jobStatus in the store. When a pipeline completes, automatically
 *      calls POST /system/session/start_grace to switch to the 5-min export TTL.
 *    - Subsequent heartbeats in grace phase use the longer TTL (300 s).
 *
 * 3. IDLE TIMEOUT WARNING:
 *    - After IDLE_WARN_MS of no user interaction (mouse/keyboard/scroll),
 *      shows a countdown dialog.
 *    - If the user doesn't respond within IDLE_WARN_TIMEOUT_MS, automatically
 *      releases the session (calls release + redirects to logout).
 *    - In grace phase, idle timeout is extended (users are downloading/viewing).
 *
 * 4. RELEASE ON UNMOUNT / BEFOREUNLOAD:
 *    - On component unmount (React hot-reload / SPA navigation): calls release.
 *    - On window beforeunload (tab close / browser close): uses navigator.sendBeacon
 *      for a best-effort fire-and-forget release. sendBeacon is the only reliable
 *      way to send a request in beforeunload across browsers.
 *
 * USAGE:
 *   Call inside the component that renders after SessionGate grants access.
 *   Pass sessionId and queueEnabled flag from SessionGate context.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { releaseSessionLock, sendSessionHeartbeat, startGraceSession } from '@shared/api/client';
import { useStore } from '@app/store/useStore';
import { getLogoutUrl } from '@shared/api/client';

// ─── Timing constants ─────────────────────────────────────────────────────────
const HEARTBEAT_INTERVAL_MS  = 15_000;  // 15 s — must be < SHORT_TTL (45 s)
const IDLE_WARN_MS_ACTIVE    = 3 * 60 * 1000;   // 3 min no interaction → warn (active phase)
const IDLE_WARN_MS_GRACE     = 4 * 60 * 1000;   // 4 min no interaction → warn (grace phase)
const IDLE_RELEASE_MS        = 60_000;           // 60 s after warn → auto-release

export type SessionPhase = 'active' | 'grace';

export interface SessionGuardState {
  /** Current phase of the session. */
  phase: SessionPhase;
  /** Seconds remaining in the idle countdown (null = not in countdown). */
  idleCountdownSec: number | null;
  /** Call this to dismiss the idle warning and reset the idle timer. */
  resetIdle: () => void;
}

export function useSessionGuard(
  sessionId: string,
  queueEnabled: boolean,
): SessionGuardState {
  const jobStatus    = useStore((s) => s.jobStatus);
  const [phase, setPhase]              = useState<SessionPhase>('active');
  const [idleCountdownSec, setIdleCountdownSec] = useState<number | null>(null);

  const phaseRef              = useRef<SessionPhase>('active');
  const lastActivityRef       = useRef<number>(0);
  const idleWarnTriggeredRef  = useRef(false);
  const idleCountdownRef      = useRef<number | null>(null);
  const releasedRef           = useRef(false);

  // Initialize lastActivityRef on mount
  useEffect(() => {
    lastActivityRef.current = Date.now();
  }, []);

  // ── Phase change: active → grace on pipeline completion ─────────────────
  useEffect(() => {
    if (!queueEnabled) return;
    if (jobStatus === 'completed' && phase === 'active') {
      // Defer state update to next tick to avoid "set-state-in-effect" warning
      setTimeout(() => setPhase('grace'), 0);
      phaseRef.current = 'grace';
      startGraceSession(sessionId).catch((e) =>
        console.warn('[SessionGuard] start_grace failed', e),
      );
    }
    // If pipeline fails/is reset, stay in current phase
  }, [jobStatus, phase, queueEnabled, sessionId]);

  // ── Heartbeat ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!queueEnabled || !sessionId) return;

    const sendBeat = () => {
      // Don't heartbeat if tab is hidden (saves lock for active users)
      if (document.visibilityState === 'hidden') return;
      sendSessionHeartbeat(sessionId, phaseRef.current).catch((err) =>
        console.warn('[SessionGuard] Heartbeat failed', err),
      );
    };

    sendBeat(); // immediate first beat
    const intervalId = window.setInterval(sendBeat, HEARTBEAT_INTERVAL_MS);

    // Resume heartbeats when tab becomes visible again
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        lastActivityRef.current = Date.now(); // treat tab focus as activity
        sendBeat();
      }
    };

    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [queueEnabled, sessionId]);

  // ── Release function (idempotent) ──────────────────────────────────────
  const doRelease = useCallback(
    (reason: 'idle_timeout' | 'unmount' | 'beforeunload') => {
      if (releasedRef.current) return;
      releasedRef.current = true;

      if (reason === 'beforeunload') {
        // navigator.sendBeacon is the only reliable fire-and-forget in unload
        const url = `/api/v1/system/session/release?session_id=${encodeURIComponent(sessionId)}`;
        navigator.sendBeacon(url, '');
      } else {
        releaseSessionLock(sessionId).catch(() => {});
      }

      if (reason === 'idle_timeout') {
        // Redirect to logout so the Google OAuth session is also cleared
        window.location.assign(getLogoutUrl('/'));
      }
    },
    [sessionId],
  );

  // ── Idle detection ────────────────────────────────────────────────────
  useEffect(() => {
    if (!queueEnabled || !sessionId) return;

    const EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'];

    const resetActivity = () => {
      lastActivityRef.current = Date.now();
      if (idleWarnTriggeredRef.current) {
        // User interacted during countdown — cancel it
        idleWarnTriggeredRef.current = false;
        if (idleCountdownRef.current !== null) {
          window.clearInterval(idleCountdownRef.current);
          idleCountdownRef.current = null;
        }
        setIdleCountdownSec(null);
      }
    };

    EVENTS.forEach((e) => window.addEventListener(e, resetActivity, { passive: true }));

    const checkIdle = () => {
      if (idleWarnTriggeredRef.current) return; // already counting down

      const now      = Date.now();
      const idleMs   = now - lastActivityRef.current;
      const threshold = phaseRef.current === 'grace' ? IDLE_WARN_MS_GRACE : IDLE_WARN_MS_ACTIVE;

      if (idleMs >= threshold) {
        idleWarnTriggeredRef.current = true;
        let secs = Math.floor(IDLE_RELEASE_MS / 1000);
        setIdleCountdownSec(secs);

        idleCountdownRef.current = window.setInterval(() => {
          secs -= 1;
          setIdleCountdownSec(secs);
          if (secs <= 0) {
            window.clearInterval(idleCountdownRef.current!);
            idleCountdownRef.current = null;
            doRelease('idle_timeout');
          }
        }, 1000);
      }
    };

    const idleCheckInterval = window.setInterval(checkIdle, 10_000); // check every 10 s

    return () => {
      EVENTS.forEach((e) => window.removeEventListener(e, resetActivity));
      window.clearInterval(idleCheckInterval);
      if (idleCountdownRef.current !== null) {
        window.clearInterval(idleCountdownRef.current);
      }
    };
  }, [queueEnabled, sessionId, doRelease]);

  // ── Release on unmount ───────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (queueEnabled) doRelease('unmount');
    };
  }, [queueEnabled, doRelease]);

  // ── Release on tab/window close (beforeunload) ────────────────────────
  useEffect(() => {
    if (!queueEnabled) return;

    const onBeforeUnload = () => doRelease('beforeunload');
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [queueEnabled, doRelease]);

  const resetIdle = useCallback(() => {
    lastActivityRef.current = Date.now();
    idleWarnTriggeredRef.current = false;
    if (idleCountdownRef.current !== null) {
      window.clearInterval(idleCountdownRef.current);
      idleCountdownRef.current = null;
    }
    setIdleCountdownSec(null);
  }, []);

  return { phase, idleCountdownSec, resetIdle };
}
