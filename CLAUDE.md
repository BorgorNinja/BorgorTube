# BorgorTube – Web Edition: Development Roadmap
`CLAUDE.md` — Phased implementation plan for sessions with tool-call budgets.

---

## Project Overview

BorgorTube was a PyQt5 desktop app that embedded mpv inside a Qt widget.
It has been converted to a **web-first architecture**:

| Layer | Technology | Port |
|-------|-----------|------|
| API backend | Python (FastAPI + uvicorn) | 8000 |
| MPV IPC bridge | Deno WebSocket (`ws_bridge.ts`) | 8001 |
| Frontend | HTML + CSS + Vanilla JS | served by backend |
| Video extraction | yt-dlp (Python) | — |
| Streaming | Browser `<video>` + MPV pop-out | — |

---

## Arch Diagram

```
Browser (JS)
    │
    ├─ REST fetch ──────────────── FastAPI (Python :8000)
    │                                   ├─ /api/search
    │                                   ├─ /api/video
    │                                   ├─ /api/channel
    │                                   ├─ /api/comments
    │                                   ├─ /api/mpv/*
    │                                   └─ spawns mpv subprocess
    │
    └─ WebSocket ──────────────── Deno ws_bridge.ts (:8001)
                                        └─ Unix socket ──── mpv --input-ipc-server=/tmp/mpvsocket
```

---

## MPV ↔ Browser Integration: Technical Analysis

### Why Direct mpv-in-browser Embedding is Not Possible

The original app used `--wid=<HWND>` to embed mpv inside a Qt widget window.
Browsers do **not** expose native window IDs to JavaScript, so this approach
cannot be replicated in a web frontend.

### Approach 1 (IMPLEMENTED) — Browser `<video>` + yt-dlp URLs
- yt-dlp extracts a direct HTTPS stream URL from YouTube
- `<video src="...">` plays it natively
- **Works for**: progressive MP4/WebM streams (typically ≤ 360p or 720p merged)
- **Limitation**: YouTube split streams (video-only + audio-only) cannot be
  demuxed by the browser. Only merged/progressive formats are playable this way.

### Approach 2 (IMPLEMENTED) — MPV Pop-out via API
- Backend spawns `mpv` as a detached subprocess
- mpv opens in its own window at any quality (using yt-dlp format strings)
- Browser communicates start time, quality, pause state via REST + WebSocket
- **Advantage**: Full quality (4K, 1080p60) available; all mpv features work
- **Limitation**: Separate window, not embedded in browser UI

### Approach 3 (PLANNED Phase 3) — HLS Transcoding Bridge
- Backend runs `ffmpeg` to transcode the yt-dlp stream into HLS segments
- Segments are served via `/hls/{session_id}/index.m3u8`
- Browser plays via `hls.js` (or native Safari HLS)
- **Advantage**: Any quality in the browser player, no separate window needed
- **Risk**: CPU-intensive transcoding; introduces latency

### Approach 4 (PLANNED Phase 4) — WebRTC from mpv Output
- mpv pipes decoded video to ffmpeg → GStreamer → WebRTC
- Extremely low latency; embeds mpv output directly in browser
- **Complexity**: High. Requires GStreamer + WebRTC signaling server
- **Best for**: Live streams / very low latency use cases

---

## Phase 1 — Core Web App (COMPLETE ✓)

**Goal**: Functional YouTube-clone web frontend + Python backend.

Files delivered:
- `backend/main.py` — FastAPI server, all REST endpoints, WebSocket MPV relay
- `backend/ytdl.py` — yt-dlp search/extraction/channel logic (from original)
- `backend/mpv_manager.py` — MPV subprocess + IPC socket wrapper
- `backend/scraper.py` — Comment scraping + channel avatar (pyppeteer, bs4)
- `frontend/index.html` — Full page structure (home, watch, channel)
- `frontend/css/style.css` — YouTube dark-theme clone
- `frontend/js/api.js` — All API calls + Deno WS bridge client
- `frontend/js/player.js` — Browser `<video>` + MPV pop-out control
- `frontend/js/search.js` — Card rendering, grid population
- `frontend/js/app.js` — Page routing, search, comments, channel
- `deno/ws_bridge.ts` — Deno WebSocket ↔ mpv IPC Unix socket bridge
- `requirements.txt` — Updated with FastAPI, uvicorn, deno note

**How to run** (Phase 1):
```bash
# 1. Install Python deps
pip install -r requirements.txt

# 2. Start backend
cd backend
python main.py
# → API + frontend served at http://localhost:8000
# → Frontend served at http://localhost:8000/static

# 3. (Optional) Start Deno MPV bridge
cd deno
deno run --allow-net --allow-read ws_bridge.ts
# → WS bridge at ws://localhost:8001

# 4. Open browser
open http://localhost:8000/static/index.html
```

---

## Phase 2 — HLS Transcoding for In-Browser High-Quality Playback ✓

**Goal**: Play any quality (1080p, 4K) directly in the browser's `<video>` tag
without needing a pop-out MPV window.

**STATUS: COMPLETE**

Files added/updated:
- `backend/hls_manager.py` — ffmpeg HLS session manager (start/stop/status/log)
- `backend/main.py` — `/api/hls/*` endpoints + `/hls/` static segment serving
- `frontend/js/hls_player.js` — hls.js client + BorgorAPI.hls* methods
- `frontend/js/player.js` — HLS-first load, quality switch, low-latency toggle
- `frontend/index.html` — player-mode-badge element, hls_player.js script tag
- `frontend/css/style.css` — mode-badge, hls-banner CSS
- `requirements.txt` — ffmpeg install note added

**Tasks**:

### 2a. Backend: HLS session manager (`backend/hls_manager.py`)
```python
# New file to create
# - HLSSession(id, url, quality) dataclass
# - launch_ffmpeg(session) → subprocess with pipe to HLS output dir
# - cleanup_session(id)
# - GET /hls/{session_id}/{filename} → StaticFiles route
```

Key ffmpeg command:
```bash
ffmpeg -re \
  -i "<yt-dlp stream URL>" \
  -c:v copy -c:a aac \
  -f hls \
  -hls_time 4 \
  -hls_list_size 10 \
  -hls_flags delete_segments \
  /tmp/hls/{session_id}/index.m3u8
```

### 2b. Backend: New endpoints
```
POST /api/hls/start  { url, quality }  → { session_id, hls_url }
DELETE /api/hls/{session_id}           → kill ffmpeg
GET  /api/hls/{session_id}/status      → { segments, position }
```

### 2c. Frontend: hls.js integration (`frontend/js/hls_player.js`)
```html
<!-- Add to index.html <head> -->
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
```
```js
// New module: HLSPlayer
// - loadHLS(hlsUrl, videoEl)
// - Uses Hls.js if browser doesn't support HLS natively
// - Falls back to native HLS on Safari
```

### 2d. Player integration
- In `player.js`: prefer HLS if session starts successfully
- Fall back to direct URL if HLS session fails to start within 5s
- Add "HD" badge to quality selector when HLS is active

**Session plan**: ~8 tool calls in `backend/hls_manager.py`, ~4 in `main.py`,
~6 in `frontend/js/hls_player.js`, ~3 in `index.html`.

---

## Phase 3 — Deno Bridge Polish + MPV Sync ✓

**Goal**: Seamless sync between browser `<video>` and mpv (for when both run).

**STATUS: COMPLETE**

Files added/updated:
- `deno/ws_bridge.ts` — rewritten with persistent socket, `observe_property` subscriptions,
  multiplexed request/response, EOF/idle event broadcasting
- `frontend/js/sync.js` — new module: mpv↔browser drift correction, pause/volume mirror,
  keyboard shortcuts (k/j/l/f/m/space/?), sync indicator overlay
- `frontend/js/app.js` — `BorgorSync.init()` called on startup
- `frontend/index.html` — sync.js script tag, keyboard shortcut hint badge
- `frontend/css/style.css` — sync-indicator overlay, kbd-hint CSS

**Tasks**:

### 3a. Bidirectional sync (`deno/ws_bridge.ts` additions)
- Subscribe to mpv property events via `observe_property` IPC command
- Push real-time position updates to browser (sub-second)
- Handle mpv EOF → auto-close badge in browser

Key mpv IPC for property observation:
```json
{ "command": ["observe_property", 1, "time-pos"] }
{ "command": ["observe_property", 2, "pause"] }
{ "command": ["observe_property", 3, "playback-time"] }
```
mpv will push JSON events back on the socket when these change.

### 3b. Frontend sync (`frontend/js/sync.js` — new file)
```js
// BorgorSync module
// - onMPVTimeUpdate(t) → if video.src matches, seek video to t (if drift > 2s)
// - onMPVPause(paused) → mirror pause state in browser player
// - Throttle sync to avoid feedback loops
```

### 3c. Deno bridge: property event forwarding
```typescript
// In ws_bridge.ts: maintain a persistent socket connection
// (rather than connect/disconnect per command)
// Use a multiplexed connection for commands + event subscription
```

**Session plan**: ~10 tool calls across `ws_bridge.ts` and new `sync.js`.

---

## Phase 4 — WebRTC Pipeline (Advanced / Optional)

**Goal**: Embed mpv's decoded video output directly into the browser using WebRTC.

**Feasibility assessment**: HIGH complexity. Recommended only if HLS latency
(Phase 2) is unacceptable (> 8s).

**Stack**:
```
mpv (decode) → video pipe → ffmpeg → GStreamer (appsrc) → webrtcbin → aiortc (Python) → browser RTCPeerConnection
```

**Prerequisites**:
```
pip install aiortc aiohttp
apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
```

**Tasks**:

### 4a. Python WebRTC signaling (`backend/webrtc_server.py`)
- Use `aiortc` library
- Endpoint: `POST /api/webrtc/offer` → SDP answer
- Pipe ffmpeg stdout → aiortc VideoStreamTrack

### 4b. mpv → ffmpeg pipe
```bash
mpv --vo=raw --vf=format=yuv420p --of=rawvideo - | \
  ffmpeg -f rawvideo -pix_fmt yuv420p -s 1280x720 -r 30 -i pipe:0 ...
```

### 4c. Frontend WebRTC (`frontend/js/webrtc_player.js`)
```js
const pc = new RTCPeerConnection();
pc.ontrack = (e) => { videoEl.srcObject = e.streams[0]; };
// offer/answer exchange with /api/webrtc/offer
```

**Session plan**: 1 full dedicated session (20+ tool calls). Use Research mode
to look up aiortc + mpv pipe examples before starting.

---

## Phase 5 — Feature Parity with Original Desktop App ✓

**Goal**: Match all features of the original PyQt5 app in the web UI.

**STATUS: COMPLETE**

Files added/updated:
- `backend/db.py` — aiosqlite watch history + cookie store
- `backend/downloader.py` — yt-dlp download jobs with SSE progress streaming
- `backend/main.py` — watch history, cookie upload, download endpoints
- `frontend/js/features.js` — search history chips, watch history page,
  cookie modal, download panel with live progress, keyboard shortcut overlay
- `frontend/js/app.js` — BorgorFeatures.init(), recordWatch, _currentVideoUrl
- `frontend/index.html` — PWA meta/manifest, history chips, Download/Cookies buttons
- `frontend/css/style.css` — chips, modal, download panel, shortcut overlay CSS
- `frontend/manifest.json` — PWA manifest
- `frontend/sw.js` — service worker (shell cache-first, thumbs stale-while-revalidate)
- `frontend/icons/` — SVG PWA icons
- `requirements.txt` — aiosqlite added
- `run.sh` — one-command full-stack startup script

### 5a. Quality-aware HLS (each quality = separate ffmpeg session)
### 5b. Low-latency mode toggle → smaller HLS segment size (0.5s)
### 5c. Search history sidebar with clickable chips
### 5d. Channel page: subscriber count scraping (bs4)
### 5e. Cookie-based auth fallback (age-restricted videos)
```
POST /api/auth/cookies  { cookies_txt_base64 }
```
### 5f. Download button → triggers yt-dlp download endpoint
```
POST /api/download  { url, quality, format }
GET  /api/download/progress/{job_id}  (SSE)
```
### 5g. Keyboard shortcuts overlay (K=play/pause, F=fullscreen, J/L=±10s)
### 5h. Persistent watch history (SQLite via `aiosqlite`)
### 5i. PWA manifest + service worker for offline thumbnail cache

**Session plan**: 2–3 sessions, tackle 5a–5c then 5d–5f then 5g–5i.

---

## Phase 6 — Polish & Production ✓

**STATUS: COMPLETE**

Files added/updated:
- `Dockerfile` — multi-stage Python build (mpv + ffmpeg + chromium included)
- `docker-compose.yml` — three-service stack: api + deno + nginx
- `nginx/nginx.conf` — reverse proxy, rate limiting zones, SSE/WS passthrough,
  gzip, security headers, HLS no-cache headers
- `backend/main.py` — slowapi rate limiting on search + video endpoints, /health endpoint
- `frontend/js/errors.js` — global error boundary with friendly banners + retry callbacks
- `frontend/js/app.js` — BorgorErrors.show() wired to all search/load/channel failures
- `frontend/css/style.css` — error banner, skip-link, focus-visible rings, full mobile
  overhaul (≤600px 2-col grid, touch targets, horizontal suggested scroll),
  reduced-motion, high-contrast, print styles
- `frontend/index.html` — skip-link, ARIA roles/labels on all major sections,
  aria-live on results/comments, errors.js script tag
- `.env.example` — all environment variable documentation
- `.dockerignore`, `.gitignore`
- `requirements.txt` — slowapi added

### 6a. Auth / multi-user (optional)
### 6b. Docker Compose deployment
```yaml
services:
  api:    { build: ./backend, ports: [8000] }
  deno:   { image: denoland/deno, command: run ... }
  nginx:  { ports: [80, 443], proxy_pass: api:8000 }
```
### 6c. Rate limiting (slowapi)
### 6d. Error boundary UI (retry logic)
### 6e. Responsive mobile layout polish
### 6f. Accessibility audit (ARIA labels, focus management)

---

## Session Resumption Checklist

When starting a new session to continue this project, paste this into Claude:

```
Continue BorgorTube web edition.
Current phase: [X]
Files already created in /borgortube/:
  backend/main.py, ytdl.py, mpv_manager.py, scraper.py, hls_manager.py
  frontend/index.html, css/style.css
  frontend/js/api.js, hls_player.js, player.js, search.js, sync.js, app.js
  deno/ws_bridge.ts
  requirements.txt, CLAUDE.md
Phases complete: 1,2,3,5 (all practical phases done)
Next phase: 4 (WebRTC, optional) or 6 (production polish)
Next task: [describe exact task from phase]
```

---

## Known Limitations & Notes

1. **YouTube CORS**: Direct stream URLs from yt-dlp include auth tokens that
   expire. The browser can play them directly but cannot proxy them via simple
   fetch() due to YouTube's CORS headers. The backend `/api/video` must be
   called for fresh URLs on each play.

2. **Split streams**: YouTube's best quality is split (video-only + audio-only).
   The browser's `<video>` tag cannot merge them. MPV pop-out handles this
   correctly via yt-dlp format merging. Phase 2 (HLS) will fix this for
   in-browser playback.

3. **mpv IPC socket on Windows**: `/tmp/mpvsocket` is a Unix socket path.
   On Windows, use a named pipe: `\\.\pipe\mpvsocket` and update
   `mpv_manager.py` and `ws_bridge.ts` accordingly.

4. **Deno bridge is optional**: If Deno is not installed, the app still works
   fully. The Deno bridge adds real-time MPV state to the browser UI but is
   not required for core functionality.

5. **pyppeteer comment scraping**: Comments are slow to load (headless Chrome
   scrolling). Phase 5 can replace this with a faster InnerTube API approach.
