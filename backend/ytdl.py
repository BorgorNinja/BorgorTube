"""
BorgorTube – yt-dlp utilities
All search, extraction, channel, and format logic from the original main.py.
"""

import json
import os

import requests
import requests_cache
import yt_dlp

# Enable disk caching for HTTP requests (expires after 1 day)
requests_cache.install_cache("youtube_cache", expire_after=86400)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

session = requests.Session()

# ---------------------------------------------------------------------------
# Shared yt-dlp extractor args
# ---------------------------------------------------------------------------
# Use the iOS player client to bypass YouTube's "n challenge" throttling.
# The n challenge requires a JS runtime (Deno/Node) to solve; the iOS client
# API never triggers it, giving full-speed streams at all qualities.
# "web" is kept as second fallback for metadata that iOS may omit.
YTDLP_EXTRACTOR_ARGS = {
    "youtube": {"player_client": ["web"]}
}
session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "max-age=86400",
    }
)

FORMAT_MAPPING: dict[str, str] = {
    "2k":       "bestvideo[height>=1440]+bestaudio/best",
    "1080p60":  "bestvideo[height>=1080][fps>=60]+bestaudio/best",
    "1080p":    "bestvideo[height>=1080]+bestaudio/best",
    "720p60":   "bestvideo[height>=720][fps>=60]+bestaudio/best",
    "720p":     "bestvideo[height>=720][height<1080]+bestaudio/best",
    "360p":     "bestvideo[height>=360][height<720]+bestaudio/best",
    "240p":     "bestvideo[height>=240][height<360]+bestaudio/best",
    "144p":     "bestvideo[height>=144][height<240]+bestaudio/best",
}
ALL_QUALITIES = list(FORMAT_MAPPING.keys())

SEARCH_HISTORY_FILE = "search_history.json"

# In-memory caches
# Note: extraction_cache holds resolved stream URLs which expire after ~6h.
# The cache is intentionally in-memory only (cleared on server restart).
search_cache: dict = {}
extraction_cache: dict = {}
channel_videos_cache: dict = {}


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
# Thumbnail helpers
# ---------------------------------------------------------------------------

def get_low_res_thumbnail(url: str) -> str:
    if "maxresdefault" in url:
        return url.replace("maxresdefault", "mqdefault")
    return url


# ---------------------------------------------------------------------------
# Format / quality helpers
# ---------------------------------------------------------------------------

def available_buckets(info: dict) -> list[str]:
    formats = info.get("formats", [])
    bucket_avail: set[str] = set()
    for f in formats:
        h = f.get("height") or 0
        fps = f.get("fps") or 0
        if h >= 1440:
            bucket_avail.add("2k")
        if h >= 1080 and fps >= 60:
            bucket_avail.add("1080p60")
        if h >= 1080:
            bucket_avail.add("1080p")
        if h >= 720 and fps >= 60:
            bucket_avail.add("720p60")
        if h >= 720 and h < 1080:
            bucket_avail.add("720p")
        if h >= 360 and h < 720:
            bucket_avail.add("360p")
        if h >= 240 and h < 360:
            bucket_avail.add("240p")
        if h >= 144 and h < 240:
            bucket_avail.add("144p")
    result = [q for q in ALL_QUALITIES if q in bucket_avail]
    return result if result else ["360p"]


# ---------------------------------------------------------------------------
# Core yt-dlp wrappers
# ---------------------------------------------------------------------------

def search_youtube(query: str, max_results: int = 20) -> list[dict]:
    cache_key = (query, max_results)
    if cache_key in search_cache:
        return search_cache[cache_key]
    expr = f"ytsearch{max_results}:{query}"
    opts = {
        "quiet": True,
        "dump_single_json": True,
        "extract_flat": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 5,
        "extractor_args": YTDLP_EXTRACTOR_ARGS,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(expr, download=False)
        results = []
        for entry in data.get("entries", []):
            if not entry.get("url"):
                continue
            thumb = ""
            if entry.get("thumbnails"):
                thumb = entry["thumbnails"][-1]["url"]
            elif entry.get("thumbnail"):
                thumb = entry["thumbnail"]
            duration = entry.get("duration")
            results.append(
                {
                    "title": entry.get("title", "Unknown"),
                    "videoId": entry["url"],
                    "thumbnail": thumb,
                    "duration": duration,
                    "uploader": entry.get("uploader") or entry.get("channel") or "",
                    "view_count": entry.get("view_count"),
                }
            )
    search_cache[cache_key] = results
    return results


def extract_formats(video_url: str, cookies_file: str | None = None) -> dict:
    cache_key = (video_url, cookies_file)
    if cache_key in extraction_cache:
        return extraction_cache[cache_key]
    opts = {
        "quiet": True,
        "skip_download": True,
        "dump_single_json": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 5,
        "extractor_args": YTDLP_EXTRACTOR_ARGS,
    }
    if cookies_file:
        opts["cookies"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    # Only cache if we actually got formats (don't cache empty/error responses)
    if info and info.get("formats"):
        extraction_cache[cache_key] = info
    return info


def get_channel_videos(channel_url: str, max_results: int = 20) -> list[dict]:
    if not channel_url:
        return []
    cache_key = (channel_url, max_results)
    if cache_key in channel_videos_cache:
        return channel_videos_cache[cache_key]
    if (
        "youtube.com/@" in channel_url or "youtube.com/channel/" in channel_url
    ) and "/videos" not in channel_url:
        channel_url += "/videos"
    opts = {
        "quiet": True,
        "dump_single_json": True,
        "extract_flat": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 5,
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
                results.append(
                    {
                        "title": entry.get("title", "Unknown"),
                        "videoId": entry["url"],
                        "thumbnail": thumb,
                        "duration": entry.get("duration"),
                    }
                )
    except Exception as e:
        print("get_channel_videos error:", e)
    channel_videos_cache[cache_key] = results
    return results
