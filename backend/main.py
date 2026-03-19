#!/usr/bin/env python3
"""
BorgorTube – FastAPI Backend
Replaces the PyQt5 desktop app with a REST + WebSocket server.
"""

import asyncio
import json
import os
import socket
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ytdl import (
    search_youtube,
    extract_formats,
    get_channel_videos,
    available_buckets,
    FORMAT_MAPPING,
    ALL_QUALITIES,
    load_search_history,
    save_search_history,
)
from fastapi.responses import StreamingResponse, RedirectResponse, FileResponse
from fastapi.requests import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from mpv_manager import MPVManager
from scraper import scrape_comments_headless, scrape_channel_avatar
from hls_manager import HLSManager, HLS_ROOT
from db import record_watch, get_watch_history, delete_watch_entry, clear_watch_history, save_cookies, write_cookies_file
from downloader import DownloadManager
from executor import pool  # shared 16-worker thread pool

# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mpv = MPVManager()
    app.state.hls = HLSManager()
    app.state.dl  = DownloadManager()
    # Periodic cleanup task for expired HLS sessions
    async def _cleanup_loop():
        while True:
            await asyncio.sleep(60)
            app.state.hls.cleanup_expired()
    asyncio.create_task(_cleanup_loop())
    yield
    app.state.mpv.kill()
    app.state.hls.stop_all()


app = FastAPI(title="BorgorTube API", version="2.0.0", lifespan=lifespan)
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Root → redirect to frontend
@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


# Favicon
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "icons", "icon-192.svg")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/svg+xml")
    return RedirectResponse(url="/static/icons/icon-192.svg")


# Health check endpoint
@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# Serve the frontend from /
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_dir, html=True), name="static")
import pathlib as _pathlib
_pathlib.Path(HLS_ROOT).mkdir(parents=True, exist_ok=True)
app.mount("/hls", StaticFiles(directory=HLS_ROOT), name="hls")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/api/search")
@limiter.limit("20/minute")
async def api_search(request: Request, q: str = Query(..., min_length=1), max_results: int = 20):
    save_search_history(q)
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(pool, lambda: search_youtube(q, max_results))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"results": results, "query": q}


@app.get("/api/history")
async def api_history():
    return load_search_history()


# ---------------------------------------------------------------------------
# Video info & stream URLs
# ---------------------------------------------------------------------------

@app.get("/api/video")
@limiter.limit("30/minute")
async def api_video(request: Request, url: str = Query(...)):
    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(pool, lambda: extract_formats(url))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    buckets = available_buckets(info)

    # Build per-quality stream URL map
    stream_urls: dict[str, dict] = {}
    for fmt_label in buckets:
        fmt_str = FORMAT_MAPPING[fmt_label]
        # Find best matching format entry
        best_video = None
        best_audio = None
        for f in info.get("formats", []):
            if f.get("vcodec") != "none" and f.get("height"):
                best_video = f
            if f.get("acodec") != "none" and f.get("vcodec") == "none":
                best_audio = f
        stream_urls[fmt_label] = {
            "format_string": fmt_str,
        }

    # Provide the best direct URL for each quality via yt-dlp's url field
    # We include the raw best URL so the browser <video> can try it.
    best_url = info.get("url") or info.get("manifest_url") or ""

    # Collect direct format URLs for the browser player
    formats_for_browser = []
    for f in info.get("formats", []):
        if f.get("url") and f.get("height"):
            formats_for_browser.append({
                "url": f["url"],
                "height": f.get("height"),
                "fps": f.get("fps"),
                "ext": f.get("ext"),
                "acodec": f.get("acodec", "none"),
                "vcodec": f.get("vcodec", "none"),
                "tbr": f.get("tbr"),
                "protocol": f.get("protocol", ""),
            })

    return {
        "videoId": url,
        "title": info.get("title", "Untitled"),
        "description": info.get("description", ""),
        "uploader": info.get("uploader", "Unknown"),
        "uploader_url": info.get("uploader_url", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration": info.get("duration"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "qualities": buckets,
        "best_url": best_url,
        "formats": formats_for_browser,
        "webpage_url": info.get("webpage_url") or info.get("original_url") or url,
    }


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------

@app.get("/api/channel")
async def api_channel(url: str = Query(...), max_results: int = 20):
    loop = asyncio.get_event_loop()
    try:
        avatar = await loop.run_in_executor(pool, lambda: scrape_channel_avatar(url))
        videos = await loop.run_in_executor(pool, lambda: get_channel_videos(url, max_results))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"url": url, "avatar": avatar, "videos": videos}


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@app.get("/api/comments")
async def api_comments(
    url: str = Query(...),
    scroll_count: int = 1,
    max_comments: int = 50,
):
    try:
        comments = await scrape_comments_headless(url, scroll_count=scroll_count, max_comments=max_comments)
    except Exception as e:
        # Return empty rather than 500 – Chromium may not be ready yet
        print(f"[comments] scraping error (non-fatal): {e}")
        comments = []
    return {"comments": comments, "url": url, "note": "" if comments else "Comments unavailable (Chromium not ready)"}


# ---------------------------------------------------------------------------
# MPV control
# ---------------------------------------------------------------------------

@app.post("/api/mpv/launch")
async def api_mpv_launch(body: dict):
    url = body.get("url")
    quality = body.get("quality", "360p")
    start_time = body.get("start_time", 0.0)
    detached = body.get("detached", True)
    low_latency = body.get("low_latency", False)
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    app.state.mpv.launch(
        url=url,
        quality_label=quality,
        start_time=start_time,
        detached=detached,
        low_latency=low_latency,
    )
    return {"status": "launched", "quality": quality}


@app.post("/api/mpv/kill")
async def api_mpv_kill():
    app.state.mpv.kill()
    return {"status": "killed"}


@app.get("/api/mpv/status")
async def api_mpv_status():
    return app.state.mpv.get_status()


@app.post("/api/mpv/ipc")
async def api_mpv_ipc(body: dict):
    command = body.get("command")
    if not command:
        raise HTTPException(status_code=400, detail="command list required")
    result = app.state.mpv.send_ipc({"command": command})
    return result


# ---------------------------------------------------------------------------
# WebSocket – live MPV state relay
# ---------------------------------------------------------------------------

@app.websocket("/ws/mpv")
async def ws_mpv(websocket: WebSocket):
    await websocket.accept()
    mpv = app.state.mpv
    try:
        while True:
            # Receive a command from the browser
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                payload = json.loads(raw)
                action = payload.get("action")

                if action == "ipc":
                    result = mpv.send_ipc({"command": payload.get("command", [])})
                    await websocket.send_json({"type": "ipc_response", "data": result})
                elif action == "launch":
                    mpv.launch(**{k: v for k, v in payload.items() if k != "action"})
                    await websocket.send_json({"type": "launched"})
                elif action == "kill":
                    mpv.kill()
                    await websocket.send_json({"type": "killed"})

            except asyncio.TimeoutError:
                pass

            # Push current MPV state to browser
            status = mpv.get_status()
            await websocket.send_json({"type": "status", "data": status})
            await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


# ---------------------------------------------------------------------------
# HLS streaming (Phase 2)
# ---------------------------------------------------------------------------

@app.post("/api/hls/start")
async def api_hls_start(body: dict):
    """
    Start an HLS transcoding session.
    Body: { url, quality, low_latency }
    Returns: { session_id, playlist_url, ready }
    """
    url = body.get("url")
    quality = body.get("quality", "720p")
    low_latency = body.get("low_latency", False)
    if not url:
        raise HTTPException(status_code=400, detail="url required")

    cookies_file = "cookies.txt" if os.path.exists("cookies.txt") else None
    session = await app.state.hls.start(
        url, quality=quality, low_latency=low_latency, cookies_file=cookies_file
    )

    # Return immediately — ffmpeg is launching in the background.
    # The client polls /api/hls/{session_id}/status for ready=true.
    # This means the endpoint responds in <5ms regardless of how many
    # users are simultaneously starting streams.
    if session.error:
        raise HTTPException(status_code=500, detail=session.error)

    return session.to_dict()


@app.get("/api/hls/{session_id}/status")
async def api_hls_status(session_id: str):
    session = app.state.hls.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


@app.get("/api/hls/{session_id}/log")
async def api_hls_log(session_id: str, tail: int = 30):
    log = app.state.hls.get_log(session_id, tail=tail)
    return {"log": log}


@app.delete("/api/hls/{session_id}")
async def api_hls_stop(session_id: str):
    ok = app.state.hls.stop(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "stopped", "session_id": session_id}


@app.get("/api/hls")
async def api_hls_list():
    return {"sessions": app.state.hls.list_sessions()}


# ---------------------------------------------------------------------------
# Watch history  (Phase 5h)
# ---------------------------------------------------------------------------

@app.post("/api/history/watch")
async def api_record_watch(body: dict):
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    await record_watch(
        video_url=url,
        title=body.get("title", ""),
        thumbnail=body.get("thumbnail", ""),
        uploader=body.get("uploader", ""),
        uploader_url=body.get("uploader_url", ""),
        duration=body.get("duration"),
    )
    return {"status": "recorded"}


@app.get("/api/history/watch")
async def api_get_watch_history(limit: int = 50, offset: int = 0):
    rows = await get_watch_history(limit=limit, offset=offset)
    return {"history": rows, "limit": limit, "offset": offset}


@app.delete("/api/history/watch/{video_url:path}")
async def api_delete_watch_entry(video_url: str):
    ok = await delete_watch_entry(video_url)
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "deleted"}


@app.delete("/api/history/watch")
async def api_clear_watch_history():
    count = await clear_watch_history()
    return {"status": "cleared", "count": count}


# ---------------------------------------------------------------------------
# Cookie upload  (Phase 5e)
# ---------------------------------------------------------------------------

@app.post("/api/auth/cookies")
async def api_upload_cookies(body: dict):
    """
    Accept a Netscape-format cookies.txt (as plain text or base64).
    Store in DB and write to disk for yt-dlp to use.
    """
    import base64
    raw = body.get("cookies_txt", "")
    label = body.get("label", "default")
    if not raw:
        raise HTTPException(status_code=400, detail="cookies_txt required")

    # Accept base64-encoded content
    if not raw.strip().startswith("#"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            pass  # assume it's already plain text

    row_id = await save_cookies(raw, label=label)
    await write_cookies_file("cookies.txt", label=label)
    return {"status": "saved", "id": row_id, "label": label}


@app.get("/api/auth/cookies/status")
async def api_cookies_status():
    from db import get_latest_cookies
    content = await get_latest_cookies()
    has_cookies = content is not None and len(content.strip()) > 0
    return {"has_cookies": has_cookies, "file_exists": os.path.exists("cookies.txt")}


# ---------------------------------------------------------------------------
# Downloads  (Phase 5f)
# ---------------------------------------------------------------------------

@app.post("/api/download")
async def api_download_start(body: dict):
    url     = body.get("url")
    quality = body.get("quality", "720p")
    fmt     = body.get("format", "mp4")
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    cookies_file = "cookies.txt" if os.path.exists("cookies.txt") else None
    job = app.state.dl.start(url=url, quality=quality, fmt=fmt, cookies_file=cookies_file)
    return job.to_dict()


@app.get("/api/download/progress/{job_id}")
async def api_download_progress(job_id: str):
    job = app.state.dl.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(
        app.state.dl.event_stream(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/download/{job_id}/status")
async def api_download_status(job_id: str):
    job = app.state.dl.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.delete("/api/download/{job_id}")
async def api_download_cancel(job_id: str):
    ok = app.state.dl.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "cancelling", "job_id": job_id}


@app.get("/api/download")
async def api_download_list():
    return {"jobs": app.state.dl.list_jobs()}
