/**
 * BorgorTube – Search & card rendering module
 */

window.BorgorSearch = (() => {

  // ── Video card HTML factories ───────────────────────────────────────

  function makeVideoCard(video, onClick) {
    const card = document.createElement("div");
    card.className = "video-card";
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.setAttribute("title", video.title);

    const dur = video.duration ? BorgorPlayer.formatDuration(video.duration) : "";
    const views = video.view_count ? BorgorPlayer.formatViews(video.view_count) : "";
    const channel = video.uploader || "";

    card.innerHTML = `
      <div class="vc-thumb">
        ${video.thumbnail
          ? `<img src="${escHtml(video.thumbnail)}" alt="" loading="lazy" decoding="async" />`
          : `<div class="thumb-placeholder">▶</div>`}
        ${dur ? `<span class="vc-duration">${escHtml(dur)}</span>` : ""}
      </div>
      <div class="vc-info">
        <div class="vc-meta">
          <div class="vc-title">${escHtml(video.title)}</div>
          ${channel ? `<div class="vc-channel">${escHtml(channel)}</div>` : ""}
          ${views ? `<div class="vc-stats">${escHtml(views)}</div>` : ""}
        </div>
      </div>`;

    card.addEventListener("click", () => onClick(video));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") onClick(video);
    });
    // Prefetch on hover — resolves stream URLs before user clicks
    let prefetchTimer = null;
    card.addEventListener("mouseenter", () => {
      prefetchTimer = setTimeout(() => {
        BorgorAPI.prefetch(video.videoId, "720p", false);
      }, 300);  // 300ms debounce
    });
    card.addEventListener("mouseleave", () => clearTimeout(prefetchTimer));

    return card;
  }

  function makeSuggestedCard(video, onClick) {
    const card = document.createElement("div");
    card.className = "suggested-card";
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");

    const dur = video.duration ? BorgorPlayer.formatDuration(video.duration) : "";
    const channel = video.uploader || "";

    card.innerHTML = `
      <div class="sc-thumb">
        ${video.thumbnail
          ? `<img src="${escHtml(video.thumbnail)}" alt="" loading="lazy" />`
          : ""}
        ${dur ? `<span class="sc-duration">${escHtml(dur)}</span>` : ""}
      </div>
      <div class="sc-info">
        <div class="sc-title">${escHtml(video.title)}</div>
        ${channel ? `<div class="sc-channel">${escHtml(channel)}</div>` : ""}
      </div>`;

    card.addEventListener("click", () => onClick(video));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") onClick(video);
    });

    return card;
  }

  // ── Grid population ─────────────────────────────────────────────────

  function populateGrid(containerId, videos, onVideoClick) {
    const grid = document.getElementById(containerId);
    if (!grid) return;
    grid.innerHTML = "";
    for (const v of videos) {
      grid.appendChild(makeVideoCard(v, onVideoClick));
    }
  }

  function populateSuggested(containerId, videos, onVideoClick) {
    const list = document.getElementById(containerId);
    if (!list) return;
    list.innerHTML = "";
    for (const v of videos) {
      list.appendChild(makeSuggestedCard(v, onVideoClick));
    }
  }

  // ── Escape HTML ─────────────────────────────────────────────────────

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  return {
    makeVideoCard,
    makeSuggestedCard,
    populateGrid,
    populateSuggested,
    escHtml,
  };
})();
