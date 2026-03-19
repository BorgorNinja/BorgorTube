# BorgorTube

A self-hosted YouTube client with a full web frontend, running entirely in your browser. Search YouTube, stream videos in up to 1080p60 via HLS, launch MPV for higher quality or fullscreen, and download videos — all from a clean YouTube-style interface.

---

## What it looks like

- Dark/light theme YouTube-clone UI with a responsive video grid
- In-browser video player with quality selection and HLS streaming
- MPV pop-out for fullscreen or high-quality (4K) playback
- Watch history, search history chips, channel pages, and comments
- Download panel with live progress bars
- Installable as a PWA

---

## How it works

```
Browser ──── REST/SSE ────► FastAPI (Python)
        └─── WebSocket ───► Deno bridge ──► mpv IPC socket
                               ↑
                         real-time position,
                         pause, volume sync
```

The Python backend handles everything YouTube-related: searching, extracting stream URLs via yt-dlp, transcoding to HLS with ffmpeg, and managing mpv as a subprocess. The browser plays the HLS stream natively via hls.js. The optional Deno bridge gives the browser real-time access to mpv's IPC socket — so seeking in the browser seeks mpv and vice versa.

---

## Requirements

**Required**

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.10+ | Backend runtime | [python.org](https://python.org) |
| ffmpeg | HLS transcoding (in-browser HD) | `winget install Gyan.FFmpeg` / `apt install ffmpeg` / `brew install ffmpeg` |

**Strongly recommended**

| Tool | Purpose | Install |
|------|---------|---------|
| mpv | Pop-out player for 4K / fullscreen | `winget install mpv` / `apt install mpv` / `brew install mpv` |

**Optional**

| Tool | Purpose | Install |
|------|---------|---------|
| Deno | Real-time mpv↔browser sync bridge | `winget install DenoLand.Deno` / [deno.land](https://deno.land) |
| Playwright Chromium | Comment scraping | installed by `setup.bat` / `setup.sh` |

---

## Quick start

### 1. First-time setup

**Windows**
```bat
setup.bat
```

**Linux / macOS**
```bash
chmod +x setup.sh && ./setup.sh
```

This installs all Python dependencies (including the yt-dlp YouTube challenge solver) and downloads the Playwright Chromium browser for comment scraping.

### 2. Run

**Windows**
```bat
run.bat
```

**Linux / macOS**
```bash
./run.sh
```

**PowerShell (Windows)**
```powershell
# First time only:
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

.\run.ps1
```

Then open **http://localhost:8000** in your browser.

---

## Usage

### Searching
Type any search query or paste a YouTube URL directly into the search bar and press Enter. Clicking a video thumbnail opens the watch page and starts streaming immediately.

### Video quality
Use the quality dropdown below the player. Lower qualities (360p and below) stream as direct URLs. Higher qualities (720p+) are transcoded to HLS by ffmpeg on the backend so they play natively in the browser.

### MPV pop-out
Click **MPV Pop-out** to open the current video in a full mpv window at the selected quality. mpv handles all formats including AV1 and VP9 without transcoding, so 4K and 60fps streams play smoothly. The Deno bridge (if running) keeps the browser player and mpv in sync — seeking one seeks the other.

### Keyboard shortcuts
Press `?` anywhere on the watch page to see all shortcuts.

| Key | Action |
|-----|--------|
| `k` / `Space` | Play / Pause |
| `j` / `←` | Rewind 10s |
| `l` / `→` | Forward 10s |
| `↑` / `↓` | Volume ±10% |
| `m` | Mute |
| `f` | Fullscreen |

### Downloading
Click **Download** on the watch page, choose quality and format (MP4, WebM, MP3), and click Download. Progress streams live to the panel. Files are saved to `~/Downloads/BorgorTube/` by default.

### Age-restricted videos
Click **Cookies** and paste the contents of a Netscape-format `cookies.txt` file (export from your browser with a cookies.txt extension). The cookies are stored in the local SQLite database and used automatically for subsequent requests.

### Watch history
Click **History** in the sidebar to see previously watched videos. Videos are recorded automatically when you open the watch page.

---

## Command-line options

```bash
# Change port
./run.sh --port 8080
run.bat --port 8080

# Skip the Deno MPV bridge
./run.sh --no-deno
run.bat --no-deno

# PowerShell flags
.\run.ps1 -Port 8080 -NoDeno
```

---

## Docker

```bash
cp .env.example .env
docker compose up -d
```

Opens on **http://localhost** (nginx on port 80).

The compose stack runs three services: `api` (FastAPI backend), `deno` (MPV WebSocket bridge), and `nginx` (reverse proxy with rate limiting and static file serving).

---

## Project structure

```
borgortube/
├── backend/
│   ├── main.py          # FastAPI app — all REST + WebSocket endpoints
│   ├── ytdl.py          # yt-dlp search, extraction, channel helpers
│   ├── hls_manager.py   # ffmpeg HLS session manager
│   ├── mpv_manager.py   # mpv subprocess + IPC socket wrapper
│   ├── downloader.py    # background yt-dlp download jobs
│   ├── scraper.py       # playwright comment scraping
│   └── db.py            # aiosqlite watch history + cookie store
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   ├── js/
│   │   ├── api.js        # fetch wrappers + Deno bridge client
│   │   ├── player.js     # <video> player + MPV pop-out control
│   │   ├── hls_player.js # hls.js integration
│   │   ├── sync.js       # mpv↔browser drift correction + keyboard shortcuts
│   │   ├── search.js     # video card rendering
│   │   ├── features.js   # history, downloads, cookies, shortcut overlay
│   │   ├── errors.js     # global error boundary with retry
│   │   └── app.js        # page routing, search, comments, channel
│   ├── sw.js             # service worker (PWA)
│   └── manifest.json
├── deno/
│   └── ws_bridge.ts     # Deno WebSocket ↔ mpv IPC bridge
├── nginx/
│   └── nginx.conf
├── setup.sh / setup.bat
├── run.sh / run.bat / run.ps1
├── docker-compose.yml
└── Dockerfile
```

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/search?q=&max_results=` | Search YouTube |
| `GET` | `/api/video?url=` | Extract video info and stream URLs |
| `GET` | `/api/channel?url=` | Channel info and videos |
| `GET` | `/api/comments?url=&scroll_count=` | Scrape comments |
| `POST` | `/api/hls/start` | Start ffmpeg HLS session |
| `GET` | `/hls/{id}/index.m3u8` | HLS playlist (served as static) |
| `DELETE` | `/api/hls/{id}` | Stop HLS session |
| `POST` | `/api/mpv/launch` | Launch mpv subprocess |
| `POST` | `/api/mpv/kill` | Kill mpv |
| `GET` | `/api/mpv/status` | mpv status snapshot |
| `POST` | `/api/mpv/ipc` | Send raw mpv IPC command |
| `WS` | `/ws/mpv` | Live mpv status WebSocket |
| `POST` | `/api/download` | Start yt-dlp download job |
| `GET` | `/api/download/progress/{id}` | SSE download progress |
| `GET` | `/api/history/watch` | Watch history |
| `POST` | `/api/history/watch` | Record a watch |
| `POST` | `/api/auth/cookies` | Upload cookies.txt |
| `GET` | `/health` | Health check |

Interactive docs available at **http://localhost:8000/docs**

---

## Known limitations

- **Split streams in the browser:** YouTube serves its best quality as separate video-only and audio-only streams. The browser `<video>` element cannot merge them, so the backend transcodes via ffmpeg to H.264 HLS. This adds a few seconds of startup latency and uses CPU. MPV pop-out handles split streams natively with no transcoding.
- **Comments:** Require `playwright install chromium` (done by setup scripts). Scraping is slow (~10–20s) because it scrolls a headless browser through the YouTube page.
- **mpv IPC on Windows:** Uses a named pipe (`\\.\pipe\mpvsocket`). The Deno bridge attempts `connectPipe` — if your Deno version doesn't support it, real-time sync falls back to polling via the Python backend.
- **Stream URL expiry:** YouTube stream URLs expire after ~6 hours. Reopen the video to get fresh URLs.

---

## License

MIT — see [LICENSE](LICENSE)
