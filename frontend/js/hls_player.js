/**
 * BorgorTube – HLS Player module  (Phase 2)
 *
 * Manages an HLS transcoding session on the backend (ffmpeg) and
 * plays the resulting stream in the browser <video> via hls.js.
 *
 * Flow:
 *   1. POST /api/hls/start  → session_id + playlist_url
 *   2. hls.js loads /hls/{session_id}/index.m3u8
 *   3. browser plays natively while ffmpeg fills segments
 *   4. DELETE /api/hls/{session_id} on stop / new video
 *
 * Falls back to direct URL if:
 *   - ffmpeg not available on server
 *   - HLS session fails to become ready in time
 *   - hls.js fails to attach
 */

window.BorgorHLS = (() => {
  let hlsInstance = null;       // hls.js Hls object
  let activeSessionId = null;   // current backend session ID
  let statusPollTimer = null;

  // ── Load hls.js dynamically (only once) ─────────────────────────────

  let hlsJsReady = typeof Hls !== "undefined";
  let hlsJsLoadPromise = null;

  function ensureHlsJs() {
    if (hlsJsReady) return Promise.resolve();
    if (hlsJsLoadPromise) return hlsJsLoadPromise;

    hlsJsLoadPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js";
      script.onload = () => { hlsJsReady = true; resolve(); };
      script.onerror = () => reject(new Error("Failed to load hls.js"));
      document.head.appendChild(script);
    });
    return hlsJsLoadPromise;
  }

  // ── Tear down any existing HLS session ───────────────────────────────

  async function stopCurrent() {
    clearInterval(statusPollTimer);
    statusPollTimer = null;

    if (hlsInstance) {
      hlsInstance.destroy();
      hlsInstance = null;
    }

    if (activeSessionId) {
      try {
        await BorgorAPI.hlsStop(activeSessionId);
      } catch { /* ignore */ }
      activeSessionId = null;
    }
  }

  // ── Start a new HLS session and attach to <video> ────────────────────

  /** Poll /api/hls/{id}/status until ready or timeout. */
  async function _pollReady(sessionId, timeoutMs = 25000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        const s = await BorgorAPI.hlsStatus(sessionId);
        if (s.state === "error") return false;
        if (s.ready) return true;
      } catch { /* ignore poll errors */ }
      await new Promise(r => setTimeout(r, 400));
    }
    return false;
  }

  /**
   * @param {string} videoUrl - original YouTube URL
   * @param {string} quality  - quality label e.g. "1080p"
   * @param {HTMLVideoElement} videoEl
   * @param {boolean} lowLatency
   * @returns {Promise<{ok: boolean, sessionId: string|null, fallbackUrl: string|null}>}
   */
  async function load(videoUrl, quality, videoEl, lowLatency = false) {
    await stopCurrent();

    // 1. Ask backend to start ffmpeg HLS session
    let session;
    try {
      // Returns immediately — ffmpeg launches in the background on the server.
      // We poll /status until ready=true instead of blocking the request.
      session = await BorgorAPI.hlsStart({ url: videoUrl, quality, low_latency: lowLatency });
    } catch (e) {
      console.warn("[HLS] backend session start failed:", e.message);
      return { ok: false, sessionId: null, fallbackUrl: null };
    }

    activeSessionId = session.session_id;

    // Poll for first segment — server responds in <5ms so this loop
    // runs client-side while the server prepares segments in parallel.
    const ready = await _pollReady(session.session_id, 25000);
    if (!ready) {
      console.warn("[HLS] session not ready in time");
      await BorgorAPI.hlsStop(session.session_id).catch(() => {});
      activeSessionId = null;
      return { ok: false, sessionId: null, fallbackUrl: null };
    }
    const playlistUrl = session.playlist_url; // e.g. /hls/abc12345/index.m3u8

    // 2. Attach via hls.js (or native HLS)
    try {
      await ensureHlsJs();
    } catch {
      // hls.js failed to load – try native
      videoEl.src = playlistUrl;
      videoEl.load();
      return { ok: true, sessionId: activeSessionId, fallbackUrl: null };
    }

    if (Hls.isSupported()) {
      hlsInstance = new Hls({
        enableWorker: true,
        lowLatencyMode: lowLatency,
        backBufferLength: lowLatency ? 5 : 30,
        maxBufferLength: lowLatency ? 10 : 60,
        // Retry params
        manifestLoadingMaxRetry: 5,
        manifestLoadingRetryDelay: 1000,
        levelLoadingMaxRetry: 5,
        fragLoadingMaxRetry: 5,
      });

      hlsInstance.loadSource(playlistUrl);
      hlsInstance.attachMedia(videoEl);

      hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
        // Clear any loading overlay immediately when manifest is ready
        const overlay = document.getElementById("player-overlay");
        if (overlay) overlay.hidden = true;
        videoEl.play().catch(() => {});
      });

      hlsInstance.on(Hls.Events.FRAG_LOADED, () => {
        // Also clear overlay on first fragment load
        const overlay = document.getElementById("player-overlay");
        if (overlay) overlay.hidden = true;
      });

      hlsInstance.on(Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              hlsInstance.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              hlsInstance.recoverMediaError();
              break;
            default:
              console.error("[HLS] fatal error, stopping:", data);
              hlsInstance.destroy();
              hlsInstance = null;
              showToast("HLS error – falling back to direct stream");
              break;
          }
        }
      });
    } else if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
      // Safari native HLS
      videoEl.src = playlistUrl;
      videoEl.load();
      videoEl.play().catch(() => {});
    } else {
      showToast("HLS not supported in this browser");
      return { ok: false, sessionId: activeSessionId, fallbackUrl: null };
    }

    // 3. Start polling session status
    startStatusPoll(session.session_id);

    return { ok: true, sessionId: session.session_id, fallbackUrl: null };
  }

  // ── Poll session status (used for the MPV status badge) ──────────────

  function startStatusPoll(sessionId) {
    clearInterval(statusPollTimer);
    statusPollTimer = setInterval(async () => {
      try {
        const s = await BorgorAPI.hlsStatus(sessionId);
        if (!s.running) {
          clearInterval(statusPollTimer);
          showToast("HLS stream ended");
        }
        // Emit event for other modules
        window.dispatchEvent(new CustomEvent("borgortube:hls-status", { detail: s }));
      } catch {
        clearInterval(statusPollTimer);
      }
    }, 3000);
  }

  // ── Quality change ───────────────────────────────────────────────────

  async function changeQuality(videoUrl, newQuality, videoEl, lowLatency) {
    // Capture position before stopping — load() calls stop() which resets video
    const currentTime = videoEl ? videoEl.currentTime : 0;

    // Stop current session and start a new one for the new quality.
    // Each quality needs its own ffmpeg session with the right FORMAT_MAPPING.
    const result = await load(videoUrl, newQuality, videoEl, lowLatency);

    // Seek to previous position once the new stream is ready
    if (result.ok && videoEl && currentTime > 2) {
      const seekOnce = () => {
        videoEl.currentTime = currentTime;
        videoEl.removeEventListener("canplay", seekOnce);
      };
      videoEl.addEventListener("canplay", seekOnce);
    }
    return result;
  }

  // ── Public API ───────────────────────────────────────────────────────

  return {
    load,
    changeQuality,
    stop: stopCurrent,
    getSessionId: () => activeSessionId,
    isActive: () => activeSessionId !== null && hlsInstance !== null,
  };
})();


// ── Extend BorgorAPI with HLS methods ────────────────────────────────────
// (added here so hls_player.js is self-contained)
Object.assign(window.BorgorAPI || {}, {
  async hlsStart({ url, quality = "720p", low_latency = false }) {
    const API_BASE = window.location.origin.includes("localhost") || window.location.origin.includes("127.0.0.1")
      ? "http://localhost:8000" : window.location.origin;
    const res = await fetch(`${API_BASE}/api/hls/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, quality, low_latency }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async hlsStatus(sessionId) {
    const API_BASE = window.location.origin.includes("localhost") || window.location.origin.includes("127.0.0.1")
      ? "http://localhost:8000" : window.location.origin;
    const res = await fetch(`${API_BASE}/api/hls/${sessionId}/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async hlsStop(sessionId) {
    const API_BASE = window.location.origin.includes("localhost") || window.location.origin.includes("127.0.0.1")
      ? "http://localhost:8000" : window.location.origin;
    await fetch(`${API_BASE}/api/hls/${sessionId}`, { method: "DELETE" });
  },
});
