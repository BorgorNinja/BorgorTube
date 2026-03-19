/**
 * BorgorTube – Error boundary  (Phase 6d)
 *
 * - Catches unhandled promise rejections and JS errors globally
 * - Shows friendly in-UI error banners (not raw stack traces)
 * - Provides retry callbacks for recoverable failures
 * - Tracks repeated failures and suggests user actions
 */

window.BorgorErrors = (() => {

  // ── Error categorisation ─────────────────────────────────────────────

  const MESSAGES = {
    "Failed to fetch":          { msg: "Cannot reach the backend server.", action: "retry", icon: "🔌" },
    "NetworkError":             { msg: "Network connection lost.",          action: "retry", icon: "📡" },
    "HTTP 429":                 { msg: "Too many requests – slow down a bit.", action: "wait", icon: "⏱" },
    "HTTP 500":                 { msg: "Server error on the backend.",      action: "retry", icon: "🔧" },
    "HTTP 503":                 { msg: "Server is temporarily unavailable.", action: "retry", icon: "🔧" },
    "HTTP 404":                 { msg: "Resource not found.",               action: "dismiss", icon: "🔍" },
    "yt-dlp error":             { msg: "Could not extract video info from YouTube.", action: "retry", icon: "▶" },
    "No direct stream URL":     { msg: "No playable stream found for this video.", action: "mpv", icon: "▶" },
    "ffmpeg not found":         { msg: "ffmpeg is not installed on the server.", action: "info", icon: "⚙" },
    "mpv not connected":        { msg: "MPV is not running.", action: "dismiss", icon: "🎬" },
    "HLS not supported":        { msg: "Your browser doesn't support HLS – try MPV pop-out.", action: "mpv", icon: "🎬" },
    "autoplay":                 { msg: "Autoplay was blocked by the browser.", action: "dismiss", icon: "▶" },
    "default":                  { msg: "Something went wrong.",             action: "retry", icon: "⚠" },
  };

  function classify(errMsg) {
    for (const [key, val] of Object.entries(MESSAGES)) {
      if (key !== "default" && errMsg.toLowerCase().includes(key.toLowerCase())) {
        return val;
      }
    }
    return MESSAGES["default"];
  }

  // ── Error banner DOM ─────────────────────────────────────────────────

  function getOrCreateBanner() {
    let el = document.getElementById("error-banner");
    if (el) return el;
    el = document.createElement("div");
    el.id = "error-banner";
    el.className = "error-banner";
    el.hidden = true;
    el.setAttribute("role", "alert");
    el.setAttribute("aria-live", "assertive");
    document.body.appendChild(el);
    return el;
  }

  let dismissTimer = null;

  /**
   * Show a recoverable error banner.
   * @param {string} rawMsg  - raw error message
   * @param {Function|null} retryFn - called when user clicks Retry
   */
  function show(rawMsg, retryFn = null) {
    const { msg, action, icon } = classify(rawMsg);
    const banner = getOrCreateBanner();
    clearTimeout(dismissTimer);

    let actionHtml = "";
    if (action === "retry" && retryFn) {
      actionHtml = `<button class="eb-btn eb-retry" id="eb-retry">Retry</button>`;
    } else if (action === "mpv") {
      actionHtml = `<button class="eb-btn eb-mpv" id="eb-mpv-pop">Open in MPV</button>`;
    } else if (action === "info") {
      actionHtml = `<a class="eb-btn" href="https://ffmpeg.org/download.html" target="_blank" rel="noopener">Install ffmpeg ↗</a>`;
    }

    banner.innerHTML = `
      <span class="eb-icon">${icon}</span>
      <span class="eb-msg">${escHtml(msg)}</span>
      <div class="eb-actions">
        ${actionHtml}
        <button class="eb-dismiss" id="eb-dismiss" aria-label="Dismiss">✕</button>
      </div>`;
    banner.hidden = false;
    banner.classList.add("eb-show");

    // Wire buttons
    banner.querySelector("#eb-dismiss")?.addEventListener("click", dismiss);
    banner.querySelector("#eb-retry")?.addEventListener("click", () => {
      dismiss();
      retryFn?.();
    });
    banner.querySelector("#eb-mpv-pop")?.addEventListener("click", () => {
      dismiss();
      document.getElementById("btn-mpv-pop")?.click();
    });

    // Auto-dismiss non-critical errors after 8s
    if (action === "dismiss" || !retryFn) {
      dismissTimer = setTimeout(dismiss, 8000);
    }

    console.warn("[BorgorErrors]", rawMsg);
  }

  function dismiss() {
    const banner = document.getElementById("error-banner");
    if (!banner) return;
    banner.classList.remove("eb-show");
    setTimeout(() => { banner.hidden = true; }, 300);
  }

  // ── Global catch-all ─────────────────────────────────────────────────

  function init() {
    // Unhandled promise rejections
    window.addEventListener("unhandledrejection", (e) => {
      const msg = e.reason?.message || String(e.reason) || "Unhandled error";
      // Don't surface trivial abort errors
      if (msg.includes("AbortError") || msg.includes("The user aborted")) return;
      show(msg);
    });

    // Uncaught sync errors
    window.addEventListener("error", (e) => {
      if (e.message?.includes("ResizeObserver")) return; // browser quirk, ignore
      show(e.message || "Script error");
    });

    // Patch fetch to surface HTTP errors automatically
    const origFetch = window.fetch;
    window.fetch = async function (...args) {
      try {
        const res = await origFetch.apply(this, args);
        return res;
      } catch (err) {
        // Only surface network errors, not intentional aborts
        if (!err.name?.includes("Abort")) {
          // Don't auto-show — callers handle with retryFn
          // Just re-throw so callers can decide
        }
        throw err;
      }
    };
  }

  function escHtml(s) {
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  return { show, dismiss, init };
})();
