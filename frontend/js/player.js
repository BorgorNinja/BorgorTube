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
  /**
   * Returns a direct stream URL only for formats that don't need HLS.
   * Anything with hls_required=true must go through the HLS path.
   * Returns null to force HLS when no direct URL works for the quality.
   */
  function pickStreamUrl(info, qualityLabel) {
    const formats = info.formats || [];
    const targetH = qualityLabel
      ? parseInt(qualityLabel.replace("p60","").replace("p","").replace("k","440"))
      : 360;

    // Only use direct URL for progressive (merged) formats — not hls_required
    const direct = formats.filter(
      (f) => !f.hls_required && f.acodec !== "none" && f.vcodec !== "none" && f.url
    );

    // Find the direct format closest to target height (prefer at or below target)
    const exact = direct.filter((f) => (f.height || 0) >= targetH);
    if (exact.length > 0) {
      exact.sort((a, b) => (a.height || 0) - (b.height || 0));
      return exact[0].url;
    }

    // Any direct format as last resort
    if (direct.length > 0) {
      direct.sort((a, b) => (b.height || 0) - (a.height || 0));
      return direct[0].url;
    }

    // HLS manifest fallback
    if (info.best_url?.includes(".m3u8")) return info.best_url;

    // For anything requiring HLS, return null to trigger the HLS path
    return null;
  }

  /**
   * Returns true if the given quality requires HLS (ffmpeg transcode).
   * Anything above 360p on YouTube is split video+audio and needs HLS.
   */
  function qualityNeedsHLS(qualityLabel) {
    const noHLS = ["360p", "240p", "144p"];  // these have merged progressive streams
    return !noHLS.includes(qualityLabel);
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

    // Always restart HLS for quality changes — it needs a new ffmpeg session
    // with the right format string for the new quality
    await loadWithHLSFallback(currentInfo, q);

    // If MPV pop-out is running, restart it at same position with new quality
    BorgorAPI.mpvStatus().then((status) => {
      if (status.running && currentUrl) {
        BorgorAPI.mpvLaunch({
          url: currentUrl, quality: q,
          start_time: status.time_pos || 0,
          detached: true, low_latency: ll,
          with_browser_mirror: true,
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
      low_latency: $chkLowLatency?.checked || false,
      with_browser_mirror: true,
    })
      .then((res) => {
        showToast(`MPV launched at ${q}`);
        startMpvPoll();
        // If server started a mirror HLS stream, switch browser player to it
        if (res.mirror_active && res.mirror_playlist && $video) {
          showToast('Browser mirroring MPV output…', 4000);
          setPlayerMode('hls-loading');
          // Give mpv ~2s to start writing to the pipe before attaching
          setTimeout(async () => {
            if (window.BorgorHLS) await BorgorHLS.stop();
            if (window.Hls && Hls.isSupported()) {
              const hls = new Hls({ lowLatencyMode: true, backBufferLength: 5 });
              hls.loadSource(res.mirror_playlist);
              hls.attachMedia($video);
              hls.on(Hls.Events.MANIFEST_PARSED, () => {
                $video.play().catch(() => {});
                setPlayerMode('hls');
                const badge = document.getElementById('player-mode-badge');
                if (badge) { badge.textContent = 'MPV MIRROR'; badge.className = 'mode-badge mode-hls'; }
              });
            }
          }, 2000);
        }
      })
      .catch((e) => showToast('MPV error: ' + e.message));
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
    const forceHLS = qualityNeedsHLS(qualityLabel);
    const directUrl = forceHLS ? null : pickStreamUrl(info, qualityLabel);

    // Always use HLS for anything above 360p (YouTube split streams)
    if (window.BorgorHLS && $video && (forceHLS || !directUrl)) {
      try {
        setPlayerMode("hls-loading");
        const result = await BorgorHLS.load(
          currentUrl, qualityLabel, $video, $chkLowLatency?.checked || false
        );
        if (result.ok) {
          setPlayerMode("hls");
          setTimeout(() => { if ($overlay) $overlay.hidden = true; }, 3000);
          return;
        }
      } catch (e) {
        console.warn("[Player] HLS load error:", e.message);
      }
      // HLS failed for a quality that needs it — fall through to direct if possible
      if (forceHLS) {
        setPlayerMode("direct");
        showToast("HLS unavailable — try MPV pop-out for HD quality");
        if ($overlay) $overlay.hidden = true;
        return;
      }
    }

    // Direct URL playback (360p and below)
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
