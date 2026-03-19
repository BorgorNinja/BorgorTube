"""
BorgorTube – HLS Session Manager  (Phase 2)

Transcodes yt-dlp stream URLs into HLS segments served locally,
so the browser <video> tag can play any quality (1080p, 4K, etc.)
without requiring mpv pop-out or progressive download.

Architecture:
  yt-dlp (Python) ──► direct stream URLs
  ffmpeg subprocess ──► HLS segments → /tmp/borgortube_hls/{session_id}/
  FastAPI StaticFiles ──► browser fetches /hls/{session_id}/index.m3u8
  hls.js (browser) ──► adaptive playback in <video>

Each HLS session is a unique UUID with its own ffmpeg process and segment dir.
Sessions auto-expire after HLS_SESSION_TTL seconds of inactivity.
"""

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import yt_dlp

HLS_ROOT         = "/tmp/borgortube_hls"
HLS_SEGMENT_SECS = 4        # normal mode: 4-second segments
HLS_SEGMENT_LL   = 0.5      # low-latency mode: 0.5-second segments
HLS_LIST_SIZE    = 12       # number of segments kept in playlist
HLS_SESSION_TTL  = 300      # seconds of inactivity before auto-cleanup

# iOS player client bypasses YouTube n-challenge (no JS runtime needed)
YTDLP_EXTRACTOR_ARGS = {"youtube": {"player_client": ["web"]}}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


# ── Dataclass ─────────────────────────────────────────────────────────────

@dataclass
class HLSSession:
    session_id: str
    video_url: str          # original YouTube / yt-dlp URL
    quality: str            # e.g. "1080p"
    low_latency: bool
    output_dir: str
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    started_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    error: Optional[str] = None

    def touch(self):
        self.last_access = time.time()

    def is_expired(self) -> bool:
        return time.time() - self.last_access > HLS_SESSION_TTL

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def segments_ready(self) -> bool:
        """True once at least one .ts segment is on disk."""
        try:
            return any(
                f.endswith(".ts")
                for f in os.listdir(self.output_dir)
            )
        except FileNotFoundError:
            return False

    def playlist_url(self) -> str:
        return f"/hls/{self.session_id}/index.m3u8"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "video_url": self.video_url,
            "quality": self.quality,
            "low_latency": self.low_latency,
            "running": self.is_running(),
            "ready": self.segments_ready(),
            "playlist_url": self.playlist_url(),
            "started_at": self.started_at,
            "error": self.error,
        }


# ── Format selection ──────────────────────────────────────────────────────

# Prefer H.264 (vcodec^=avc) sources — reduces ffmpeg work since we always
# need H.264 output for HLS .ts browser compatibility.
FORMAT_MAPPING = {
    "2k":      "bestvideo[height>=1440][vcodec^=avc]+bestaudio/bestvideo[height>=1440]+bestaudio/best",
    "1080p60": "bestvideo[height>=1080][fps>=60][vcodec^=avc]+bestaudio/bestvideo[height>=1080][fps>=60]+bestaudio/best",
    "1080p":   "bestvideo[height>=1080][vcodec^=avc]+bestaudio/bestvideo[height>=1080]+bestaudio/best",
    "720p60":  "bestvideo[height>=720][fps>=60][vcodec^=avc]+bestaudio/bestvideo[height>=720][fps>=60]+bestaudio/best",
    "720p":    "bestvideo[height>=720][height<1080][vcodec^=avc]+bestaudio/bestvideo[height>=720][height<1080]+bestaudio/best",
    "360p":    "bestvideo[height>=360][height<720][vcodec^=avc]+bestaudio/bestvideo[height>=360][height<720]+bestaudio/best",
    "240p":    "bestvideo[height>=240][height<360][vcodec^=avc]+bestaudio/bestvideo[height>=240][height<360]+bestaudio/best",
    "144p":    "bestvideo[height>=144][height<240][vcodec^=avc]+bestaudio/bestvideo[height>=144][height<240]+bestaudio/best",
}


def resolve_stream_urls(video_url: str, quality: str) -> tuple[str, Optional[str]]:
    """
    Use yt-dlp to resolve the best video URL (and separate audio URL if split).
    Returns (video_url_or_manifest, audio_url_or_None).
    """
    fmt_str = FORMAT_MAPPING.get(quality, "best")
    opts = {
        "quiet": True,
        "skip_download": True,
        "dump_single_json": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 10,
        "format": fmt_str,
        "extractor_args": YTDLP_EXTRACTOR_ARGS,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    # yt-dlp may merge formats itself and give us a single URL
    requested = info.get("requested_formats")
    if requested and len(requested) >= 2:
        # Split: separate video + audio
        video_f = next((f for f in requested if f.get("vcodec") != "none"), None)
        audio_f = next((f for f in requested if f.get("acodec") != "none" and f.get("vcodec") == "none"), None)
        v_url = video_f["url"] if video_f else info.get("url", "")
        a_url = audio_f["url"] if audio_f else None
        return v_url, a_url
    elif requested and len(requested) == 1:
        return requested[0]["url"], None
    else:
        return info.get("url", ""), None


# ── ffmpeg HLS transcoder ─────────────────────────────────────────────────

def build_ffmpeg_args(
    video_url: str,
    audio_url: Optional[str],
    output_dir: str,
    segment_secs: float,
    list_size: int,
    video_codec: str = "auto",
) -> list[str]:
    """
    Build the ffmpeg command list for HLS segmentation.

    IMPORTANT: HLS .ts segments only support H.264 video.
    YouTube streams are typically VP9 or AV1, so we must transcode
    to H.264 with libx264. Using -c:v copy causes a black video in
    all browsers because VP9/AV1 in .ts is not supported.
    """
    args = ["ffmpeg", "-y", "-loglevel", "warning"]

    # HTTP headers for yt-dlp stream URLs
    http_headers = f"User-Agent: {USER_AGENT}\r\n"

    # Input(s)
    args += ["-headers", http_headers]
    args += ["-i", video_url]

    if audio_url:
        args += ["-headers", http_headers]
        args += ["-i", audio_url]

    # Stream mapping
    if audio_url:
        args += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        args += ["-map", "0:v:0"]
        args += ["-map", "0:a:0?"]

    # Video: always transcode to H.264 – the only codec HLS .ts supports in browsers.
    # -preset fast balances speed vs file size; -crf 23 is visually lossless.
    # -pix_fmt yuv420p ensures broad browser compatibility (some sources are yuv444p).
    args += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]

    # Audio: always encode to AAC for .ts compatibility
    args += [
        "-c:a", "aac",
        "-ac", "2",
        "-ar", "48000",
        "-b:a", "128k",
    ]

    # HLS output
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


# ── Session manager ───────────────────────────────────────────────────────

class HLSManager:
    def __init__(self):
        self._sessions: dict[str, HLSSession] = {}
        os.makedirs(HLS_ROOT, exist_ok=True)

    # ── Create / start session ──────────────────────────────────────────

    async def start(
        self,
        video_url: str,
        quality: str = "720p",
        low_latency: bool = False,
    ) -> HLSSession:
        session_id = str(uuid.uuid4())[:8]
        output_dir = os.path.join(HLS_ROOT, session_id)
        os.makedirs(output_dir, exist_ok=True)

        session = HLSSession(
            session_id=session_id,
            video_url=video_url,
            quality=quality,
            low_latency=low_latency,
            output_dir=output_dir,
        )
        self._sessions[session_id] = session

        # Resolve stream URLs in a thread (yt-dlp is blocking)
        loop = asyncio.get_event_loop()
        try:
            v_url, a_url = await loop.run_in_executor(
                None, lambda: resolve_stream_urls(video_url, quality)
            )
        except Exception as e:
            session.error = f"yt-dlp error: {e}"
            return session

        # Build + launch ffmpeg
        seg_secs = HLS_SEGMENT_LL if low_latency else HLS_SEGMENT_SECS
        ffmpeg_args = build_ffmpeg_args(v_url, a_url, output_dir, seg_secs, HLS_LIST_SIZE)

        try:
            session.process = subprocess.Popen(
                ffmpeg_args,
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(output_dir, "ffmpeg.log"), "w"),
            )
        except FileNotFoundError:
            session.error = "ffmpeg not found – install ffmpeg"
        except Exception as e:
            session.error = str(e)

        return session

    # ── Wait until first segment is ready ──────────────────────────────

    async def wait_ready(self, session_id: str, timeout: float = 20.0) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if session.segments_ready():
                return True
            if not session.is_running():
                return False
            await asyncio.sleep(0.4)
        return False

    # ── Stop / cleanup ──────────────────────────────────────────────────

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
        try:
            shutil.rmtree(session.output_dir, ignore_errors=True)
        except Exception:
            pass
        return True

    def stop_all(self):
        for sid in list(self._sessions.keys()):
            self.stop(sid)

    # ── Auto-expire idle sessions ───────────────────────────────────────

    def cleanup_expired(self):
        for sid in list(self._sessions.keys()):
            s = self._sessions[sid]
            if s.is_expired():
                self.stop(sid)

    # ── Status ──────────────────────────────────────────────────────────

    def get(self, session_id: str) -> Optional[HLSSession]:
        s = self._sessions.get(session_id)
        if s:
            s.touch()
        return s

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    # ── ffmpeg log tail ─────────────────────────────────────────────────

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
