/**
 * useFramePreloader — aggressive blob-based frame cache for smooth production playback.
 *
 * THE PRODUCTION STUTTER PROBLEM:
 *   In production, each frame is fetched from `/api/v1/pipeline/<id>/frames/<n>`.
 *   OpenLayers creates a new XHR for every `new Static({url: ...})` call, regardless
 *   of browser HTTP cache headers. On a remote OCI host, this round-trip latency
 *   causes visible stutter during playback even at 1 fps.
 *
 * THE FIX — Blob URL caching:
 *   1. We fetch each frame as a Blob (binary) using the Fetch API.
 *   2. We create a persistent blob URL via `URL.createObjectURL()`.
 *   3. We expose a `getBlobUrl(frameIndex)` function that MapViewer calls.
 *   4. MapViewer passes the blob URL to OL's ImageStatic — no network round-trip,
 *      just an in-memory lookup → zero-latency frame switches.
 *   5. On job change or unmount, we revoke all blob URLs to free memory.
 *
 * MEMORY SAFETY:
 *   - We maintain a sliding window: [current - BEHIND, current + AHEAD].
 *   - Frames outside the window are revoked and evicted.
 *   - Concurrent fetches are de-duped via a `fetching` Set.
 *   - Aborted/failed fetches log warnings and don't crash the playback loop.
 */
import { useEffect, useRef, useCallback } from 'react';

const CACHE_NAME = 'aethergis-frames-v1';
const AHEAD_BASE = 24;
const BEHIND_BASE = 12;
const MAX_CONCURRENT_FETCHES = 8;
const supportsPersistentCache = typeof window !== 'undefined' && 'caches' in window;

function getWindowSize(playbackSpeed: number) {
  const speedFactor = playbackSpeed >= 4 ? 1.8 : playbackSpeed >= 2 ? 1.35 : playbackSpeed <= 0.5 ? 0.9 : 1;
  return {
    ahead: Math.max(10, Math.round(AHEAD_BASE * speedFactor)),
    behind: Math.max(4, Math.round(BEHIND_BASE * speedFactor)),
  };
}

export function useFramePreloader(
  jobId: string | null,
  totalFrames: number,
  currentFrameIndex: number,
  playbackSpeed: 0.5 | 1 | 2 | 4 = 1,
  jobStatus?: string,
) {
  // Map<frameIndex, blobUrl>
  const cacheRef = useRef(new Map<number, string>());
  // Set of frame indices currently being fetched (de-duplication)
  const fetchingRef = useRef(new Set<number>());
  // AbortController per-frame to cancel in-flight requests on eviction
  const abortControllersRef = useRef(new Map<number, AbortController>());
  // Track the last jobId so we can flush on job change
  const lastJobIdRef = useRef<string | null>(null);
  const inflightGlobalRef = useRef(0);

  /** Revoke and evict a single frame from cache */
  const evict = useCallback((idx: number) => {
    const blobUrl = cacheRef.current.get(idx);
    if (blobUrl) {
      URL.revokeObjectURL(blobUrl);
      cacheRef.current.delete(idx);
    }
    // Abort any in-flight request
    abortControllersRef.current.get(idx)?.abort();
    abortControllersRef.current.delete(idx);
    fetchingRef.current.delete(idx);
  }, []);

  /** Flush all cached blobs (on job change or unmount) */
  const flushAll = useCallback(() => {
    for (const idx of Array.from(cacheRef.current.keys())) {
      evict(idx);
    }
    cacheRef.current.clear();
    fetchingRef.current.clear();
    abortControllersRef.current.clear();
  }, [evict]);

  const frameRequest = useCallback((activeJobId: string, idx: number) => {
    return new Request(`/api/v1/pipeline/${activeJobId}/frames/${idx}`);
  }, []);

  const trimPersistentCache = useCallback(async (activeJobId: string) => {
    if (!supportsPersistentCache) return;
    const cacheStore = await caches.open(CACHE_NAME);
    const keys = await cacheStore.keys();
    const activeNeedle = `/api/v1/pipeline/${activeJobId}/frames/`;
    await Promise.all(
      keys
        .filter((req) => req.url.includes('/api/v1/pipeline/') && !req.url.includes(activeNeedle))
        .map((req) => cacheStore.delete(req)),
    );
  }, []);

  /** Fetch a single frame as a blob and store its object URL */
  const prefetchFrame = useCallback(
    async (jobId: string, idx: number) => {
      if (cacheRef.current.has(idx) || fetchingRef.current.has(idx)) return;
      if (inflightGlobalRef.current >= MAX_CONCURRENT_FETCHES) return;

      fetchingRef.current.add(idx);
      inflightGlobalRef.current += 1;
      const controller = new AbortController();
      abortControllersRef.current.set(idx, controller);

      try {
        const request = frameRequest(jobId, idx);
        if (supportsPersistentCache) {
          const cacheStore = await caches.open(CACHE_NAME);
          const cachedResponse = await cacheStore.match(request);
          if (cachedResponse) {
            const cachedBlob = await cachedResponse.blob();
            if (fetchingRef.current.has(idx)) {
              const cachedBlobUrl = URL.createObjectURL(cachedBlob);
              cacheRef.current.set(idx, cachedBlobUrl);
            }
            return;
          }
        }

        const res = await fetch(request, {
          signal: controller.signal,
          cache: 'force-cache',
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (supportsPersistentCache) {
          const cacheStore = await caches.open(CACHE_NAME);
          await cacheStore.put(request, res.clone());
        }
        const blob = await res.blob();
        // Only store if we're still in the window (not evicted while fetching)
        if (fetchingRef.current.has(idx)) {
          const blobUrl = URL.createObjectURL(blob);
          cacheRef.current.set(idx, blobUrl);
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name !== 'AbortError') {
          console.warn(`[FramePreloader] Failed to prefetch frame ${idx}:`, err.message);
        }
      } finally {
        fetchingRef.current.delete(idx);
        abortControllersRef.current.delete(idx);
        inflightGlobalRef.current = Math.max(0, inflightGlobalRef.current - 1);
      }
    },
    [frameRequest],
  );

  /** Background sync: fetch ALL frames into persistent cache when job is COMPLETED */
  useEffect(() => {
    if (!jobId || !supportsPersistentCache || jobStatus !== 'completed') return;

    let isCancelled = false;
    const syncAll = async () => {
      const cacheStore = await caches.open(CACHE_NAME);
      // Low priority background sync
      for (let i = 0; i < totalFrames; i++) {
        if (isCancelled) break;
        const request = frameRequest(jobId, i);
        const match = await cacheStore.match(request);
        if (!match) {
          try {
            // Fetch and put in cache without blocking or storing in memory
            const res = await fetch(request, { cache: 'force-cache' });
            if (res.ok) await cacheStore.put(request, res);
            // Throttle background sync to avoid saturating connection
            await new Promise(r => setTimeout(r, 100)); 
          } catch {
            // Ignore background fetch errors
          }
        }
      }
    };

    syncAll();
    return () => { isCancelled = true; };
  }, [jobId, totalFrames, jobStatus, frameRequest]);

  useEffect(() => {
    if (!jobId || totalFrames === 0) return;

    // Flush cache on job change
    if (jobId !== lastJobIdRef.current) {
      flushAll();
      lastJobIdRef.current = jobId;
      trimPersistentCache(jobId).catch(() => {});
    }

    const { ahead, behind } = getWindowSize(playbackSpeed);
    const low = Math.max(0, currentFrameIndex - behind);
    const high = Math.min(totalFrames - 1, currentFrameIndex + ahead);

    // Evict frames outside the window
    for (const idx of Array.from(cacheRef.current.keys())) {
      if (idx < low || idx > high) evict(idx);
    }

    // Prefetch frames in window (prioritise ahead, then behind)
    for (let i = currentFrameIndex; i <= high; i++) prefetchFrame(jobId, i);
    for (let i = currentFrameIndex - 1; i >= low; i--) prefetchFrame(jobId, i);
  }, [jobId, totalFrames, currentFrameIndex, playbackSpeed, prefetchFrame, evict, flushAll, trimPersistentCache]);

  // Flush all blob URLs on unmount
  useEffect(() => {
    return () => flushAll();
  }, [flushAll]);

  /**
   * Returns the blob URL for a frame if it's already cached,
   * or the fallback API URL if the blob isn't ready yet.
   * MapViewer should call this every time it needs a frame URL.
   */
  const getFrameUrl = useCallback(
    (frameIndex: number): string => {
      const cached = cacheRef.current.get(frameIndex);
      if (cached) return cached;
      // Fallback: direct API URL (will work but may stutter)
      return jobId ? `/api/v1/pipeline/${jobId}/frames/${frameIndex}` : '';
    },
    [jobId],
  );

  return { getFrameUrl, cache: cacheRef.current };
}
