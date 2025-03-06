import sys
import os
import json
import asyncio
import platform
import requests
from pyppeteer import launch
import yt_dlp

from PyQt5.QtCore import (
    QProcess, Qt, QThreadPool, QRunnable, pyqtSlot, QObject, pyqtSignal, QSize, QTimer
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QListWidgetItem, QPushButton, QLineEdit, QComboBox, QTextEdit, QProgressBar
)
from PyQt5.QtGui import QPixmap, QIcon, QPalette, QColor

#################################
# Fixed Chromium 132 User Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"

#################################
# Bucket Labels and Format Mapping
# These strings force mpv to merge video and audio streams at or above a minimum resolution.
FORMAT_MAPPING = {
    "2k":       "bestvideo[height>=1440]+bestaudio/best",
    "1080p60":  "bestvideo[height>=1080][fps>=60]+bestaudio/best",
    "1080p":    "bestvideo[height>=1080]+bestaudio/best",
    "720p60":   "bestvideo[height>=720][fps>=60]+bestaudio/best",
    "720p":     "bestvideo[height>=720][height<1080]+bestaudio/best",
    "360p":     "bestvideo[height>=360][height<720]+bestaudio/best",
    "240p":     "bestvideo[height>=240][height<360]+bestaudio/best",
    "144p":     "bestvideo[height>=144][height<240]+bestaudio/best",
}
BUCKET_LABELS = [
    "2k",
    "1080p60",
    "1080p",
    "720p60",
    "720p",
    "360p",
    "240p",
    "144p"
]

SETTINGS_FILE = "settings.json"
LOG_FILE = "mpvlog.txt"

#################################
# available_buckets function
def available_buckets(info):
    """
    Given the yt-dlp info dict, return a list of bucket labels that are available.
    Unlike before, we do not filter out video-only streams.
    We simply check if any format (combined or video-only) meets the criteria.
    """
    formats = info.get("formats", [])
    bucket_avail = {}
    for f in formats:
        h = f.get("height") or 0
        fps = f.get("fps") or 0
        # We ignore tbr here and just check resolution (and fps for certain buckets).
        for label in BUCKET_LABELS:
            if label == "2k" and h >= 1440:
                bucket_avail[label] = True
            elif label == "1080p60" and h >= 1080 and fps >= 60:
                bucket_avail[label] = True
            elif label == "1080p" and h >= 1080:
                bucket_avail[label] = True
            elif label == "720p60" and h >= 720 and fps >= 60:
                bucket_avail[label] = True
            elif label == "720p" and h >= 720 and h < 1080:
                bucket_avail[label] = True
            elif label == "360p" and h >= 360 and h < 720:
                bucket_avail[label] = True
            elif label == "240p" and h >= 240 and h < 360:
                bucket_avail[label] = True
            elif label == "144p" and h >= 144 and h < 240:
                bucket_avail[label] = True
    # Return available buckets in the order defined in BUCKET_LABELS.
    return [label for label in BUCKET_LABELS if label in bucket_avail]

#################################
# Cookie Fallback Functions

async def get_cookies_headless(video_url):
    print("Launching headless Chromium for cookie extraction...")
    browser = await launch(headless=True, args=['--no-sandbox'])
    page = await browser.newPage()
    await page.setUserAgent(USER_AGENT)
    await page.goto(video_url, {'waitUntil': 'networkidle2'})
    await asyncio.sleep(3)
    cookies = await page.cookies()
    await browser.close()
    return cookies

def save_cookies_to_file(cookies, filename="cookies.txt"):
    with open(filename, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c.get("domain", "")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure", False) else "FALSE"
            expiry = str(c.get("expires", 0))
            name = c.get("name", "")
            value = c.get("value", "")
            f.write("\t".join([domain, flag, path, secure, expiry, name, value]) + "\n")
    print("Cookies saved to", filename)
    return filename

#################################
# Searching and Extraction

def search_youtube(query, max_results=20):
    expr = f"ytsearch{max_results}:{query}"
    opts = {
        "quiet": True,
        "dump_single_json": True,
        "extract_flat": True,
        "http_headers": {"User-Agent": USER_AGENT}
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
            results.append({
                "title": entry.get("title", "Unknown"),
                "videoId": entry["url"],
                "thumbnail": thumb
            })
        return results

def extract_formats(video_url, cookies_file=None):
    opts = {
        "quiet": True,
        "skip_download": True,
        "dump_single_json": True,
        "http_headers": {"User-Agent": USER_AGENT}
    }
    if cookies_file:
        opts["cookies"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info

#################################
# Asynchronous Worker

class WorkerSignals(QObject):
    finished = pyqtSignal(object)

class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
    @pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.finished.emit(e)

#################################
# Main Application Window

class YouTubeClient(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube Client")
        self.setGeometry(100, 100, 1200, 800)
        self.threadpool = QThreadPool()
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)

        # Left layout
        self.left_layout = QVBoxLayout()
        self.main_layout.addLayout(self.left_layout, 2)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Search YouTube")
        self.left_layout.addWidget(self.url_input)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.start_search)
        self.left_layout.addWidget(self.search_button)

        self.video_list = QListWidget()
        self.video_list.setIconSize(QSize(320, 180))
        self.video_list.itemClicked.connect(self.on_video_clicked)
        self.left_layout.addWidget(self.video_list)

        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.left_layout.addWidget(self.console_output)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.left_layout.addWidget(self.progress_bar)

        # Quality selector (populated dynamically)
        self.quality_combo = QComboBox()
        self.left_layout.addWidget(self.quality_combo)

        self.watch_button = QPushButton("Watch")
        self.watch_button.setVisible(False)
        self.watch_button.clicked.connect(self.watch_video)
        self.left_layout.addWidget(self.watch_button)

        # Button for separate streams mode
        self.watch_separate_button = QPushButton("Watch Separate Streams")
        self.watch_separate_button.setVisible(False)
        self.watch_separate_button.clicked.connect(self.watch_separate_streams)
        self.left_layout.addWidget(self.watch_separate_button)

        self.add_to_playlist_button = QPushButton("Add to Playlist")
        self.add_to_playlist_button.clicked.connect(self.add_to_playlist)
        self.left_layout.addWidget(self.add_to_playlist_button)

        self.play_playlist_button = QPushButton("Play Playlist")
        self.play_playlist_button.clicked.connect(self.play_playlist)
        self.left_layout.addWidget(self.play_playlist_button)

        self.dark_mode_button = QPushButton("Toggle Dark Mode")
        self.dark_mode_button.clicked.connect(self.toggle_dark_mode)
        self.left_layout.addWidget(self.dark_mode_button)

        self.attach_detach_button = QPushButton("Detach Video")
        self.attach_detach_button.clicked.connect(self.toggle_attach_detach)
        self.left_layout.addWidget(self.attach_detach_button)

        # Right layout
        self.right_layout = QVBoxLayout()
        self.main_layout.addLayout(self.right_layout, 3)

        self.mpv_widget = QWidget()
        self.mpv_widget.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self.mpv_widget.setAttribute(Qt.WA_NativeWindow, True)
        self.mpv_widget.setMinimumSize(640, 360)
        self.right_layout.addWidget(self.mpv_widget)

        self.media_controls_layout = QHBoxLayout()
        self.play_pause_button = QPushButton("Play/Pause")
        self.play_pause_button.clicked.connect(self.play_pause_video)
        self.media_controls_layout.addWidget(self.play_pause_button)

        self.fullscreen_button = QPushButton("Fullscreen")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        self.media_controls_layout.addWidget(self.fullscreen_button)

        self.fast_forward_button = QPushButton("Fast Forward")
        self.fast_forward_button.clicked.connect(self.fast_forward_video)
        self.media_controls_layout.addWidget(self.fast_forward_button)

        self.right_layout.addLayout(self.media_controls_layout)

        # Internal state
        self.search_results = []
        self.cookies_file = None
        self.current_video_url = None
        self.current_info = None  # full info from yt-dlp
        self.bucketed_formats = []  # list of available bucket labels
        self.player_process = None
        self.video_process = None
        self.audio_process = None
        self.is_detached = False
        self.playlist = []

        self.url_input.returnPressed.connect(self.search_button.click)
        self.load_quality_settings()

        # Timer for synchronizing separate streams (stub)
        self.sync_timer = QTimer()
        self.sync_timer.timeout.connect(self.check_sync)

    def load_quality_settings(self):
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r") as f:
                pass

    def save_quality_settings(self):
        pass

    ############################
    # Searching

    def start_search(self):
        query = self.url_input.text().strip()
        if not query:
            self.console_output.append("Please enter a search query.")
            return
        self.console_output.append(f"Searching for: {query} ...")
        self.video_list.clear()
        self.search_results.clear()
        worker = Worker(search_youtube, query, max_results=20)
        worker.signals.finished.connect(self.on_search_done)
        self.threadpool.start(worker)

    def on_search_done(self, result):
        if isinstance(result, Exception):
            self.console_output.append(f"Search error: {result}")
            return
        self.search_results = result
        self.console_output.append(f"Found {len(result)} videos.")
        for i, item in enumerate(result):
            lw_item = QListWidgetItem(item["title"])
            lw_item.setData(Qt.UserRole, item)
            self.video_list.addItem(lw_item)
            thumb_url = item.get("thumbnail")
            if thumb_url:
                w = Worker(self.fetch_thumbnail, thumb_url, i)
                w.signals.finished.connect(self.on_thumbnail_fetched)
                self.threadpool.start(w)

    def fetch_thumbnail(self, url, index):
        resp = requests.get(url, timeout=5, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return (index, resp.content)

    def on_thumbnail_fetched(self, result):
        if isinstance(result, Exception):
            self.console_output.append(f"Thumbnail error: {result}")
            return
        index, content = result
        if index < 0 or index >= self.video_list.count():
            return
        item = self.video_list.item(index)
        pix = QPixmap()
        pix.loadFromData(content)
        pix = pix.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        icon = QIcon(pix)
        item.setIcon(icon)
        item.setSizeHint(QSize(340, 200))

    ############################
    # Video Selection & Dynamic Quality

    def on_video_clicked(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        self.console_output.append(f"Selected video: {data['title']}")
        self.current_video_url = data["videoId"]
        if not self.current_video_url.startswith("http"):
            self.current_video_url = f"https://www.youtube.com/watch?v={self.current_video_url}"
        worker = Worker(self.extract_with_fallback_bg, self.current_video_url)
        worker.signals.finished.connect(self.on_extraction_done)
        self.threadpool.start(worker)

    def extract_with_fallback_bg(self, video_url):
        try:
            info = extract_formats(video_url, cookies_file=None)
            return ("no_cookies", info)
        except Exception as e:
            if not os.path.exists("cookies.txt"):
                cookies = asyncio.run(get_cookies_headless(video_url))
                save_cookies_to_file(cookies, "cookies.txt")
            info2 = extract_formats(video_url, cookies_file="cookies.txt")
            return ("cookies", info2)

    def on_extraction_done(self, result):
        if isinstance(result, Exception):
            self.console_output.append(f"Extraction error: {result}")
            return
        mode, info = result
        self.current_info = info
        if mode == "no_cookies":
            self.console_output.append("Extraction succeeded without cookies.")
        else:
            self.console_output.append("Extraction succeeded with cookies fallback.")
        # Dynamically determine available quality buckets.
        available = available_buckets(info)
        self.bucketed_formats = available  # list of labels
        self.quality_combo.clear()
        for label in available:
            self.quality_combo.addItem(label)
        if self.quality_combo.count() > 0:
            self.quality_combo.setCurrentIndex(0)
        self.watch_button.setVisible(True)
        if self.has_separate_streams(info):
            self.watch_separate_button.setVisible(True)
        else:
            self.watch_separate_button.setVisible(False)
        self.console_output.append("Quality options updated: " + ", ".join(available))

    def has_separate_streams(self, info):
        video_only = False
        audio_only = False
        for fmt in info.get("formats", []):
            if fmt.get("acodec") == "none":
                video_only = True
            if fmt.get("vcodec") == "none":
                audio_only = True
        return video_only and audio_only

    ############################
    # Playback (Merged Mode)

    def watch_video(self):
        if not self.current_video_url:
            self.console_output.append("No video selected.")
            return
        selected_label = self.quality_combo.currentText()
        fmt_string = FORMAT_MAPPING.get(selected_label, "best")
        mpv_args = [
            "--osc",
            "--cache=yes",
            "--demuxer-thread=yes",
            f"--ytdl-format={fmt_string}",
            f"--log-file={LOG_FILE}",
            "--msg-level=all=v",
            "--input-ipc-server=/tmp/mpvsocket",
            self.current_video_url
        ]
        if not self.is_detached:
            wid = str(int(self.mpv_widget.winId()))
            mpv_args.insert(0, f"--wid={wid}")
        self.kill_mpv()
        self.console_output.append(f"Launching merged mpv with quality '{selected_label}', format='{fmt_string}' ...")
        self.console_output.append(f"mpv logs will be in {LOG_FILE}")
        self.player_process = QProcess(self)
        self.player_process.start("mpv", mpv_args)
        if self.is_detached:
            self.console_output.append("Playback started in detached mode (merged).")
        else:
            self.console_output.append("Playback started embedded (merged).")

    ############################
    # Playback (Separate Streams Mode)

    def watch_separate_streams(self):
        if not self.current_info:
            self.console_output.append("No video info available for separate streams.")
            return
        video_only_url = None
        audio_only_url = None
        for fmt in self.current_info.get("formats", []):
            if fmt.get("acodec") == "none" and not video_only_url:
                video_only_url = fmt.get("url")
            if fmt.get("vcodec") == "none" and not audio_only_url:
                audio_only_url = fmt.get("url")
        if not video_only_url or not audio_only_url:
            self.console_output.append("Separate video-only and audio-only streams not available; falling back to merged mode.")
            self.watch_video()
            return
        video_ipc = "/tmp/mpv_video"
        audio_ipc = "/tmp/mpv_audio"
        video_args = [
            "--no-audio",
            "--osc",
            "--cache=yes",
            "--demuxer-thread=yes",
            f"--input-ipc-server={video_ipc}",
            video_only_url
        ]
        audio_args = [
            "--no-video",
            "--osc",
            "--cache=yes",
            "--demuxer-thread=yes",
            f"--input-ipc-server={audio_ipc}",
            audio_only_url
        ]
        if not self.is_detached:
            wid = str(int(self.mpv_widget.winId()))
            video_args.insert(0, f"--wid={wid}")
        self.kill_mpv()
        self.console_output.append("Launching separate mpv processes for video and audio streams...")
        self.video_process = QProcess(self)
        self.video_process.start("mpv", video_args)
        self.audio_process = QProcess(self)
        self.audio_process.start("mpv", audio_args)
        self.console_output.append("Separate streams launched.")
        self.sync_timer.start(1000)

    def check_sync(self):
        self.console_output.append("Sync check (stub): ensure video and audio are in sync.")

    ############################
    # Kill mpv Processes

    def kill_mpv(self):
        os_type = platform.system()
        if os_type == "Windows":
            os.system("taskkill /F /IM mpv.exe")
        elif os_type == "Linux":
            os.system("pkill mpv")
        elif os_type == "Darwin":
            os.system("pkill mpv")
        else:
            self.console_output.append("Unsupported OS for killing mpv.")

    def toggle_attach_detach(self):
        self.is_detached = not self.is_detached
        if self.is_detached:
            self.attach_detach_button.setText("Attach Video")
            self.console_output.append("Video will now be detached.")
        else:
            self.attach_detach_button.setText("Detach Video")
            self.console_output.append("Video will now be embedded.")
        if self.current_video_url:
            self.watch_video()

    ############################
    # Playlist

    def add_to_playlist(self):
        if self.current_video_url:
            self.playlist.append(self.current_video_url)
            self.console_output.append(f"Added to playlist: {self.current_video_url}")

    def play_playlist(self):
        if not self.playlist:
            self.console_output.append("Playlist is empty.")
            return
        next_url = self.playlist.pop(0)
        self.console_output.append(f"Playing from playlist: {next_url}")
        self.current_video_url = next_url
        worker = Worker(self.extract_with_fallback_bg, self.current_video_url)
        worker.signals.finished.connect(self.on_extraction_done)
        self.threadpool.start(worker)

    ############################
    # Basic UI Controls

    def toggle_dark_mode(self):
        palette = self.palette()
        if palette.color(QPalette.Window) == QColor(255, 255, 255):
            palette.setColor(QPalette.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
            self.console_output.setStyleSheet("background-color: #2c2c2c; color: white;")
        else:
            palette.setColor(QPalette.Window, QColor(255, 255, 255))
            palette.setColor(QPalette.WindowText, QColor(0, 0, 0))
            self.console_output.setStyleSheet("background-color: white; color: black;")
        self.setPalette(palette)

    def play_pause_video(self):
        self.console_output.append("Play/Pause not wired (use mpv keybindings).")

    def toggle_fullscreen(self):
        self.console_output.append("Fullscreen not wired (use mpv keybindings).")

    def fast_forward_video(self):
        self.console_output.append("Fast Forward not wired (use mpv keybindings).")

def main():
    app = QApplication(sys.argv)
    client = YouTubeClient()
    client.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
