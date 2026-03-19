/**
 * BorgorTube – Phase 5 features
 *
 * Covers:
 *   5c  Search history chips in sidebar
 *   5d  Watch history page
 *   5e  Cookie upload modal
 *   5f  Download panel with SSE progress
 *   5g  Keyboard shortcut overlay (rendered here; handling in sync.js)
 *   5h  Watch recording on video load
 *   5i  PWA service worker registration
 */

window.BorgorFeatures = (() => {

  // ── 5i — Service worker registration ───────────────────────────────

  function registerSW() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/static/sw.js")
        .then(() => console.log("[SW] registered"))
        .catch((e) => console.warn("[SW] registration failed:", e));
    }
  }

  // ── 5c — Search history chips ───────────────────────────────────────

  function buildHistoryChips(queries) {
    const container = document.getElementById("history-chips");
    if (!container) return;
    container.innerHTML = "";
    const recent = [...queries].reverse().slice(0, 8);
    for (const q of recent) {
      const chip = document.createElement("button");
      chip.className = "history-chip";
      chip.textContent = q;
      chip.title = q;
      chip.addEventListener("click", () => {
        const input = document.getElementById("search-input");
        if (input) { input.value = q; }
        document.getElementById("search-form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      });
      container.appendChild(chip);
    }
  }

  async function refreshHistoryChips() {
    try {
      const data = await BorgorAPI.history();
      buildHistoryChips(data.queries || []);
    } catch { /* ignore */ }
  }

  // ── 5d — Watch history page ─────────────────────────────────────────

  async function showWatchHistory(onVideoClick) {
    const grid = document.getElementById("video-grid");
    const title = document.getElementById("home-title");
    const header = document.getElementById("home-header");
    const placeholder = document.getElementById("home-placeholder");
    if (!grid) return;

    grid.innerHTML = "";
    if (header) header.style.display = "";
    if (title) title.textContent = "Watch History";
    if (placeholder) placeholder.hidden = true;

    try {
      const data = await BorgorAPI.watchHistory();
      const items = data.history || [];
      if (items.length === 0) {
        grid.innerHTML = `<p style="color:var(--text-2);padding:24px 4px">No watch history yet.</p>`;
        return;
      }
      for (const entry of items) {
        const video = {
          title: entry.title || entry.video_url,
          videoId: entry.video_url,
          thumbnail: entry.thumbnail,
          uploader: entry.uploader,
          duration: entry.duration,
        };
        grid.appendChild(BorgorSearch.makeVideoCard(video, onVideoClick));
      }
    } catch (e) {
      grid.innerHTML = `<p style="color:var(--text-2);padding:24px 4px">Failed to load history.</p>`;
      showToast("History error: " + e.message);
    }
  }

  // Record a video as watched (called by app.js when video loads)
  async function recordWatch(info) {
    if (!info || !info.webpage_url) return;
    try {
      await BorgorAPI.recordWatch({
        url: info.webpage_url,
        title: info.title,
        thumbnail: info.thumbnail,
        uploader: info.uploader,
        uploader_url: info.uploader_url,
        duration: info.duration,
      });
    } catch { /* non-critical */ }
  }

  // ── 5e — Cookie upload modal ────────────────────────────────────────

  function buildCookieModal() {
    if (document.getElementById("cookie-modal")) return;
    const modal = document.createElement("div");
    modal.id = "cookie-modal";
    modal.className = "modal-backdrop";
    modal.hidden = true;
    modal.innerHTML = `
      <div class="modal-box">
        <div class="modal-header">
          <h3 class="modal-title">Upload cookies.txt</h3>
          <button class="modal-close" id="cookie-modal-close" aria-label="Close">✕</button>
        </div>
        <div class="modal-body">
          <p class="modal-desc">
            Required for age-restricted or members-only videos.<br>
            Export from your browser using a cookies.txt extension (Netscape format).
          </p>
          <textarea id="cookie-textarea" class="cookie-textarea"
            placeholder="Paste your cookies.txt content here…" spellcheck="false"></textarea>
          <div class="modal-status" id="cookie-status"></div>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" id="cookie-modal-close2">Cancel</button>
          <button class="btn-primary" id="cookie-upload-btn">Save Cookies</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    const close = () => { modal.hidden = true; };
    modal.querySelector("#cookie-modal-close").addEventListener("click", close);
    modal.querySelector("#cookie-modal-close2").addEventListener("click", close);
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });

    modal.querySelector("#cookie-upload-btn").addEventListener("click", async () => {
      const text = modal.querySelector("#cookie-textarea").value.trim();
      const statusEl = modal.querySelector("#cookie-status");
      if (!text) { statusEl.textContent = "⚠ Paste cookie content first."; return; }
      statusEl.textContent = "Saving…";
      try {
        await BorgorAPI.uploadCookies(text);
        statusEl.textContent = "✓ Cookies saved. Age-restricted videos should now work.";
        setTimeout(close, 2000);
      } catch (e) {
        statusEl.textContent = "✗ Error: " + e.message;
      }
    });
  }

  function showCookieModal() {
    buildCookieModal();
    document.getElementById("cookie-modal").hidden = false;
  }

  // ── 5f — Download panel ─────────────────────────────────────────────

  function buildDownloadPanel() {
    if (document.getElementById("download-panel")) return;
    const panel = document.createElement("div");
    panel.id = "download-panel";
    panel.className = "download-panel";
    panel.hidden = true;
    panel.innerHTML = `
      <div class="dp-header">
        <span class="dp-title">Download</span>
        <button class="dp-close" id="dp-close">✕</button>
      </div>
      <div class="dp-options">
        <select id="dp-quality" class="quality-select" title="Quality">
          <option value="1080p">1080p</option>
          <option value="720p" selected>720p</option>
          <option value="360p">360p</option>
          <option value="audio">Audio only (MP3)</option>
        </select>
        <select id="dp-format" class="quality-select" title="Format">
          <option value="mp4">MP4</option>
          <option value="webm">WebM</option>
          <option value="mp3">MP3</option>
        </select>
        <button class="btn-primary dp-start" id="dp-start">Download</button>
      </div>
      <div class="dp-jobs" id="dp-jobs"></div>`;
    document.body.appendChild(panel);

    document.getElementById("dp-close").addEventListener("click", () => {
      panel.hidden = true;
    });

    document.getElementById("dp-start").addEventListener("click", startDownload);
  }

  async function startDownload() {
    const urlEl = document.getElementById("dp-current-url");
    const url = urlEl ? urlEl.value : (window._currentVideoUrl || "");
    if (!url) { showToast("No video loaded"); return; }

    const quality = document.getElementById("dp-quality").value;
    const fmt = document.getElementById("dp-format").value;

    try {
      const job = await BorgorAPI.startDownload({ url, quality, format: fmt });
      renderDownloadJob(job);
      subscribeDownloadProgress(job.job_id);
    } catch (e) {
      showToast("Download error: " + e.message);
    }
  }

  function renderDownloadJob(job) {
    const container = document.getElementById("dp-jobs");
    if (!container) return;
    const el = document.createElement("div");
    el.className = "dp-job";
    el.id = `dp-job-${job.job_id}`;
    el.innerHTML = `
      <div class="dp-job-title">${BorgorSearch.escHtml(job.url)}</div>
      <div class="dp-job-row">
        <div class="dp-progress-track">
          <div class="dp-progress-bar" id="dp-bar-${job.job_id}" style="width:0%"></div>
        </div>
        <span class="dp-pct" id="dp-pct-${job.job_id}">0%</span>
        <button class="dp-cancel" data-job="${job.job_id}" title="Cancel">✕</button>
      </div>
      <div class="dp-job-meta" id="dp-meta-${job.job_id}">Queued…</div>`;
    container.prepend(el);

    el.querySelector(".dp-cancel").addEventListener("click", async (e) => {
      const jid = e.currentTarget.dataset.job;
      await BorgorAPI.cancelDownload(jid).catch(() => {});
      setJobMeta(jid, "Cancelling…");
    });
  }

  function setJobProgress(jobId, pct, speed, eta) {
    const bar = document.getElementById(`dp-bar-${jobId}`);
    const pctEl = document.getElementById(`dp-pct-${jobId}`);
    if (bar) bar.style.width = pct + "%";
    if (pctEl) pctEl.textContent = Math.round(pct) + "%";
    if (speed || eta) setJobMeta(jobId, `${speed} · ETA ${eta}`);
  }

  function setJobMeta(jobId, text) {
    const el = document.getElementById(`dp-meta-${jobId}`);
    if (el) el.textContent = text;
  }

  function subscribeDownloadProgress(jobId) {
    const source = new EventSource(`/api/download/progress/${jobId}`);
    source.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "progress") {
          setJobProgress(jobId, msg.progress, msg.speed, msg.eta);
        } else if (msg.type === "merging") {
          setJobMeta(jobId, "Merging streams…");
          setJobProgress(jobId, 99, "", "");
        } else if (msg.type === "done" || msg.type === "final") {
          setJobProgress(jobId, 100, "", "");
          setJobMeta(jobId, `✓ Done: ${msg.filename || ""}`);
          source.close();
          showToast(`Download complete: ${msg.filename || "file"}`);
        } else if (msg.type === "error") {
          setJobMeta(jobId, "✗ Error: " + msg.error);
          source.close();
        } else if (msg.type === "cancelled") {
          setJobMeta(jobId, "Cancelled");
          source.close();
        }
      } catch { /* ignore */ }
    };
    source.onerror = () => { source.close(); };
  }

  function showDownloadPanel(videoUrl) {
    buildDownloadPanel();
    // Store URL for the download panel
    window._currentVideoUrl = videoUrl;
    let urlInput = document.getElementById("dp-current-url");
    if (!urlInput) {
      urlInput = document.createElement("input");
      urlInput.id = "dp-current-url";
      urlInput.type = "hidden";
      document.getElementById("download-panel").appendChild(urlInput);
    }
    urlInput.value = videoUrl || "";
    document.getElementById("download-panel").hidden = false;
  }

  // ── 5g — Keyboard shortcut overlay ─────────────────────────────────

  function buildShortcutOverlay() {
    if (document.getElementById("shortcut-overlay")) return;
    const ov = document.createElement("div");
    ov.id = "shortcut-overlay";
    ov.className = "shortcut-overlay";
    ov.hidden = true;
    ov.innerHTML = `
      <div class="so-box">
        <div class="so-header">Keyboard Shortcuts</div>
        <div class="so-grid">
          ${[
            ["k / Space", "Play / Pause"],
            ["j / ←", "Rewind 10s"],
            ["l / →", "Forward 10s"],
            ["↑ / ↓", "Volume ±10%"],
            ["m", "Mute / Unmute"],
            ["f", "Fullscreen"],
            ["?", "Show this overlay"],
            ["Esc", "Close overlay"],
          ].map(([k, v]) => `
            <div class="so-key"><kbd>${k}</kbd></div>
            <div class="so-val">${v}</div>`).join("")}
        </div>
        <button class="so-close" id="so-close">Close</button>
      </div>`;
    document.body.appendChild(ov);
    document.getElementById("so-close").addEventListener("click", () => {
      ov.hidden = true;
    });
    ov.addEventListener("click", (e) => { if (e.target === ov) ov.hidden = true; });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") ov.hidden = true;
      if (e.key === "?" && !["INPUT","TEXTAREA"].includes(e.target.tagName)) {
        ov.hidden = !ov.hidden;
      }
    });
  }

  // ── BorgorAPI extensions (Phase 5) ─────────────────────────────────

  const API_BASE = () =>
    window.location.origin.includes("localhost") || window.location.origin.includes("127.0.0.1")
      ? "http://localhost:8000" : window.location.origin;

  async function apiFetch5(path, opts = {}) {
    const res = await fetch(API_BASE() + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  Object.assign(window.BorgorAPI, {
    watchHistory:   (limit = 50, offset = 0) =>
      apiFetch5(`/api/history/watch?limit=${limit}&offset=${offset}`),
    recordWatch:    (body)   => apiFetch5("/api/history/watch", { method: "POST", body: JSON.stringify(body) }),
    clearHistory:   ()       => apiFetch5("/api/history/watch", { method: "DELETE" }),
    uploadCookies:  (txt, label = "default") =>
      apiFetch5("/api/auth/cookies", { method: "POST", body: JSON.stringify({ cookies_txt: txt, label }) }),
    cookiesStatus:  ()       => apiFetch5("/api/auth/cookies/status"),
    startDownload:  (body)   => apiFetch5("/api/download", { method: "POST", body: JSON.stringify(body) }),
    cancelDownload: (jobId)  => apiFetch5(`/api/download/${jobId}`, { method: "DELETE" }),
    listDownloads:  ()       => apiFetch5("/api/download"),
  });

  // ── Public API ──────────────────────────────────────────────────────

  function init(onVideoClick) {
    registerSW();
    refreshHistoryChips();
    buildShortcutOverlay();

    // Wire up sidebar history nav
    const navHistory = document.getElementById("nav-history");
    if (navHistory) {
      navHistory.addEventListener("click", (e) => {
        e.preventDefault();
        const pageHome = document.getElementById("page-home");
        const pageWatch = document.getElementById("page-watch");
        const pageChannel = document.getElementById("page-channel");
        if (pageHome) pageHome.hidden = false;
        if (pageWatch) pageWatch.hidden = true;
        if (pageChannel) pageChannel.hidden = true;
        showWatchHistory(onVideoClick);
      });
    }

    // Wire up cookie upload button (in player controls)
    const btnCookies = document.getElementById("btn-cookies");
    if (btnCookies) btnCookies.addEventListener("click", showCookieModal);

    // Wire up download button
    const btnDownload = document.getElementById("btn-download");
    if (btnDownload) btnDownload.addEventListener("click", () => {
      showDownloadPanel(window._currentVideoUrl);
    });
  }

  return {
    init,
    recordWatch,
    showWatchHistory,
    showCookieModal,
    showDownloadPanel,
    refreshHistoryChips,
  };
})();
