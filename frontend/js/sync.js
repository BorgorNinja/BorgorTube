/**
 * BorgorTube – Sync module  (Phase 3)
 *
 * Listens to real-time mpv property events from the Deno bridge and
 * mirrors them into the browser's <video> element (and vice versa).
 *
 * Sync rules:
 *  - time-pos:  if drift between mpv and browser > DRIFT_THRESHOLD, seek browser
 *  - pause:     mirror mpv pause state in browser (with loop-guard)
 *  - volume:    keep browser volume in sync with mpv
 *  - media-title: update page title
 *  - mpv_ended: show "MPV playback ended" toast, clear MPV status badge
 *
 * Anti-feedback guards prevent seek/pause events triggered BY sync
 * from bouncing back to mpv.
 */

window.BorgorSync = (() => {
  const DRIFT_THRESHOLD = 2.5;   // seconds before we force a seek
  const SYNC_COOLDOWN   = 1500;  // ms to ignore browser events after a sync action

  let $video = null;
  let enabled = true;
  let lastSyncAt = 0;            // timestamp of last sync action
  let syncGuard = false;         // true while we're programmatically seeking/pausing

  // ── Internal helpers ─────────────────────────────────────────────────

  function isCoolingDown() {
    return Date.now() - lastSyncAt < SYNC_COOLDOWN;
  }

  function markSync() {
    lastSyncAt = Date.now();
  }

  function guardedAction(fn) {
    syncGuard = true;
    markSync();
    try { fn(); }
    finally { setTimeout(() => { syncGuard = false; }, SYNC_COOLDOWN); }
  }

  // ── Handle mpv property changes ──────────────────────────────────────

  function onMpvProperty(name, value) {
    if (!enabled || !$video) return;

    switch (name) {
      case "time-pos": {
        if (value == null) return;
        const mpvTime = parseFloat(value);
        if (isNaN(mpvTime)) return;
        const browserTime = $video.currentTime;
        const drift = Math.abs(mpvTime - browserTime);
        // Only sync if HLS/direct stream is also playing and drift is significant
        if (drift > DRIFT_THRESHOLD && !$video.paused && !isCoolingDown()) {
          console.log(`[Sync] drift ${drift.toFixed(1)}s → seeking browser to ${mpvTime.toFixed(1)}s`);
          guardedAction(() => { $video.currentTime = mpvTime; });
          showSyncIndicator(`Synced to ${formatTime(mpvTime)}`);
        }
        break;
      }

      case "pause": {
        if (value == null) return;
        const mpvPaused = Boolean(value);
        if (syncGuard) return;
        if (mpvPaused && !$video.paused) {
          guardedAction(() => $video.pause());
        } else if (!mpvPaused && $video.paused) {
          guardedAction(() => $video.play().catch(() => {}));
        }
        break;
      }

      case "volume": {
        if (value == null) return;
        const vol = parseFloat(value) / 100;
        if (!isNaN(vol) && Math.abs($video.volume - vol) > 0.02) {
          guardedAction(() => { $video.volume = Math.max(0, Math.min(1, vol)); });
        }
        break;
      }

      case "media-title": {
        if (value && typeof value === "string") {
          document.title = `${value} – BorgorTube`;
        }
        break;
      }
    }
  }

  // ── Handle mpv lifecycle events ──────────────────────────────────────

  function onMpvEvent(msg) {
    if (msg.type === "mpv_ended") {
      showToast("MPV playback ended");
      // Update the status badge
      const dot = document.getElementById("mpv-dot");
      const txt = document.getElementById("mpv-status-text");
      if (dot) dot.className = "mpv-dot";
      if (txt) txt.textContent = "MPV not running";
    } else if (msg.type === "mpv_idle") {
      // mpv went idle (no more videos in queue)
      const txt = document.getElementById("mpv-status-text");
      if (txt) txt.textContent = "MPV idle";
    }
  }

  // ── Mirror browser events → mpv ──────────────────────────────────────

  function attachBrowserToMpv() {
    if (!$video) return;

    // Browser seek → mpv (only if mpv is running and we're not in a sync cooldown)
    $video.addEventListener("seeked", () => {
      if (syncGuard || !MPVBridge.isConnected()) return;
      MPVBridge.ipc(["set_property", "time-pos", $video.currentTime]);
    });

    // Browser pause/play → mpv
    $video.addEventListener("pause", () => {
      if (syncGuard || !MPVBridge.isConnected()) return;
      MPVBridge.ipc(["set_property", "pause", true]);
    });

    $video.addEventListener("play", () => {
      if (syncGuard || !MPVBridge.isConnected()) return;
      MPVBridge.ipc(["set_property", "pause", false]);
    });

    // Browser volume → mpv
    $video.addEventListener("volumechange", () => {
      if (syncGuard || !MPVBridge.isConnected()) return;
      MPVBridge.ipc(["set_property", "volume", Math.round($video.volume * 100)]);
    });
  }

  // ── Sync indicator ───────────────────────────────────────────────────

  let indicatorTimer = null;
  function showSyncIndicator(text) {
    let el = document.getElementById("sync-indicator");
    if (!el) {
      el = document.createElement("div");
      el.id = "sync-indicator";
      el.className = "sync-indicator";
      const wrapper = document.getElementById("player-wrapper");
      if (wrapper) wrapper.appendChild(el);
    }
    el.textContent = "⟳ " + text;
    el.classList.add("visible");
    clearTimeout(indicatorTimer);
    indicatorTimer = setTimeout(() => el.classList.remove("visible"), 2500);
  }

  function formatTime(s) {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  // ── Public API ────────────────────────────────────────────────────────

  function init() {
    $video = document.getElementById("video-player");
    if (!$video) return;

    // Listen to Deno bridge events
    MPVBridge.on("message", (msg) => {
      if (!enabled) return;
      if (msg.type === "property") {
        onMpvProperty(msg.name, msg.value);
      } else {
        onMpvEvent(msg);
      }
    });

    attachBrowserToMpv();

    // Keyboard shortcuts
    document.addEventListener("keydown", onKeyDown);

    console.log("[BorgorSync] initialized");
  }

  // ── Keyboard shortcuts ────────────────────────────────────────────────
  function onKeyDown(e) {
    // Don't intercept when typing in inputs
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

    const pageWatch = document.getElementById("page-watch");
    if (!pageWatch || pageWatch.hidden) return;

    switch (e.key) {
      case "k":
      case " ": {
        e.preventDefault();
        if ($video) {
          if ($video.paused) $video.play().catch(() => {});
          else $video.pause();
        }
        break;
      }
      case "j":
      case "ArrowLeft": {
        e.preventDefault();
        if ($video) $video.currentTime = Math.max(0, $video.currentTime - 10);
        break;
      }
      case "l":
      case "ArrowRight": {
        e.preventDefault();
        if ($video) $video.currentTime = $video.currentTime + 10;
        break;
      }
      case "ArrowUp": {
        e.preventDefault();
        if ($video) $video.volume = Math.min(1, $video.volume + 0.1);
        break;
      }
      case "ArrowDown": {
        e.preventDefault();
        if ($video) $video.volume = Math.max(0, $video.volume - 0.1);
        break;
      }
      case "m": {
        if ($video) $video.muted = !$video.muted;
        break;
      }
      case "f": {
        if ($video) {
          if (document.fullscreenElement) document.exitFullscreen();
          else document.getElementById("player-wrapper")?.requestFullscreen().catch(() => {});
        }
        break;
      }
      case "?": {
        showToast("k/Space: play·pause  j/←: −10s  l/→: +10s  ↑↓: volume  m: mute  f: fullscreen");
        break;
      }
    }
  }

  return {
    init,
    enable:  () => { enabled = true;  },
    disable: () => { enabled = false; },
    isEnabled: () => enabled,
  };
})();
