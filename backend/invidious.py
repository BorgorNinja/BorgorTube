"""
BorgorTube – Invidious / Piped API client

FreeTube approach: use public Invidious/Piped instances for fast metadata
and search. These APIs return JSON in <200ms vs yt-dlp's 2-5s per request.
yt-dlp is kept as the authoritative fallback for stream URL resolution.

Instance list auto-rotates on failure so one dead instance doesn't break
the app.
"""

import asyncio
import random
import time
import threading
from typing import Optional

import requests

_session = requests.Session()
_session.headers.update({
    "User-Agent": "BorgorTube/2.0",
    "Accept": "application/json",
})
_session.timeout = 6

# ---------------------------------------------------------------------------
# Public Invidious instances  (health-checked at startup, rotated on failure)
# Taken from https://api.invidious.io/ — all support /api/v1
# ---------------------------------------------------------------------------
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.privacydev.net",
    "https://iv.melmac.space",
    "https://invidious.nerdvpn.de",
    "https://invidious.dhusch.de",
    "https://yt.artemislena.eu",
    "https://invidious.lunar.icu",
    "https://invidious.io.lol",
]

_lock = threading.Lock()
_healthy: list[str] = []
_last_health_check: float = 0
HEALTH_CHECK_INTERVAL = 300  # re-check every 5 minutes


def _check_instance(base_url: str) -> bool:
    """Return True if the instance responds to a lightweight stats endpoint."""
    try:
        r = _session.get(f"{base_url}/api/v1/stats", timeout=4)
        return r.status_code == 200
    except Exception:
        return False


def get_healthy_instances(force: bool = False) -> list[str]:
    """Return list of responsive Invidious instances, cached for 5 minutes."""
    global _last_health_check
    with _lock:
        now = time.time()
        if _healthy and not force and (now - _last_health_check) < HEALTH_CHECK_INTERVAL:
            return list(_healthy)

    # Run checks concurrently in threads
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=len(INVIDIOUS_INSTANCES)) as ex:
        futures = {ex.submit(_check_instance, u): u for u in INVIDIOUS_INSTANCES}
        for fut in as_completed(futures, timeout=5):
            url = futures[fut]
            try:
                if fut.result():
                    results.append(url)
            except Exception:
                pass

    with _lock:
        _healthy[:] = results if results else INVIDIOUS_INSTANCES[:3]
        _last_health_check = time.time()
    return list(_healthy)


def _pick() -> str:
    instances = get_healthy_instances()
    return random.choice(instances) if instances else INVIDIOUS_INSTANCES[0]


def _get(path: str, params: dict | None = None, retries: int = 3) -> dict | list | None:
    """GET from Invidious API, rotating instances on failure."""
    tried: set[str] = set()
    for _ in range(retries):
        base = _pick()
        if base in tried:
            continue
        tried.add(base)
        try:
            r = _session.get(f"{base}{path}", params=params, timeout=6)
            if r.status_code == 200:
                return r.json()
            # Mark instance unhealthy if it returns errors
            with _lock:
                if base in _healthy:
                    _healthy.remove(base)
        except Exception:
            with _lock:
                if base in _healthy:
                    _healthy.remove(base)
    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(query: str, max_results: int = 20) -> list[dict]:
    """
    Search via Invidious API. Returns same shape as ytdl.search_youtube().
    Typically 200-400ms vs yt-dlp's 3-6s.
    """
    data = _get("/api/v1/search", params={
        "q": query,
        "type": "video",
        "sort_by": "relevance",
        "page": 1,
    })
    if not data or not isinstance(data, list):
        return []

    results = []
    for item in data[:max_results]:
        if item.get("type") != "video":
            continue
        video_id = item.get("videoId", "")
        thumb = _best_thumbnail(item.get("videoThumbnails", []))
        results.append({
            "title":      item.get("title", "Unknown"),
            "videoId":    f"https://www.youtube.com/watch?v={video_id}",
            "videoIdRaw": video_id,
            "thumbnail":  thumb,
            "duration":   item.get("lengthSeconds"),
            "uploader":   item.get("author", ""),
            "view_count": item.get("viewCount"),
        })
    return results


# ---------------------------------------------------------------------------
# Video metadata  (fast path — no stream URL extraction)
# ---------------------------------------------------------------------------

def get_video_info(video_id: str) -> dict | None:
    """
    Fetch video metadata from Invidious.
    Returns title, description, uploader, formats, thumbnails.
    Typically 300-600ms.
    """
    data = _get(f"/api/v1/videos/{video_id}", params={"fields": (
        "videoId,title,description,author,authorUrl,authorThumbnails,"
        "viewCount,likeCount,lengthSeconds,published,adaptiveFormats,"
        "formatStreams,videoThumbnails,recommendedVideos"
    )})
    if not data or not isinstance(data, dict):
        return None
    return data


def get_formats(video_id: str) -> tuple[list[dict], list[dict]]:
    """
    Return (adaptive_formats, format_streams) from Invidious.
    adaptive_formats = split video+audio (high quality).
    format_streams = merged progressive streams (low quality, always playable).
    """
    info = get_video_info(video_id)
    if not info:
        return [], []
    return info.get("adaptiveFormats", []), info.get("formatStreams", [])


def get_recommended(video_id: str, limit: int = 10) -> list[dict]:
    """Get recommended videos — used to populate 'Up Next' sidebar."""
    info = get_video_info(video_id)
    if not info:
        return []
    recs = info.get("recommendedVideos", [])[:limit]
    return [
        {
            "title":      r.get("title", "Unknown"),
            "videoId":    f"https://www.youtube.com/watch?v={r.get('videoId','')}",
            "videoIdRaw": r.get("videoId", ""),
            "thumbnail":  _best_thumbnail(r.get("videoThumbnails", [])),
            "duration":   r.get("lengthSeconds"),
            "uploader":   r.get("author", ""),
            "view_count": r.get("viewCount"),
        }
        for r in recs
        if r.get("videoId")
    ]


def get_channel_videos_fast(channel_id: str, max_results: int = 24) -> list[dict]:
    """Fast channel videos via Invidious channel API."""
    data = _get(f"/api/v1/channels/{channel_id}/videos", params={"page": 1})
    if not data or not isinstance(data, dict):
        return []
    videos = []
    for item in data.get("videos", [])[:max_results]:
        vid_id = item.get("videoId", "")
        thumb = _best_thumbnail(item.get("videoThumbnails", []))
        videos.append({
            "title":      item.get("title", "Unknown"),
            "videoId":    f"https://www.youtube.com/watch?v={vid_id}",
            "videoIdRaw": vid_id,
            "thumbnail":  thumb,
            "duration":   item.get("lengthSeconds"),
        })
    return videos


# ---------------------------------------------------------------------------
# Stream URL selection from Invidious adaptive formats
# ---------------------------------------------------------------------------

# Map quality label → minimum height
_QUALITY_HEIGHT = {
    "2k": 1440, "1080p60": 1080, "1080p": 1080,
    "720p60": 720, "720p": 720, "360p": 360,
    "240p": 240, "144p": 144,
}


def _ql_to_height(ql: str) -> int:
    """Extract numeric height from Invidious qualityLabel e.g. '1080p60' -> 1080."""
    import re
    m = re.match(r"(\d+)", ql or "")
    return int(m.group(1)) if m else 0


def pick_stream_urls(
    adaptive_formats: list[dict],
    format_streams: list[dict],
    quality: str = "720p",
) -> tuple[str, Optional[str]]:
    """
    Pick (video_url, audio_url) from Invidious format lists.
    Invidious uses 'qualityLabel' (e.g. "1080p", "720p60") NOT 'resolution'.
    """
    target_h = _QUALITY_HEIGHT.get(quality, 360)
    need_60fps = "60" in quality

    # Video-only adaptive formats — keyed by qualityLabel
    video_formats = [
        f for f in adaptive_formats
        if f.get("type", "").startswith("video/") and f.get("url")
        and _ql_to_height(f.get("qualityLabel", "")) > 0
    ]
    audio_formats = [
        f for f in adaptive_formats
        if f.get("type", "").startswith("audio/") and f.get("url")
    ]

    # Sort by closeness to target height; prefer 60fps if requested
    video_formats.sort(key=lambda f: (
        abs(_ql_to_height(f.get("qualityLabel", "")) - target_h),
        0 if (need_60fps and "60" in (f.get("qualityLabel") or "")) else 1,
    ))
    audio_formats.sort(key=lambda f: -(f.get("bitrate") or 0))

    if video_formats and audio_formats:
        return video_formats[0]["url"], audio_formats[0]["url"]

    # Fall back to progressive (merged) streams
    progressive = sorted(
        [f for f in format_streams if f.get("url")],
        key=lambda f: abs(_ql_to_height(f.get("qualityLabel", "")) - target_h)
    )
    if progressive:
        return progressive[0]["url"], None

    return "", None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _best_thumbnail(thumbnails: list[dict]) -> str:
    """Pick the medium-quality thumbnail (mqdefault equivalent)."""
    if not thumbnails:
        return ""
    # Prefer medium quality
    for quality in ("medium", "high", "maxres", "default", "start", "end"):
        for t in thumbnails:
            if t.get("quality") == quality and t.get("url"):
                return t["url"]
    # Last resort: any thumbnail
    for t in thumbnails:
        if t.get("url"):
            return t["url"]
    return ""


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from a URL."""
    import re
    patterns = [
        r"(?:v=|youtu\.be/|/embed/|/v/|/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # Maybe it's already just an ID
    if len(url) == 11 and re.match(r"^[A-Za-z0-9_-]+$", url):
        return url
    return ""
