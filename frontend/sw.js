/**
 * BorgorTube – Service Worker  (Phase 5i)
 *
 * Caches:
 *   - App shell (HTML, CSS, JS) → cache-first
 *   - Video thumbnails (img.youtube.com, i.ytimg.com) → stale-while-revalidate
 *   - API responses for search/history → network-first with 5s timeout
 *
 * Does NOT cache HLS segments or stream URLs (these are live/auth-gated).
 */

const CACHE_SHELL   = "borgortube-shell-v1";
const CACHE_THUMBS  = "borgortube-thumbs-v1";
const CACHE_API     = "borgortube-api-v1";

const SHELL_ASSETS = [
  "/static/index.html",
  "/static/css/style.css",
  "/static/js/api.js",
  "/static/js/hls_player.js",
  "/static/js/player.js",
  "/static/js/search.js",
  "/static/js/sync.js",
  "/static/js/app.js",
  "/static/manifest.json",
];

const THUMB_HOSTS = ["i.ytimg.com", "img.youtube.com", "yt3.ggpht.com"];
const API_PATHS   = ["/api/search", "/api/history"];

// ── Install: cache shell assets ─────────────────────────────────────────────

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_SHELL).then((cache) =>
      cache.addAll(SHELL_ASSETS).catch((err) => {
        console.warn("[SW] shell cache partial failure:", err);
      })
    ).then(() => self.skipWaiting())
  );
});

// ── Activate: clean old caches ───────────────────────────────────────────────

self.addEventListener("activate", (e) => {
  const live = [CACHE_SHELL, CACHE_THUMBS, CACHE_API];
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => !live.includes(k)).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch ────────────────────────────────────────────────────────────────────

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // ── Thumbnails: stale-while-revalidate ─────────────────────────────
  if (THUMB_HOSTS.includes(url.hostname)) {
    e.respondWith(staleWhileRevalidate(e.request, CACHE_THUMBS));
    return;
  }

  // ── Shell assets: cache-first ──────────────────────────────────────
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(cacheFirst(e.request, CACHE_SHELL));
    return;
  }

  // ── API reads: network-first with 5s timeout, fall back to cache ───
  if (
    url.pathname.startsWith("/api/search") ||
    url.pathname.startsWith("/api/history")
  ) {
    e.respondWith(networkFirstWithTimeout(e.request, CACHE_API, 5000));
    return;
  }

  // ── HLS segments & stream URLs: always network, never cache ────────
  if (
    url.pathname.startsWith("/hls/") ||
    url.pathname.startsWith("/api/hls/") ||
    url.pathname.startsWith("/api/download/")
  ) {
    // pass through untouched
    return;
  }

  // Default: network passthrough
});

// ── Strategy helpers ─────────────────────────────────────────────────────────

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(cacheName);
    cache.put(request, response.clone());
  }
  return response;
}

async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const fetchPromise = fetch(request).then((response) => {
    if (response.ok) cache.put(request, response.clone());
    return response;
  }).catch(() => cached);
  return cached || fetchPromise;
}

async function networkFirstWithTimeout(request, cacheName, timeoutMs) {
  const cache = await caches.open(cacheName);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(request, { signal: controller.signal });
    clearTimeout(timer);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    clearTimeout(timer);
    const cached = await cache.match(request);
    return cached || new Response(JSON.stringify({ error: "offline" }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}
