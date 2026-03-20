"""
BorgorTube – HLS Session Manager  (concurrency-optimised)

Key changes for multi-user performance:
- start() returns IMMEDIATELY — yt-dlp + ffmpeg launch in a background task
- No blocking wait_ready() in the HTTP request; client polls /status
- Uses the shared executor from executor.py so all yt-dlp calls share one pool
- Uses resolve_stream_urls() which reuses extract_formats() cache — zero
  duplicate yt-dlp calls when the user already opened the watch page
- Each session is a completely independent ffmpeg subprocess — N users = N
  parallel ffmpeg processes with no shared state
"""

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from executor import pool
from ytdl import resolve_stream_urls

HLS_ROOT         = "/tmp/borgortube_hls"
HLS_SEGMENT_SECS = 4
HLS_SEGMENT_LL   = 0.5
HLS_LIST_SIZE    = 12
HLS_SESSION_TTL  = 300      # seconds idle before auto-cleanup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

# Prefer H.264 sources — avoids full transcode, just re-mux
FORMAT_MAPPING = {
    "2k":      "bestvideo[height>=1440][vcodec^=avc]+bestaudio/bestvideo[height>=1440]+bestaudio/best",
    "1080p60": "bestvideo[height>=1080][fps>=60][vcodec^=avc]+bestaudio/bestvideo[height>=1080][fps>=60]+bestaudio/best",
    "1080p":   "bestvideo[height>=1080][vcodec^=avc]+bestaudio/bestvideo[height>=1080]+bestaudio/best",
    "720p60":  "bestvideo[height>=720][fps>=60][vcodec^=avc]+bestaudio/bestvideo[height>=720][fps>=60]+bestaudio/best",
    "720p":    "bestvideo[height>=720][height<1080][vcodec^=avc]+bestaudio/bestvideo[height>=720][height<1080]+bestaudio/best",
    "480p":    "bestvideo[height>=480][height<720][vcodec^=avc]+bestaudio/bestvideo[height>=480][height<720]+bestaudio/best",
    "360p":    "bestvideo[height>=360][height<480][vcodec^=avc]+bestaudio/bestvideo[height>=360][height<480]+bestaudio/best",
    "240p":    "bestvideo[height>=240][height<360][vcodec^=avc]+bestaudio/bestvideo[height>=240][height<360]+bestaudio/best",
    "144p":    "bestvideo[height>=144][height<240][vcodec^=avc]+bestaudio/bestvideo[height>=144][height<240]+bestaudio/best",
}


@dataclass
class HLSSession:
    session_id:  str
    video_url:   str
    quality:     str
    low_latency: bool
    output_dir:  str
    process:     Optional[subprocess.Popen] = field(default=None, repr=False)
    started_at:  float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    error:       Optional[str] = None
    # "pending" | "starting" | "running" | "error"
    state:       str = "pending"

    def touch(self):
        self.last_access = time.time()

    def is_expired(self) -> bool:
        return time.time() - self.last_access > HLS_SESSION_TTL

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def segments_ready(self) -> bool:
        try:
            return any(f.endswith(".ts") for f in os.listdir(self.output_dir))
        except FileNotFoundError:
            return False

    def playlist_url(self) -> str:
        return f"/hls/{self.session_id}/index.m3u8"

    def to_dict(self) -> dict:
        return {
            "session_id":   self.session_id,
            "video_url":    self.video_url,
            "quality":      self.quality,
            "low_latency":  self.low_latency,
            "state":        self.state,
            "running":      self.is_running(),
            "ready":        self.segments_ready(),
            "playlist_url": self.playlist_url(),
            "started_at":   self.started_at,
            "error":        self.error,
        }


def build_ffmpeg_args(
    video_url: str,
    audio_url: Optional[str],
    output_dir: str,
    segment_secs: float,
    list_size: int,
) -> list[str]:
    http_headers = f"User-Agent: {USER_AGENT}\r\n"
    args = ["ffmpeg", "-y", "-loglevel", "warning"]

    args += ["-headers", http_headers, "-i", video_url]
    if audio_url:
        args += ["-headers", http_headers, "-i", audio_url]

    if audio_url:
        args += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        args += ["-map", "0:v:0", "-map", "0:a:0?"]

    # Always transcode to H.264 — HLS .ts containers only support H.264 in browsers
    args += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "128k",
    ]

    hls_flags = "delete_segments+append_list"
    args += [
        "-f", "hls",
        "-hls_time", str(segment_secs),
        "-hls_list_size", str(list_size),
        "-hls_flags", hls_flags,
        "-hls_segment_type", "mpegts",
        "-hls_segment_filename", os.path.join(output_dir, "seg_%05d.ts"),
        os.path.join(output_dir, "index.m3u8"),
    ]
    return args


class HLSManager:
    def __init__(self):
        self._sessions: dict[str, HLSSession] = {}
        os.makedirs(HLS_ROOT, exist_ok=True)

    # ── Start: returns immediately, launches in background ───────────────

    async def start(
        self,
        video_url: str,
        quality: str = "720p",
        low_latency: bool = False,
        cookies_file: Optional[str] = None,
    ) -> HLSSession:
        """
        Creates the session and fires off background work immediately.
        Returns to the caller in <1ms — the client polls /status for readiness.
        """
        session_id = str(uuid.uuid4())[:8]
        output_dir = os.path.join(HLS_ROOT, session_id)
        os.makedirs(output_dir, exist_ok=True)

        session = HLSSession(
            session_id=session_id,
            video_url=video_url,
            quality=quality,
            low_latency=low_latency,
            output_dir=output_dir,
            state="starting",
        )
        self._sessions[session_id] = session

        # Fire background task — does NOT block the HTTP response
        asyncio.create_task(
            self._launch_background(session, cookies_file),
            name=f"hls-{session_id}",
        )

        return session

    async def _launch_background(
        self,
        session: HLSSession,
        cookies_file: Optional[str],
    ) -> None:
        """
        Runs in a background asyncio task.
        yt-dlp runs in the shared thread pool so it never blocks the event loop
        or other users' requests.
        """
        loop = asyncio.get_event_loop()
        try:
            v_url, a_url = await loop.run_in_executor(
                pool,  # shared 16-worker pool
                lambda: resolve_stream_urls(
                    session.video_url, session.quality, cookies_file
                ),
            )
        except Exception as e:
            session.error = f"yt-dlp error: {e}"
            session.state = "error"
            return

        if not v_url:
            session.error = "Could not resolve stream URL"
            session.state = "error"
            return

        seg_secs = HLS_SEGMENT_LL if session.low_latency else HLS_SEGMENT_SECS
        ffmpeg_args = build_ffmpeg_args(
            v_url, a_url, session.output_dir, seg_secs, HLS_LIST_SIZE
        )

        try:
            session.process = subprocess.Popen(
                ffmpeg_args,
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(session.output_dir, "ffmpeg.log"), "w"),
            )
            session.state = "running"
        except FileNotFoundError:
            session.error = "ffmpeg not found – install ffmpeg"
            session.state = "error"
        except Exception as e:
            session.error = str(e)
            session.state = "error"

    # ── Poll-based readiness check (non-blocking) ─────────────────────────

    async def wait_ready(self, session_id: str, timeout: float = 20.0) -> bool:
        """Used by the optional /api/hls/start?wait=true path only."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if session.state == "error":
                return False
            if session.segments_ready():
                return True
            await asyncio.sleep(0.3)
        return False

    # ── Stop / cleanup ────────────────────────────────────────────────────

    def stop(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if not session:
            return False
        if session.process:
            try:
                session.process.terminate()
                session.process.wait(timeout=3)
            except Exception:
                try:
                    session.process.kill()
                except Exception:
                    pass
        shutil.rmtree(session.output_dir, ignore_errors=True)
        return True

    def stop_all(self):
        for sid in list(self._sessions.keys()):
            self.stop(sid)

    def cleanup_expired(self):
        for sid in list(self._sessions.keys()):
            if self._sessions[sid].is_expired():
                self.stop(sid)

    def get(self, session_id: str) -> Optional[HLSSession]:
        s = self._sessions.get(session_id)
        if s:
            s.touch()
        return s

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    def get_log(self, session_id: str, tail: int = 30) -> str:
        session = self._sessions.get(session_id)
        if not session:
            return ""
        log_path = os.path.join(session.output_dir, "ffmpeg.log")
        try:
            with open(log_path, "r") as f:
                lines = f.readlines()
            return "".join(lines[-tail:])
        except Exception:
            return ""
