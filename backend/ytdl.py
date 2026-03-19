"""
BorgorTube – yt-dlp utilities

Key design decisions for concurrent multi-user performance:
- Shared extraction cache keyed by (url, quality) — yt-dlp only runs once
  per unique video+quality combination across ALL users
- Cache entries are invalidated if formats list is empty (failed extraction)
- All public functions are synchronous; callers use run_in_executor
"""

import json
import os
import threading
from typing import Optional

import requests
import requests_cache
import yt_dlp

requests_cache.install_cache("youtube_cache", expire_after=86400)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "max-age=86400",
})

# iOS player client bypasses YouTube n-challenge (no JS runtime needed)
YTDLP_EXTRACTOR_ARGS = {"youtube": {"player_client": ["web"]}}

FORMAT_MAPPING: dict[str, str] = {
    "2k":      "bestvideo[height>=1440][vcodec^=avc]+bestaudio/bestvideo[height>=1440]+bestaudio/best",
    "1080p60": "bestvideo[height>=1080][fps>=60][vcodec^=avc]+bestaudio/bestvideo[height>=1080][fps>=60]+bestaudio/best",
    "1080p":   "bestvideo[height>=1080][vcodec^=avc]+bestaudio/bestvideo[height>=1080]+bestaudio/best",
    "720p60":  "bestvideo[height>=720][fps>=60][vcodec^=avc]+bestaudio/bestvideo[height>=720][fps>=60]+bestaudio/best",
    "720p":    "bestvideo[height>=720][height<1080][vcodec^=avc]+bestaudio/bestvideo[height>=720][height<1080]+bestaudio/best",
    "360p":    "bestvideo[height>=360][height<720][vcodec^=avc]+bestaudio/bestvideo[height>=360][height<720]+bestaudio/best",
    "240p":    "bestvideo[height>=240][height<360][vcodec^=avc]+bestaudio/bestvideo[height>=240][height<360]+bestaudio/best",
    "144p":    "bestvideo[height>=144][height<240][vcodec^=avc]+bestaudio/bestvideo[height>=144][height<240]+bestaudio/best",
}
ALL_QUALITIES = list(FORMAT_MAPPING.keys())

SEARCH_HISTORY_FILE = "search_history.json"

# ---------------------------------------------------------------------------
# Thread-safe in-memory caches
# ---------------------------------------------------------------------------

_lock = threading.Lock()
search_cache:        dict = {}
extraction_cache:    dict = {}   # (url, cookies_file) → full info dict
stream_url_cache:    dict = {}   # (url, quality)      → (v_url, a_url)
channel_videos_cache: dict = {}


def _cache_get(cache: dict, key) -> Optional[object]:
    with _lock:
        return cache.get(key)


def _cache_set(cache: dict, key, value) -> None:
    with _lock:
        cache[key] = value


# ---------------------------------------------------------------------------
# Search history
# ---------------------------------------------------------------------------

def load_search_history() -> dict:
    if os.path.exists(SEARCH_HISTORY_FILE):
        with open(SEARCH_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"queries": []}


def save_search_history(query: str) -> None:
    hist = load_search_history()
    hist["queries"].append(query)
    hist["queries"] = hist["queries"][-50:]
    with open(SEARCH_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_low_res_thumbnail(url: str) -> str:
    if "maxresdefault" in url:
        return url.replace("maxresdefault", "mqdefault")
    return url


def available_buckets(info: dict) -> list[str]:
    formats = info.get("formats", [])
    bucket_avail: set[str] = set()
    for f in formats:
        h = f.get("height") or 0
        fps = f.get("fps") or 0
        if h >= 1440: bucket_avail.add("2k")
        if h >= 1080 and fps >= 60: bucket_avail.add("1080p60")
        if h >= 1080: bucket_avail.add("1080p")
        if h >= 720 and fps >= 60: bucket_avail.add("720p60")
        if h >= 720 and h < 1080: bucket_avail.add("720p")
        if h >= 360 and h < 720: bucket_avail.add("360p")
        if h >= 240 and h < 360: bucket_avail.add("240p")
        if h >= 144 and h < 240: bucket_avail.add("144p")
    result = [q for q in ALL_QUALITIES if q in bucket_avail]
    return result if result else ["360p"]


# ---------------------------------------------------------------------------
# Core yt-dlp wrappers  (all synchronous — run via executor)
# ---------------------------------------------------------------------------

def search_youtube(query: str, max_results: int = 20) -> list[dict]:
    cache_key = (query, max_results)
    cached = _cache_get(search_cache, cache_key)
    if cached is not None:
        return cached

    opts = {
        "quiet": True,
        "dump_single_json": True,
        "extract_flat": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 10,
        "extractor_args": YTDLP_EXTRACTOR_ARGS,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)

    results = []
    for entry in data.get("entries", []):
        if not entry.get("url"):
            continue
        thumb = ""
        if entry.get("thumbnails"):
            thumb = entry["thumbnails"][-1]["url"]
        elif entry.get("thumbnail"):
            thumb = entry["thumbnail"]
        results.append({
            "title":      entry.get("title", "Unknown"),
            "videoId":    entry["url"],
            "thumbnail":  thumb,
            "duration":   entry.get("duration"),
            "uploader":   entry.get("uploader") or entry.get("channel") or "",
            "view_count": entry.get("view_count"),
        })

    _cache_set(search_cache, cache_key, results)
    return results


def extract_formats(video_url: str, cookies_file: Optional[str] = None) -> dict:
    """
    Full video extraction. Result is shared across all users requesting the
    same URL — yt-dlp only runs once per unique video regardless of concurrency.
    """
    cache_key = (video_url, cookies_file)
    cached = _cache_get(extraction_cache, cache_key)
    if cached is not None:
        return cached

    opts = {
        "quiet": True,
        "skip_download": True,
        "dump_single_json": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 10,
        "extractor_args": YTDLP_EXTRACTOR_ARGS,
    }
    if cookies_file:
        opts["cookies"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    # Only cache if we got actual playable formats
    if info and info.get("formats"):
        _cache_set(extraction_cache, cache_key, info)
    return info


def resolve_stream_urls(
    video_url: str,
    quality: str,
    cookies_file: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    Resolve the best video+audio URLs for a given quality.

    OPTIMISATION: reuses extraction_cache from extract_formats so that
    /api/video and /api/hls/start for the same video share a single
    yt-dlp call instead of running it twice.
    """
    cache_key = (video_url, quality)
    cached = _cache_get(stream_url_cache, cache_key)
    if cached is not None:
        return cached

    fmt_str = FORMAT_MAPPING.get(quality, "best")

    # Reuse the full extraction cache if available (avoids second yt-dlp call)
    full_key = (video_url, cookies_file)
    info = _cache_get(extraction_cache, full_key)

    if info is None:
        opts = {
            "quiet": True,
            "skip_download": True,
            "dump_single_json": True,
            "http_headers": {"User-Agent": USER_AGENT},
            "socket_timeout": 10,
            "format": fmt_str,
            "extractor_args": YTDLP_EXTRACTOR_ARGS,
        }
        if cookies_file:
            opts["cookies"] = cookies_file
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
        if info and info.get("formats"):
            _cache_set(extraction_cache, full_key, info)

    # Extract video+audio URLs from formats list using quality string
    formats = info.get("formats", [])
    v_url: str = ""
    a_url: Optional[str] = None

    # Try to find matching formats by quality bucket
    target_h = _quality_to_height(quality)
    video_formats = sorted(
        [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")],
        key=lambda f: abs((f.get("height") or 0) - target_h)
    )
    audio_formats = sorted(
        [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")],
        key=lambda f: -(f.get("tbr") or 0)
    )

    if video_formats:
        v_url = video_formats[0]["url"]
    if audio_formats:
        a_url = audio_formats[0]["url"]

    # Fallback: use requested_formats if available
    if not v_url:
        requested = info.get("requested_formats") or []
        for f in requested:
            if f.get("vcodec") not in (None, "none") and not v_url:
                v_url = f.get("url", "")
            if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none") and not a_url:
                a_url = f.get("url")

    # Last resort: top-level url
    if not v_url:
        v_url = info.get("url", "")

    result = (v_url, a_url)
    if v_url:
        _cache_set(stream_url_cache, cache_key, result)
    return result


def _quality_to_height(quality: str) -> int:
    mapping = {"2k": 1440, "1080p60": 1080, "1080p": 1080,
               "720p60": 720, "720p": 720, "360p": 360,
               "240p": 240, "144p": 144}
    return mapping.get(quality, 360)


def get_channel_videos(channel_url: str, max_results: int = 20) -> list[dict]:
    if not channel_url:
        return []
    cache_key = (channel_url, max_results)
    cached = _cache_get(channel_videos_cache, cache_key)
    if cached is not None:
        return cached

    if ("youtube.com/@" in channel_url or "youtube.com/channel/" in channel_url) \
            and "/videos" not in channel_url:
        channel_url += "/videos"

    opts = {
        "quiet": True,
        "dump_single_json": True,
        "extract_flat": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 10,
        "extractor_args": YTDLP_EXTRACTOR_ARGS,
    }
    results: list[dict] = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(channel_url, download=False)
            for entry in data.get("entries", [])[:max_results]:
                if not entry.get("url"):
                    continue
                thumb = ""
                if entry.get("thumbnails"):
                    thumb = entry["thumbnails"][-1]["url"]
                elif entry.get("thumbnail"):
                    thumb = entry["thumbnail"]
                results.append({
                    "title":    entry.get("title", "Unknown"),
                    "videoId":  entry["url"],
                    "thumbnail": thumb,
                    "duration": entry.get("duration"),
                })
    except Exception as e:
        print("get_channel_videos error:", e)

    _cache_set(channel_videos_cache, cache_key, results)
    return results
