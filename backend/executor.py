"""
BorgorTube – shared thread pool executor

All blocking yt-dlp / ffmpeg / requests calls run on this pool.
Sizing: yt-dlp releases the GIL during network I/O so we can run many
concurrent extractions. 16 workers handles ~10-12 simultaneous users
comfortably without exhausting file descriptors.
"""

import concurrent.futures
import os

# Allow override via env var for low-memory hosts
_WORKERS = int(os.environ.get("BORGORTUBE_WORKERS", "16"))

# Single shared pool — imported by main.py, hls_manager.py, ytdl.py
pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=_WORKERS,
    thread_name_prefix="bt-worker",
)
