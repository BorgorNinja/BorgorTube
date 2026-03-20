"""
BorgorTube – Prefetch manager

When the browser reports a 'hover' event on a video card, the frontend
calls POST /api/prefetch with the video URL. This module immediately starts
resolving stream URLs and warming the HLS transcoder in the background —
so by the time the user actually clicks, everything is already ready.

FreeTube does this via its 'preload' setting. We go further: we actually
start the ffmpeg HLS session so the first segment is ready within the
normal ffmpeg startup window (~3-4s) before the user even clicks.
"""

import asyncio
import time
from typing import TYPE_CHECKING

from executor import pool
from ytdl import extract_formats, resolve_stream_urls
from invidious import extract_video_id, get_video_info

if TYPE_CHECKING:
    from hls_manager import HLSManager

# How long to keep a prefetch result alive (seconds)
PREFETCH_TTL = 120

# Max concurrent prefetch operations (don't hammer yt-dlp)
MAX_CONCURRENT = 4


class PrefetchManager:
    def __init__(self):
        self._cache: dict[str, dict] = {}  # url → {info, fetched_at, hls_session_id}
        self._in_flight: set[str] = set()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._hls: "HLSManager | None" = None

    def set_hls_manager(self, hls: "HLSManager") -> None:
        self._hls = hls

    # ── Public API ─────────────────────────────────────────────────────────

    def get_cached(self, url: str) -> dict | None:
        entry = self._cache.get(url)
        if entry and time.time() - entry["fetched_at"] < PREFETCH_TTL:
            return entry
        return None

    def get_hls_session_id(self, url: str) -> str | None:
        entry = self.get_cached(url)
        return entry.get("hls_session_id") if entry else None

    async def prefetch(
        self,
        url: str,
        quality: str = "720p",
        start_hls: bool = True,
    ) -> None:
        """Fire-and-forget: resolve metadata + optionally warm HLS."""
        if url in self._in_flight or url in self._cache:
            return  # already in progress or cached
        self._in_flight.add(url)
        asyncio.create_task(
            self._do_prefetch(url, quality, start_hls),
            name=f"prefetch-{url[-11:]}",
        )

    async def _do_prefetch(
        self,
        url: str,
        quality: str,
        start_hls: bool,
    ) -> None:
        async with self._semaphore:
            try:
                loop = asyncio.get_event_loop()

                # 1. Try fast path: Invidious metadata (no yt-dlp)
                video_id = extract_video_id(url)
                info = None
                if video_id:
                    info = await loop.run_in_executor(
                        pool, lambda: get_video_info(video_id)
                    )

                # 2. Fallback: yt-dlp (slower but authoritative)
                if not info:
                    info = await loop.run_in_executor(
                        pool, lambda: extract_formats(url)
                    )

                entry: dict = {"info": info, "fetched_at": time.time(), "hls_session_id": None}
                self._cache[url] = entry

                # 3. Optionally pre-warm the HLS transcoder
                if start_hls and self._hls and info:
                    session = await self._hls.start(url, quality=quality)
                    entry["hls_session_id"] = session.session_id

            except Exception as e:
                print(f"[prefetch] error for {url[-20:]}: {e}")
            finally:
                self._in_flight.discard(url)

    def evict_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._cache.items()
                   if now - v["fetched_at"] > PREFETCH_TTL]
        for k in expired:
            del self._cache[k]
