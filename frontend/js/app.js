/**
 * BorgorTube – Main application
 * Orchestrates pages, search, watch, channel, comments.
 */

(() => {
  // ── State ─────────────────────────────────────────────────────────────
  let currentPage = "home";
  let currentVideoInfo = null;
  let commentScrollCount = 0;
  let commentsLoading = false;
  let currentChannelUrl = null;

  // ── DOM refs ──────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const $searchForm    = $("search-form");
  const $searchInput   = $("search-input");
  const $btnHome       = $("btn-home");
  const $btnMenu       = $("btn-menu");
  const $btnTheme      = $("btn-theme");
  const $sidebar       = $("sidebar");
  const $pageHome      = $("page-home");
  const $pageWatch     = $("page-watch");
  const $pageChannel   = $("page-channel");
  const $homeSpinner   = $("home-spinner");
  const $homeHeader    = $("home-header");
  const $homeTitle     = $("home-title");
  const $homePlaceholder = $("home-placeholder");
  const $btnLoadMore   = $("btn-load-more");
  const $descToggle    = $("desc-toggle");
  const $videoDesc     = $("video-desc");
  const $navHistory    = $("nav-history");

  // ── Toast helper ─────────────────────────────────────────────────────
  let toastTimer;
  window.showToast = function(msg, duration = 3000) {
    const t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), duration);
  };

  // ── Page navigation ───────────────────────────────────────────────────
  function showPage(name) {
    currentPage = name;
    $pageHome.hidden    = name !== "home";
    $pageWatch.hidden   = name !== "watch";
    $pageChannel.hidden = name !== "channel";

    document.querySelectorAll(".sidebar__item").forEach((el) =>
      el.classList.toggle("active", el.id === `nav-${name}`)
    );
  }

  // ── Search ────────────────────────────────────────────────────────────
  $searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    doSearch();
  });

  async function doSearch() {
    const q = $searchInput.value.trim();
    if (!q) return;

    // If it looks like a YouTube URL, go directly to watch
    if (isYouTubeUrl(q)) {
      loadVideo(q);
      return;
    }

    showPage("home");
    $homePlaceholder.hidden = true;
    $homeHeader.style.display = "";
    $homeTitle.textContent = `Results for "${q}"`;
    $homeSpinner.hidden = false;
    $("video-grid").innerHTML = "";

    try {
      const data = await BorgorAPI.search(q);
      $homeSpinner.hidden = true;
      BorgorSearch.populateGrid("video-grid", data.results, (v) => loadVideo(v.videoId));
      if (data.results.length === 0) {
        $("video-grid").innerHTML = `<p style="color:var(--text-2);padding:24px">No results found for "${q}".</p>`;
      }
    } catch (e) {
      $homeSpinner.hidden = true;
      BorgorErrors.show(e.message, () => doSearch());
    }
  }

  function isYouTubeUrl(str) {
    return /(?:youtube\.com|youtu\.be)/i.test(str);
  }

  // ── Load video ────────────────────────────────────────────────────────
  async function loadVideo(url) {
    showPage("watch");
    $("video-title").textContent = "Loading…";
    $("video-desc").textContent = "";
    $("channel-name").textContent = "";
    $("channel-avatar").src = "";
    $("comments-list").innerHTML = "";
    $("suggested-list").innerHTML = "";
    $btnLoadMore.disabled = false;
    $btnLoadMore.textContent = "Load more comments";
    commentScrollCount = 0;
    commentsLoading = false;

    try {
      const info = await BorgorPlayer.load(url);
      currentVideoInfo = info;
      window._currentVideoUrl = info.webpage_url || url;
      renderVideoInfo(info);
      BorgorFeatures.recordWatch(info);
      BorgorFeatures.refreshHistoryChips();
      loadComments(info.webpage_url || url, true);
      loadSuggested();
    } catch (e) {
      BorgorErrors.show(e.message, () => loadVideo(url));
    }
  }

  // ── Render video info block ───────────────────────────────────────────
  function renderVideoInfo(info) {
    $("video-title").textContent = info.title || "Untitled";

    const views = info.view_count ? BorgorPlayer.formatViews(info.view_count) : "";
    $("video-views").textContent = views;

    $("video-desc").textContent = info.description || "";
    $videoDesc.classList.remove("expanded");
    $descToggle.textContent = "Show more";

    // Channel
    const avatar = $("channel-avatar");
    const nameEl = $("channel-name");
    nameEl.textContent = info.uploader || "Unknown";
    if (info.thumbnail) {
      // Use video thumbnail as fallback for channel avatar
      avatar.src = info.thumbnail;
    }
    // Fetch real channel avatar async if channel URL available
    if (info.uploader_url) {
      currentChannelUrl = info.uploader_url;
      BorgorAPI.getChannel(info.uploader_url, 0)
        .then((ch) => { if (ch.avatar) avatar.src = ch.avatar; })
        .catch(() => {});
    }

    // Channel click → channel page
    $("channel-row").onclick = () => loadChannel(info.uploader_url, info.uploader);
  }

  // ── Description toggle ────────────────────────────────────────────────
  $descToggle.addEventListener("click", () => {
    const expanded = $videoDesc.classList.toggle("expanded");
    $descToggle.textContent = expanded ? "Show less" : "Show more";
  });

  // ── Comments ──────────────────────────────────────────────────────────
  async function loadComments(videoUrl, reset = false) {
    if (commentsLoading) return;
    if (!videoUrl) return;

    if (reset) commentScrollCount = 0;
    commentScrollCount++;
    commentsLoading = true;

    const spinner = $("comments-spinner");
    spinner.hidden = false;

    try {
      const data = await BorgorAPI.getComments(videoUrl, commentScrollCount, 50);
      spinner.hidden = true;
      commentsLoading = false;

      if (!data.comments || data.comments.length === 0) {
        $btnLoadMore.textContent = "No more comments";
        $btnLoadMore.disabled = true;
        return;
      }

      const list = $("comments-list");
      for (const c of data.comments) {
        list.appendChild(makeCommentEl(c));
      }
    } catch (e) {
      spinner.hidden = true;
      commentsLoading = false;
      showToast("Comments error: " + e.message);
    }
  }

  function makeCommentEl(c) {
    const div = document.createElement("div");
    div.className = "comment";
    const avatar = c.avatar
      ? `<img class="comment-avatar" src="${BorgorSearch.escHtml(c.avatar)}" alt="" loading="lazy" />`
      : `<div class="comment-avatar" style="background:var(--bg3)"></div>`;
    div.innerHTML = `
      ${avatar}
      <div class="comment-body">
        <div class="comment-user">${BorgorSearch.escHtml(c.username)}</div>
        <div class="comment-text">${BorgorSearch.escHtml(c.text)}</div>
      </div>`;
    return div;
  }

  $btnLoadMore.addEventListener("click", () => {
    if (!currentVideoInfo) return;
    loadComments(currentVideoInfo.webpage_url, false);
  });

  // ── Suggested videos ──────────────────────────────────────────────────
  async function loadSuggested() {
    const spinner = $("suggested-spinner");
    spinner.hidden = false;

    try {
      // Use last search query or video title as suggestion seed
      const seed = $searchInput.value.trim() || (currentVideoInfo?.title || "");
      const data = await BorgorAPI.search(seed, 10);
      spinner.hidden = true;
      BorgorSearch.populateSuggested("suggested-list", data.results, (v) => loadVideo(v.videoId));
    } catch {
      spinner.hidden = true;
    }
  }

  // ── Channel page ──────────────────────────────────────────────────────
  async function loadChannel(channelUrl, channelName) {
    if (!channelUrl) { showToast("No channel URL"); return; }
    showPage("channel");

    $("channel-hero-name").textContent = channelName || "Loading…";
    $("channel-hero-meta").textContent = "Loading videos…";
    $("channel-grid").innerHTML = "";

    try {
      const data = await BorgorAPI.getChannel(channelUrl, 24);
      if (data.avatar) $("channel-hero-avatar").src = data.avatar;
      $("channel-hero-meta").textContent = `${data.videos.length} videos`;
      BorgorSearch.populateGrid("channel-grid", data.videos, (v) => loadVideo(v.videoId));
    } catch (e) {
      BorgorErrors.show(e.message, () => loadChannel(channelUrl, channelName));
    }
  }

  // ── Search history sidebar item ───────────────────────────────────────
  $navHistory && $navHistory.addEventListener("click", (e) => {
    e.preventDefault();
    BorgorAPI.history().then((hist) => {
      const queries = (hist.queries || []).slice().reverse().slice(0, 5);
      if (!queries.length) { showToast("No search history"); return; }
      // Pre-fill search with last query
      $searchInput.value = queries[0];
      doSearch();
    });
  });

  // ── Sidebar explore links ─────────────────────────────────────────────
  document.querySelectorAll(".sidebar__item[data-search]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      $searchInput.value = el.dataset.search;
      doSearch();
    });
  });

  // ── Home / back button ────────────────────────────────────────────────
  $btnHome.addEventListener("click", (e) => {
    e.preventDefault();
    showPage("home");
  });

  // ── Sidebar toggle ────────────────────────────────────────────────────
  $btnMenu.addEventListener("click", () => {
    $sidebar.classList.toggle("collapsed");
  });

  // ── Theme toggle ──────────────────────────────────────────────────────
  $btnTheme.addEventListener("click", () => {
    const html = document.documentElement;
    const isDark = html.getAttribute("data-theme") === "dark";
    html.setAttribute("data-theme", isDark ? "light" : "dark");
    $btnTheme.querySelector(".icon-moon").style.display = isDark ? "none" : "";
    $btnTheme.querySelector(".icon-sun").style.display  = isDark ? "" : "none";
    localStorage.setItem("borgortube-theme", isDark ? "light" : "dark");
  });

  // Restore theme
  const savedTheme = localStorage.getItem("borgortube-theme");
  if (savedTheme) {
    document.documentElement.setAttribute("data-theme", savedTheme);
    if (savedTheme === "light") {
      $btnTheme.querySelector(".icon-moon").style.display = "none";
      $btnTheme.querySelector(".icon-sun").style.display = "";
    }
  }

  // ── Init ──────────────────────────────────────────────────────────────
  BorgorErrors.init();
  BorgorPlayer.init();
  BorgorSync.init();
  BorgorFeatures.init((v) => loadVideo(v.videoId));

  // Show placeholder on load
  showPage("home");
})();
