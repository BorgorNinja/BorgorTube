"""
BorgorTube – MPV process manager

New: MPV capture mode
  mpv plays normally in its window while simultaneously writing its raw
  demuxed stream to a named pipe via --stream-capture. A second ffmpeg
  process reads that pipe and produces HLS segments that the browser
  plays — so the browser shows exactly what mpv is playing, ~2-3s delayed.

  Pipeline:
    YouTube URL → mpv (--stream-capture=/tmp/bt_capture.ts)
                       ↓ raw TS pipe
                  ffmpeg (libx264, HLS)
                       ↓ .m3u8 + .ts segments
                  browser hls.js
"""

import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any, Optional

from ytdl import FORMAT_MAPPING

IS_WINDOWS = platform.system() == "Windows"
IPC_PATH   = r"\\.\pipe\mpvsocket" if IS_WINDOWS else "/tmp/mpvsocket"
LOG_FILE   = "mpvlog.txt"
CAPTURE_PIPE = os.path.join(tempfile.gettempdir(), "bt_capture.ts")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


class MPVManager:
    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._capture_ffmpeg: subprocess.Popen | None = None
        self._capture_hls_dir: str | None = None
        self.current_url: str | None = None
        self.current_quality: str = "360p"
        self.low_latency: bool = False
        self.capture_active: bool = False

    # ── Launch mpv ──────────────────────────────────────────────────────────

    def launch(
        self,
        url: str,
        quality_label: str = "360p",
        start_time: float = 0.0,
        detached: bool = True,
        low_latency: bool = False,
        force_fullscreen: bool = False,
        with_browser_mirror: bool = True,  # NEW: pipe to browser
    ) -> Optional[str]:
        """
        Launch mpv. If with_browser_mirror=True, also starts the ffmpeg
        capture pipeline and returns the HLS playlist path for the browser.
        Returns None if mirror is disabled or ffmpeg is unavailable.
        """
        self.kill()
        self.current_url = url
        self.current_quality = quality_label
        self.low_latency = low_latency

        mpv_format = FORMAT_MAPPING.get(quality_label, "best")

        args: list[str] = ["mpv"]

        if low_latency:
            args += ["--cache=no", "--demuxer-readahead-secs=0",
                     "--demuxer-max-bytes=524288", "--demuxer-max-back-bytes=131072"]
        else:
            args += ["--cache=yes", "--cache-secs=30", "--demuxer-readahead-secs=10"]

        if start_time > 0:
            args.append(f"--start={start_time}")

        args += [
            "--osc",
            "--demuxer-thread=yes",
            "--hwdec=auto-safe",          # use hw decode when available
            f"--ytdl-format={mpv_format}",
            f"--log-file={LOG_FILE}",
            f"--input-ipc-server={IPC_PATH}",
            "--panscan=1.0",
        ]

        if force_fullscreen:
            args.append("--fullscreen")

        # ── Stream capture to pipe ────────────────────────────────────────
        hls_playlist: Optional[str] = None
        if with_browser_mirror and not IS_WINDOWS and shutil.which("ffmpeg"):
            # Create a named pipe for mpv → ffmpeg
            try:
                if os.path.exists(CAPTURE_PIPE):
                    os.remove(CAPTURE_PIPE)
                os.mkfifo(CAPTURE_PIPE)
            except OSError:
                pass  # FIFO creation failed — skip mirror

            if os.path.exists(CAPTURE_PIPE):
                args.append(f"--stream-capture={CAPTURE_PIPE}")
                hls_playlist = self._start_capture_ffmpeg()

        args.append(url)

        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return hls_playlist

    def _start_capture_ffmpeg(self) -> Optional[str]:
        """
        Start an ffmpeg process that reads from the capture FIFO and writes
        low-latency HLS segments that the browser can play.
        Returns the HLS playlist URL path, or None on failure.
        """
        import uuid
        from hls_manager import HLS_ROOT

        session_id = f"mpv_{str(uuid.uuid4())[:8]}"
        out_dir = os.path.join(HLS_ROOT, session_id)
        os.makedirs(out_dir, exist_ok=True)
        self._capture_hls_dir = out_dir

        playlist = os.path.join(out_dir, "index.m3u8")
        seg_pattern = os.path.join(out_dir, "seg_%05d.ts")

        # Low-latency HLS: 1-second segments, zerolatency preset
        ffmpeg_args = [
            "ffmpeg", "-y", "-loglevel", "warning",
            # Read from named pipe — re-open on EOF (mpv may not write immediately)
            "-re", "-i", CAPTURE_PIPE,
            # Transcode to H.264 (pipe may contain any codec mpv received)
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "128k",
            # Low-latency HLS
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "8",
            "-hls_flags", "delete_segments+append_list+omit_endlist",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", seg_pattern,
            playlist,
        ]

        try:
            self._capture_ffmpeg = subprocess.Popen(
                ffmpeg_args,
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(out_dir, "ffmpeg.log"), "w"),
            )
            self.capture_active = True
            return f"/hls/{session_id}/index.m3u8"
        except Exception as e:
            print(f"[mpv] capture ffmpeg failed: {e}")
            self._capture_hls_dir = None
            return None

    # ── Kill ────────────────────────────────────────────────────────────────

    def kill(self) -> None:
        # Kill mpv
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        # Kill capture ffmpeg
        if self._capture_ffmpeg:
            try:
                self._capture_ffmpeg.terminate()
                self._capture_ffmpeg.wait(timeout=2)
            except Exception:
                try:
                    self._capture_ffmpeg.kill()
                except Exception:
                    pass
            self._capture_ffmpeg = None

        # Remove capture FIFO
        try:
            if os.path.exists(CAPTURE_PIPE):
                os.remove(CAPTURE_PIPE)
        except OSError:
            pass

        # Clean up HLS capture directory
        if self._capture_hls_dir and os.path.exists(self._capture_hls_dir):
            shutil.rmtree(self._capture_hls_dir, ignore_errors=True)
        self._capture_hls_dir = None
        self.capture_active = False

    # ── IPC ─────────────────────────────────────────────────────────────────

    def send_ipc(self, command: dict[str, Any]) -> dict:
        if not IS_WINDOWS and not os.path.exists(IPC_PATH):
            return {"error": "mpv IPC socket not found"}
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(IPC_PATH)
            sock.sendall((json.dumps(command) + "\n").encode("utf-8"))
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\n" in chunk:
                    break
            sock.close()
            return json.loads(response.decode("utf-8").strip())
        except Exception as e:
            return {"error": str(e)}

    def get_property(self, prop: str) -> Any:
        return self.send_ipc({"command": ["get_property", prop]}).get("data")

    def set_property(self, prop: str, value: Any) -> dict:
        return self.send_ipc({"command": ["set_property", prop, value]})

    def get_playback_time(self) -> float:
        val = self.get_property("time-pos")
        try:
            return float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def safe_get_playback_time(self, attempts: int = 5, delay: float = 0.2) -> float:
        for _ in range(attempts):
            if IS_WINDOWS or os.path.exists(IPC_PATH):
                try:
                    return self.get_playback_time()
                except OSError:
                    pass
            time.sleep(delay)
        return 0.0

    def is_fullscreen(self) -> bool:
        return bool(self.get_property("fullscreen"))

    def set_fullscreen(self, value: bool) -> dict:
        return self.set_property("fullscreen", value)

    def get_status(self) -> dict:
        running = self._process is not None and self._process.poll() is None
        status: dict = {
            "running": running,
            "current_url": self.current_url,
            "current_quality": self.current_quality,
            "low_latency": self.low_latency,
            "capture_active": self.capture_active and running,
            "capture_hls_dir": self._capture_hls_dir,
        }
        if running and (IS_WINDOWS or os.path.exists(IPC_PATH)):
            status["time_pos"]   = self.get_playback_time()
            status["fullscreen"] = self.is_fullscreen()
            status["paused"]     = self.get_property("pause")
            status["duration"]   = self.get_property("duration")
            status["volume"]     = self.get_property("volume")
            status["title"]      = self.get_property("media-title")
        return status
