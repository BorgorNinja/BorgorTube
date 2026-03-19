/**
 * BorgorTube – Player module
 * Controls the HTML5 <video> element and bridges to MPV for pop-out.
 */

window.BorgorPlayer = (() => {
  // DOM refs (populated on init)
  let $video, $wrapper, $overlay, $qualitySelect, $mpvStatus, $mpvDot, $mpvStatusText;
  let $btnMpvPop, $btnMpvKill, $btnPip, $chkLowLatency, $mpvStatusBar;

  // State
  let currentInfo = null;      // full API response from /api/video
  let currentUrl = null;
  let commentScrollCount = 1;
  let mpvPollInterval = null;

  // ── Helpers ────────────────────────────────────────────────────────────

  function formatDuration(seconds) {
    if (!seconds || isNaN(seconds)) return "";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function formatViews(n) {
    if (!n) return "";
    if (n >= 1e9) return (n / 1e9).toFixed(1) + "B views";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M views";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "K views";
    return n + " views";
  }

  // ── Best stream URL selection ─────────────────────────────────────────

  /**
   * Given the info object and a quality label, find the best direct stream URL.
   * Priority: matching video+audio merged, then best manifest_url, then best_url.
   *
   * NOTE: YouTube streams are usually split (video-only + audio-only).
   * Native <video> can only play merged streams or HLS manifests.
   * For split streams, we fall back to the best_url from yt-dlp which is
   * typically a merged/progressive fallback at lower quality.
   * Full adaptive streaming via MPV is handled in the pop-out path.
   */
  function pickStreamUrl(info, qualityLabel) {
    const formats = info.formats || [];

    // 1. Look for a merged (has both video + audio) format matching quality
    const targetH = qualityLabel
      ? parseInt(qualityLabel.replace("p", "").replace("k", "440").replace("60", ""))
      : 360;

    const merged = formats.filter(
      (f) =>
        f.acodec !== "none" &&
        f.vcodec !== "none" &&
        f.height >= targetH
    );
    if (merged.length > 0) {
      merged.sort((a, b) => (b.tbr || 0) - (a.tbr || 0));
      return merged[0].url;
    }

    // 2. HLS manifest URL
    if (info.best_url && (info.best_url.includes(".m3u8") || info.best_url.includes("manifest"))) {
      return info.best_url;
    }

    // 3. Fallback: the best progressive URL yt-dlp resolved
    if (info.best_url) return info.best_url;

    // 4. Any URL from formats
    const anyFormat = formats.find((f) => f.url);
    return anyFormat ? anyFormat.url : null;
  }

  // ── Browser <video> playback ──────────────────────────────────────────

  function loadInBrowser(info, qualityLabel) {
    if (!$video) return;

    const streamUrl = pickStreamUrl(info, qualityLabel);
    if (!streamUrl) {
      showToast("No direct stream URL found – try MPV pop-out");
      return;
    }

    $video.src = streamUrl;
    $video.load();
    $video.play().catch((e) => {
      // Autoplay blocked – user can click play manually
      console.warn("[Player] autoplay blocked:", e.message);
    });
  }

  // ── Quality switching ─────────────────────────────────────────────────

  async function onQualityChange() {
    if (!currentInfo) return;
    const q = $qualitySelect.value;
    const ll = $chkLowLatency ? $chkLowLatency.checked : false;

    // If HLS is active, switch quality via HLS session
    if (window.BorgorHLS && BorgorHLS.isActive() && $video) {
      setPlayerMode("hls-loading");
      const result = await BorgorHLS.changeQuality(currentUrl, q, $video, ll);
      if (result.ok) { setPlayerMode("hls"); }
      else           { setPlayerMode("direct"); loadInBrowser(currentInfo, q); }
    } else {
      loadInBrowser(currentInfo, q);
    }

    // If MPV pop-out is running, restart it at same position with new quality
    BorgorAPI.mpvStatus().then((status) => {
      if (status.running && currentUrl) {
        BorgorAPI.mpvLaunch({
          url: currentUrl, quality: q,
          start_time: status.time_pos || 0,
          detached: true, low_latency: ll,
        });
      }
    }).catch(() => {});
  }

  // ── MPV pop-out ───────────────────────────────────────────────────────

  function launchMpv() {
    if (!currentUrl) { showToast("No video loaded"); return; }
    const q = $qualitySelect.value;
    const t = $video ? $video.currentTime : 0;

    BorgorAPI.mpvLaunch({
      url: currentUrl,
      quality: q,
      start_time: t,
      detached: true,
      low_latency: $chkLowLatency.checked,
    })
      .then(() => {
        showToast(`MPV launched at ${q}`);
        startMpvPoll();
      })
      .catch((e) => showToast("MPV error: " + e.message));
  }

  function killMpv() {
    BorgorAPI.mpvKill()
      .then(() => { showToast("MPV stopped"); updateMpvBadge(false); })
      .catch((e) => showToast("Kill error: " + e.message));
  }

  // ── MPV status polling ────────────────────────────────────────────────

  function startMpvPoll() {
    if (mpvPollInterval) return;
    mpvPollInterval = setInterval(refreshMpvStatus, 1500);
  }

  function stopMpvPoll() {
    clearInterval(mpvPollInterval);
    mpvPollInterval = null;
  }

  async function refreshMpvStatus() {
    try {
      const s = await BorgorAPI.mpvStatus();
      updateMpvBadge(s.running, s);
      if (!s.running) stopMpvPoll();
    } catch { /* ignore */ }
  }

  function updateMpvBadge(running, data = {}) {
    if (!$mpvStatusBar) return;
    $mpvStatusBar.hidden = false;
    $mpvDot.className = "mpv-dot" + (running ? " running" : "");
    if (running && data.time_pos != null) {
      const t = formatDuration(data.time_pos);
      const dur = data.duration ? " / " + formatDuration(data.duration) : "";
      $mpvStatusText.textContent = `MPV running · ${t}${dur}`;
    } else {
      $mpvStatusText.textContent = running ? "MPV running" : "MPV not running";
    }
  }

  // ── Picture-in-Picture ────────────────────────────────────────────────

  function togglePip() {
    if (!$video) return;
    if (document.pictureInPictureElement) {
      document.exitPictureInPicture().catch(() => {});
    } else if (document.pictureInPictureEnabled) {
      $video.requestPictureInPicture().catch((e) =>
        showToast("PiP not available: " + e.message)
      );
    } else {
      showToast("Picture-in-picture not supported");
    }
  }

  // ── Load a video (called from app.js) ────────────────────────────────────────

  async function load(url) {
    currentUrl = url;
    currentInfo = null;

    // Stop any existing HLS session
    if (window.BorgorHLS) await BorgorHLS.stop();

    // Show overlay while loading
    if ($overlay) $overlay.hidden = false;

    try {
      const info = await BorgorAPI.getVideo(url);
      currentInfo = info;

      // Populate quality selector
      $qualitySelect.innerHTML = "";
      for (const q of info.qualities || ["360p"]) {
        const opt = document.createElement("option");
        opt.value = q;
        opt.textContent = q;
        $qualitySelect.appendChild(opt);
      }

      // Try HLS (any quality in browser); fall back to direct URL
      const useQuality = info.qualities?.[0] || "360p";
      await loadWithHLSFallback(info, useQuality);

      // Hide overlay
      if ($overlay) $overlay.hidden = true;

      return info;
    } catch (e) {
      if ($overlay) $overlay.hidden = true;
      throw e;
    }
  }

  /**
   * Attempt HLS (ffmpeg transcoding) first.
   * Falls back to direct stream URL if HLS unavailable.
   */
  async function loadWithHLSFallback(info, qualityLabel) {
    if (window.BorgorHLS && $video) {
      try {
        setPlayerMode("hls-loading");
        const result = await BorgorHLS.load(
          currentUrl, qualityLabel, $video, $chkLowLatency?.checked || false
        );
        if (result.ok) {
          setPlayerMode("hls");
          // Overlay is cleared by hls.js MANIFEST_PARSED, but ensure it's
          // gone regardless after a short grace period.
          setTimeout(() => {
            if ($overlay) $overlay.hidden = true;
          }, 3000);
          return;
        }
      } catch (e) {
        console.warn("[Player] HLS load error:", e.message);
      }
    }
    setPlayerMode("direct");
    loadInBrowser(info, qualityLabel);
  }

  function setPlayerMode(mode) {
    const badge = document.getElementById("player-mode-badge");
    if (!badge) return;
    const modes = {
      "hls":         ["HLS",   "mode-badge mode-hls"],
      "hls-loading": ["HLS…", "mode-badge mode-loading"],
      "direct":      ["Direct","mode-badge mode-direct"],
    };
    const [text, cls] = modes[mode] || ["", ""];
    badge.textContent = text;
    badge.className = cls;
    badge.hidden = !text;
  }


  // ── Init ─────────────────────────────────────────────────────────────

  function init() {
    $video          = document.getElementById("video-player");
    $wrapper        = document.getElementById("player-wrapper");
    $overlay        = document.getElementById("player-overlay");
    $qualitySelect  = document.getElementById("quality-select");
    $mpvStatusBar   = document.getElementById("mpv-status");
    $mpvDot         = document.getElementById("mpv-dot");
    $mpvStatusText  = document.getElementById("mpv-status-text");
    $btnMpvPop      = document.getElementById("btn-mpv-pop");
    $btnMpvKill     = document.getElementById("btn-mpv-kill");
    $btnPip         = document.getElementById("btn-pip");
    $chkLowLatency  = document.getElementById("chk-low-latency");

    if ($btnMpvPop)    $btnMpvPop.addEventListener("click", launchMpv);
    if ($btnMpvKill)   $btnMpvKill.addEventListener("click", killMpv);
    if ($btnPip)       $btnPip.addEventListener("click", togglePip);
    if ($qualitySelect) $qualitySelect.addEventListener("change", onQualityChange);

    // Low-latency toggle restarts current stream
    if ($chkLowLatency) {
      $chkLowLatency.addEventListener("change", async () => {
        if (!currentInfo) return;
        const q = $qualitySelect.value;
        if (window.BorgorHLS && BorgorHLS.isActive() && $video) {
          setPlayerMode("hls-loading");
          const r = await BorgorHLS.load(currentUrl, q, $video, $chkLowLatency.checked);
          setPlayerMode(r.ok ? "hls" : "direct");
          if (!r.ok) loadInBrowser(currentInfo, q);
        }
      });
    }

    // Deno bridge status updates
    MPVBridge.on("status", (data) => updateMpvBadge(data.connected, data));
    MPVBridge.connect();

    // Video events
    if ($video) {
      $video.addEventListener("error", () => {
        showToast("Browser player failed to load stream – try MPV pop-out");
        if ($overlay) $overlay.hidden = true;
      });
      $video.addEventListener("waiting", () => {
        if ($overlay) $overlay.hidden = false;
      });
      $video.addEventListener("canplay", () => {
        if ($overlay) $overlay.hidden = true;
      });
    }
  }

  return { init, load, formatDuration, formatViews };
})();
