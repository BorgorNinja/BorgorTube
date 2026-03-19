"""
BorgorTube – MPV process manager
Wraps mpv launch / kill / IPC socket communication.
Preserves the full original logic from main.py.
"""

import json
import os
import socket
import subprocess
import time
from typing import Any

from ytdl import FORMAT_MAPPING

import platform as _platform
IPC_PATH = r"\\.\pipe\mpvsocket" if _platform.system() == "Windows" else "/tmp/mpvsocket"
LOG_FILE = "mpvlog.txt"


class MPVManager:
    def __init__(self):
        self._process: subprocess.Popen | None = None
        self.current_url: str | None = None
        self.current_quality: str = "360p"
        self.low_latency: bool = False

    # -----------------------------------------------------------------------
    # Launch / kill
    # -----------------------------------------------------------------------

    def launch(
        self,
        url: str,
        quality_label: str = "360p",
        start_time: float = 0.0,
        detached: bool = True,
        low_latency: bool = False,
        force_fullscreen: bool = False,
        wid: str | None = None,
    ) -> None:
        self.kill()
        self.current_url = url
        self.current_quality = quality_label
        self.low_latency = low_latency

        mpv_format = FORMAT_MAPPING.get(quality_label, "best")

        args: list[str] = ["mpv"]

        if low_latency:
            args += [
                "--cache=no",
                "--demuxer-readahead-secs=0",
                "--demuxer-max-bytes=524288",
                "--demuxer-max-back-bytes=131072",
            ]
        else:
            args += [
                "--cache=yes",
                "--cache-secs=30",
                "--demuxer-readahead-secs=10",
            ]

        if start_time > 0:
            args.append(f"--start={start_time}")

        args += [
            "--osc",
            "--demuxer-thread=yes",
            "--hwdec=no",
            f"--ytdl-format={mpv_format}",
            f"--log-file={LOG_FILE}",
            f"--input-ipc-server={IPC_PATH}",  # Windows: named pipe \\.\ pipe\mpvsocket
            "--panscan=1.0",
        ]

        if force_fullscreen:
            args.append("--fullscreen")

        if wid and not detached:
            args.append(f"--wid={wid}")

        args.append(url)

        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def kill(self) -> None:
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

    # -----------------------------------------------------------------------
    # IPC helpers
    # -----------------------------------------------------------------------

    def send_ipc(self, command: dict[str, Any]) -> dict:
        """Send a JSON command to the mpv IPC socket and return the response."""
        if _platform.system() != "Windows" and not os.path.exists(IPC_PATH):
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
        result = self.send_ipc({"command": ["get_property", prop]})
        return result.get("data")

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
            if os.path.exists(IPC_PATH):
                try:
                    return self.get_playback_time()
                except OSError:
                    pass
            time.sleep(delay)
        return 0.0

    def is_fullscreen(self) -> bool:
        val = self.get_property("fullscreen")
        return bool(val)

    def set_fullscreen(self, value: bool) -> dict:
        return self.set_property("fullscreen", value)

    # -----------------------------------------------------------------------
    # Status snapshot
    # -----------------------------------------------------------------------

    def get_status(self) -> dict:
        running = self._process is not None and self._process.poll() is None
        status: dict = {
            "running": running,
            "current_url": self.current_url,
            "current_quality": self.current_quality,
            "low_latency": self.low_latency,
        }
        if running and os.path.exists(IPC_PATH):
            status["time_pos"] = self.get_playback_time()
            status["fullscreen"] = self.is_fullscreen()
            status["paused"] = self.get_property("pause")
            status["duration"] = self.get_property("duration")
            status["volume"] = self.get_property("volume")
        return status
