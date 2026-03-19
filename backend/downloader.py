"""
BorgorTube – Download manager  (Phase 5f)

Runs yt-dlp download jobs in background threads and streams progress
to the browser via Server-Sent Events (SSE).

Endpoints (added to main.py):
  POST /api/download          { url, quality, format }  → { job_id }
  GET  /api/download/progress/{job_id}                  → SSE stream
  GET  /api/download/{job_id}/status                    → JSON snapshot
  DELETE /api/download/{job_id}                         → cancel
"""

import asyncio
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

import yt_dlp

DOWNLOAD_DIR = os.environ.get("BORGORTUBE_DOWNLOADS", os.path.expanduser("~/Downloads/BorgorTube"))

FORMAT_MAPPING = {
    "2k":      "bestvideo[height>=1440]+bestaudio/best",
    "1080p60": "bestvideo[height>=1080][fps>=60]+bestaudio/best",
    "1080p":   "bestvideo[height>=1080]+bestaudio/best",
    "720p60":  "bestvideo[height>=720][fps>=60]+bestaudio/best",
    "720p":    "bestvideo[height>=720][height<1080]+bestaudio/best",
    "360p":    "bestvideo[height>=360][height<720]+bestaudio/best",
    "240p":    "bestvideo[height>=240][height<360]+bestaudio/best",
    "144p":    "bestvideo[height>=144][height<240]+bestaudio/best",
    "audio":   "bestaudio/best",
    "best":    "bestvideo+bestaudio/best",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


# ── Job state ───────────────────────────────────────────────────────────────

@dataclass
class DownloadJob:
    job_id: str
    url: str
    quality: str
    fmt: str                  # "mp4" | "webm" | "mp3" | "best"
    status: str = "queued"    # queued | downloading | merging | done | error | cancelled
    progress: float = 0.0     # 0–100
    speed: str = ""
    eta: str = ""
    filename: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    _events: list[dict] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _cancelled: bool = False

    def push_event(self, event: dict) -> None:
        with self._lock:
            self._events.append({**event, "ts": time.time()})

    def drain_events(self) -> list[dict]:
        with self._lock:
            evs = self._events[:]
            self._events.clear()
            return evs

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "url": self.url,
            "quality": self.quality,
            "format": self.fmt,
            "status": self.status,
            "progress": round(self.progress, 1),
            "speed": self.speed,
            "eta": self.eta,
            "filename": self.filename,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


# ── Download manager ─────────────────────────────────────────────────────────

class DownloadManager:
    def __init__(self):
        self._jobs: dict[str, DownloadJob] = {}
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # ── Start ──────────────────────────────────────────────────────────

    def start(
        self,
        url: str,
        quality: str = "720p",
        fmt: str = "mp4",
        cookies_file: Optional[str] = None,
    ) -> DownloadJob:
        job_id = str(uuid.uuid4())[:8]
        job = DownloadJob(job_id=job_id, url=url, quality=quality, fmt=fmt)
        self._jobs[job_id] = job

        t = threading.Thread(
            target=self._run,
            args=(job, cookies_file),
            daemon=True,
            name=f"dl-{job_id}",
        )
        job._thread = t
        t.start()
        return job

    # ── Worker ─────────────────────────────────────────────────────────

    def _run(self, job: DownloadJob, cookies_file: Optional[str]) -> None:
        fmt_str = FORMAT_MAPPING.get(job.quality, "best")

        # Merge format with container preference
        postprocessors = []
        if job.fmt == "mp3":
            fmt_str = "bestaudio/best"
            postprocessors = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        elif job.fmt in ("mp4", "webm"):
            postprocessors = [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": job.fmt,
            }]

        def progress_hook(d: dict[str, Any]) -> None:
            if job._cancelled:
                raise Exception("Cancelled by user")

            status = d.get("status", "")
            if status == "downloading":
                job.status = "downloading"
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                job.progress = (downloaded / total * 100) if total else 0
                job.speed = d.get("_speed_str", "").strip()
                job.eta = d.get("_eta_str", "").strip()
                job.push_event({
                    "type": "progress",
                    "progress": job.progress,
                    "speed": job.speed,
                    "eta": job.eta,
                })
            elif status == "finished":
                job.status = "merging"
                job.progress = 99.0
                job.filename = os.path.basename(d.get("filename", ""))
                job.push_event({"type": "merging", "filename": job.filename})
            elif status == "error":
                job.error = str(d.get("error", "unknown error"))
                job.push_event({"type": "error", "error": job.error})

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "format": fmt_str,
            "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
            "http_headers": {"User-Agent": USER_AGENT},
            "socket_timeout": 10,
            "progress_hooks": [progress_hook],
            "postprocessors": postprocessors,
            "merge_output_format": job.fmt if job.fmt in ("mp4", "webm") else None,
        }
        if cookies_file and os.path.exists(cookies_file):
            opts["cookies"] = cookies_file

        try:
            job.status = "downloading"
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([job.url])
            job.status = "done"
            job.progress = 100.0
            job.finished_at = time.time()
            job.push_event({"type": "done", "filename": job.filename})
        except Exception as e:
            if job._cancelled:
                job.status = "cancelled"
                job.push_event({"type": "cancelled"})
            else:
                job.status = "error"
                job.error = str(e)
                job.push_event({"type": "error", "error": job.error})
            job.finished_at = time.time()

    # ── Cancel ─────────────────────────────────────────────────────────

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job._cancelled = True
        return True

    # ── SSE generator ──────────────────────────────────────────────────

    async def event_stream(self, job_id: str) -> AsyncGenerator[str, None]:
        """Yields SSE-formatted text until the job finishes."""
        job = self._jobs.get(job_id)
        if not job:
            yield _sse({"type": "error", "error": "job not found"})
            return

        # Send initial status
        yield _sse({"type": "status", **job.to_dict()})

        terminal = {"done", "error", "cancelled"}
        while job.status not in terminal:
            await asyncio.sleep(0.25)
            for ev in job.drain_events():
                yield _sse(ev)

        # Drain any remaining events
        for ev in job.drain_events():
            yield _sse(ev)

        # Final status
        yield _sse({"type": "final", **job.to_dict()})

    # ── Queries ────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[DownloadJob]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self._jobs.values()]

    def cleanup_finished(self, max_age: float = 3600.0) -> int:
        """Remove completed/failed jobs older than max_age seconds."""
        now = time.time()
        terminal = {"done", "error", "cancelled"}
        to_remove = [
            jid for jid, j in self._jobs.items()
            if j.status in terminal and j.finished_at and (now - j.finished_at) > max_age
        ]
        for jid in to_remove:
            del self._jobs[jid]
        return len(to_remove)


def _sse(data: dict) -> str:
    import json
    return f"data: {json.dumps(data)}\n\n"
