import sys
import os
import json
import asyncio
import platform
import socket
import yt_dlp
from bs4 import BeautifulSoup

from PyQt5.QtCore import (
    QProcess, Qt, QThreadPool, QRunnable, pyqtSlot, QObject, pyqtSignal,
    QSize, QTimer
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QGridLayout, QLineEdit, QPushButton, QLabel, QTextEdit,
    QComboBox, QStackedWidget, QSizePolicy
)
from PyQt5.QtGui import QPixmap, QIcon, QPalette, QColor, QFont

import requests
import requests_cache

# Enable disk caching for HTTP requests (expires after 1 day)
requests_cache.install_cache('youtube_cache', expire_after=86400)

#################################
# Helper functions for mpv IPC fullscreen handling
def get_fullscreen_status(ipc_path="/tmp/mpvsocket"):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(ipc_path)
        cmd = {"command": ["get_property", "fullscreen"]}
        sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\n" in chunk:
                break
        sock.close()
        data = json.loads(response.decode("utf-8").strip())
        if "data" in data:
            return bool(data["data"])
    except Exception as e:
        print("Error getting fullscreen status:", e)
    return False

def set_fullscreen_property(fullscreen, ipc_path="/tmp/mpvsocket"):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(ipc_path)
        cmd = {"command": ["set_property", "fullscreen", fullscreen]}
        sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        sock.close()
    except Exception as e:
        print("Error setting fullscreen property:", e)

#################################
# Hard-coded user agent (Chromium 132)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

#################################
# Global persistent session with compression and caching headers.
session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "max-age=86400"
})

#################################
# Helper to reduce thumbnail resolution (saves bandwidth)
def get_low_res_thumbnail(url):
    if "maxresdefault" in url:
        return url.replace("maxresdefault", "mqdefault")
    return url

#################################
# Format mapping for merged playback
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
ALL_QUALITIES = list(FORMAT_MAPPING.keys())

#################################
# Global caches
search_cache = {}
thumbnail_cache = {}
extraction_cache = {}
channel_videos_cache = {}

SEARCH_HISTORY_FILE = "search_history.json"
SETTINGS_FILE = "settings.json"
LOG_FILE = "mpvlog.txt"

#################################
def load_search_history():
    if os.path.exists(SEARCH_HISTORY_FILE):
        with open(SEARCH_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"queries": []}

def save_search_history(query):
    history = load_search_history()
    history["queries"].append(query)
    history["queries"] = history["queries"][-50:]
    with open(SEARCH_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

#################################
def available_buckets(info):
    formats = info.get("formats", [])
    bucket_avail = set()
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

def get_current_playback_time(ipc_path="/tmp/mpvsocket"):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(ipc_path)
        cmd = {"command": ["get_property", "time-pos"]}
        sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\n" in chunk:
                break
        sock.close()
        data = json.loads(response.decode("utf-8").strip())
        if "data" in data:
            return float(data["data"])
    except Exception as e:
        print("Error getting playback time:", e)
    return 0.0

#################################
# Channel scraping using global session
def scrape_channel_avatar(channel_url):
    if not channel_url:
        return None
    try:
        r = session.get(channel_url, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            return og_image["content"]
    except Exception as e:
        print("scrape_channel_avatar error:", e)
    return None

def get_channel_videos(channel_url, max_results=20):
    if not channel_url:
        return []
    cache_key = (channel_url, max_results)
    if cache_key in channel_videos_cache:
        return channel_videos_cache[cache_key]
    opts = {
        "quiet": True,
        "dump_single_json": True,
        "extract_flat": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 5,
    }
    results = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(channel_url, download=False)
            entries = data.get("entries", [])
            count = 0
            for entry in entries:
                if entry.get("url"):
                    thumb = ""
                    if entry.get("thumbnails"):
                        thumb = entry["thumbnails"][-1]["url"]
                    results.append({
                        "title": entry.get("title", "Unknown"),
                        "videoId": entry["url"],
                        "thumbnail": thumb
                    })
                    count += 1
                    if count >= max_results:
                        break
    except Exception as e:
        print("get_channel_videos error:", e)
    channel_videos_cache[cache_key] = results
    return results

#################################
# Searching with caching using yt_dlp
def search_youtube(query, max_results=20):
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
    search_cache[cache_key] = results
    return results

#################################
# Extraction with caching
def extract_formats(video_url, cookies_file=None):
    cache_key = (video_url, cookies_file)
    if cache_key in extraction_cache:
        return extraction_cache[cache_key]
    opts = {
        "quiet": True,
        "skip_download": True,
        "dump_single_json": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "socket_timeout": 5,
    }
    if cookies_file:
        opts["cookies"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    extraction_cache[cache_key] = info
    return info

#################################
# Worker for background tasks
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
# Main Application
class ModernYouTubeClient(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BorgorTube")
        self.resize(1280, 800)
        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(4)
        self.current_info = None
        self.current_video_url = None
        self.qualities_available = []
        self.player_process = None
        self.is_detached = False
        self.video_process = None
        self.audio_process = None
        self.channel_avatar_url = None
        self.channel_name = None
        self.channel_url = None
        self.video_title = None
        self.video_description = None
        self.low_latency_mode = False
        # Timer to monitor fullscreen state
        self.fullscreen_timer = QTimer()
        self.fullscreen_timer.timeout.connect(self.check_fullscreen_mode)
        self.sync_timer = QTimer()
        self.sync_timer.timeout.connect(self.check_sync)
        self.build_ui()

    # ------------------
    def build_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_vlayout = QVBoxLayout(central_widget)
        main_vlayout.setContentsMargins(0, 0, 0, 0)
        main_vlayout.setSpacing(0)
        self.top_bar = self.create_top_bar()
        main_vlayout.addWidget(self.top_bar, 0)
        self.stacked_widget = QStackedWidget()
        self.home_page = self.create_home_page()
        self.playback_page = QWidget()
        self.playback_layout = QHBoxLayout(self.playback_page)
        self.playback_layout.setContentsMargins(10, 10, 10, 10)
        self.playback_layout.setSpacing(10)
        self.playback_left_container = self.create_playback_left_container()
        self.playback_layout.addWidget(self.playback_left_container, 2)
        self.suggested_scroll = QScrollArea()
        self.suggested_scroll.setWidgetResizable(True)
        self.suggested_container = QWidget()
        self.suggested_layout = QVBoxLayout(self.suggested_container)
        self.suggested_layout.setSpacing(10)
        self.suggested_layout.setContentsMargins(10, 10, 10, 10)
        self.suggested_scroll.setWidget(self.suggested_container)
        self.playback_layout.addWidget(self.suggested_scroll, 1)
        self.channel_page = self.create_channel_page()
        self.stacked_widget.addWidget(self.home_page)   # index 0
        self.stacked_widget.addWidget(self.playback_page) # index 1
        self.stacked_widget.addWidget(self.channel_page)  # index 2
        main_vlayout.addWidget(self.stacked_widget, 1)
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setFixedHeight(150)
        main_vlayout.addWidget(self.console_output, 0)
        self.stacked_widget.setCurrentIndex(0)

    # ------------------
    def create_top_bar(self):
        top_widget = QWidget()
        layout = QHBoxLayout(top_widget)
        layout.setContentsMargins(10, 5, 10, 5)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.go_back)
        layout.addWidget(self.back_button)
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Search or paste YouTube URL")
        self.search_field.returnPressed.connect(self.do_search)
        layout.addWidget(self.search_field, 1)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.do_search)
        layout.addWidget(self.search_button)
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("360p")
        self.quality_combo.currentIndexChanged.connect(self.on_quality_changed)
        layout.addWidget(self.quality_combo)
        self.detach_button = QPushButton("Detach")
        self.detach_button.clicked.connect(self.toggle_detach)
        layout.addWidget(self.detach_button)
        self.fullscreen_button = QPushButton("Fullscreen")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        layout.addWidget(self.fullscreen_button)
        self.dark_button = QPushButton("Dark Mode")
        self.dark_button.clicked.connect(self.toggle_dark_mode)
        layout.addWidget(self.dark_button)
        self.low_latency_button = QPushButton("Low Latency")
        self.low_latency_button.setCheckable(True)
        self.low_latency_button.toggled.connect(self.toggle_low_latency_mode)
        layout.addWidget(self.low_latency_button)
        return top_widget

    def toggle_low_latency_mode(self, checked):
        self.low_latency_mode = checked
        mode = "enabled" if checked else "disabled"
        self.console_output.append(f"Low latency mode {mode}.")
        if self.current_video_url and self.player_process is not None:
            time_pos = get_current_playback_time("/tmp/mpvsocket")
            self.launch_mpv_merged(self.quality_combo.currentText(), start_time=time_pos)

    def toggle_fullscreen(self):
        # If not detached, detach first.
        if not self.is_detached:
            self.is_detached = True
            self.console_output.append("Detaching for fullscreen mode.")
            time_pos = get_current_playback_time("/tmp/mpvsocket")
            self.launch_mpv_merged(self.quality_combo.currentText(), start_time=time_pos)
        # Send fullscreen command and start monitoring timer.
        set_fullscreen_property(True)
        self.console_output.append("Fullscreen command sent.")
        self.fullscreen_timer.start(1000)

    def check_fullscreen_mode(self):
        if self.player_process is None or self.player_process.state() != QProcess.Running:
            self.console_output.append("mpv process not running. Re-embedding video.")
            self.is_detached = False
            self.fullscreen_timer.stop()
            time_pos = get_current_playback_time("/tmp/mpvsocket")
            self.launch_mpv_merged(self.quality_combo.currentText(), start_time=time_pos)
            return
        if not get_fullscreen_status():
            self.console_output.append("mpv not in fullscreen. Forcing fullscreen mode.")
            set_fullscreen_property(True)

    def go_back(self):
        idx = self.stacked_widget.currentIndex()
        if idx == 2:
            if self.current_video_url and self.player_process:
                self.stacked_widget.setCurrentIndex(1)
                self.console_output.append("Back to playback from channel page.")
            else:
                self.stacked_widget.setCurrentIndex(0)
                self.console_output.append("Back to home from channel page.")
        elif idx == 1:
            self.stacked_widget.setCurrentIndex(0)
            self.console_output.append("Back to home from playback page.")
        else:
            self.console_output.append("Already on home page.")

    def create_home_page(self):
        page = QWidget()
        vlayout = QVBoxLayout(page)
        vlayout.setContentsMargins(5, 5, 5, 5)
        self.home_label = QLabel("Search Results")
        self.home_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        vlayout.addWidget(self.home_label, 0, Qt.AlignTop)
        self.home_scroll = QScrollArea()
        self.home_scroll.setWidgetResizable(True)
        self.home_grid_container = QWidget()
        self.home_grid_layout = QGridLayout(self.home_grid_container)
        self.home_grid_layout.setSpacing(10)
        self.home_grid_layout.setContentsMargins(10, 10, 10, 10)
        self.home_scroll.setWidget(self.home_grid_container)
        vlayout.addWidget(self.home_scroll, 1)
        return page

    def populate_home_grid(self, results):
        for i in reversed(range(self.home_grid_layout.count())):
            item = self.home_grid_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)
        row, col = 0, 0
        max_cols = 4
        for vid in results:
            widget = self.create_video_thumb(vid)
            self.home_grid_layout.addWidget(widget, row, col)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

    def create_video_thumb(self, video_data):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        thumb_label = QLabel()
        thumb_label.setFixedSize(320, 180)
        thumb_label.setStyleSheet("background-color: #000;")
        url = video_data.get("thumbnail")
        if url:
            low_res_url = get_low_res_thumbnail(url)
            if low_res_url in thumbnail_cache:
                pixmap = QPixmap()
                pixmap.loadFromData(thumbnail_cache[low_res_url])
                pixmap = pixmap.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                thumb_label.setPixmap(pixmap)
            else:
                def fetch_thumb(u):
                    r = session.get(u, timeout=5)
                    r.raise_for_status()
                    return r.content
                worker = Worker(fetch_thumb, low_res_url)
                def done(res):
                    if not isinstance(res, Exception):
                        thumbnail_cache[low_res_url] = res
                        px = QPixmap()
                        px.loadFromData(res)
                        px = px.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        thumb_label.setPixmap(px)
                worker.signals.finished.connect(done)
                self.threadpool.start(worker)
        title_label = QLabel(video_data.get("title", "Untitled"))
        title_label.setFixedWidth(320)
        title_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(11)
        title_label.setFont(font)
        layout.addWidget(thumb_label, 0, Qt.AlignCenter)
        layout.addWidget(title_label, 0, Qt.AlignCenter)
        def on_thumb_click(_):
            self.console_output.append(f"Clicked: {video_data['title']}")
            self.start_extraction(video_data["videoId"])
        thumb_label.mousePressEvent = on_thumb_click
        title_label.mousePressEvent = on_thumb_click
        return w

    def create_playback_left_container(self):
        container = QWidget()
        vlayout = QVBoxLayout(container)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(10)
        self.mpv_playback_widget = QWidget()
        self.mpv_playback_widget.setStyleSheet("background-color: #333;")
        self.mpv_playback_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.mpv_playback_widget.setMinimumHeight(400)
        vlayout.addWidget(self.mpv_playback_widget, 2)
        info_container = QWidget()
        info_layout = QVBoxLayout(info_container)
        info_layout.setSpacing(5)
        self.video_title_label = QLabel("Video Title Here")
        self.video_title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        info_layout.addWidget(self.video_title_label, 0)
        channel_row = QWidget()
        channel_hlayout = QHBoxLayout(channel_row)
        channel_hlayout.setSpacing(10)
        channel_hlayout.setContentsMargins(0, 0, 0, 0)
        self.channel_avatar_label = QLabel()
        self.channel_avatar_label.setFixedSize(48, 48)
        self.channel_avatar_label.setStyleSheet("background-color: #ccc;")
        self.channel_name_label = QLabel("Channel Name")
        ch_font = QFont()
        ch_font.setPointSize(13)
        ch_font.setBold(True)
        self.channel_name_label.setFont(ch_font)
        channel_hlayout.addWidget(self.channel_avatar_label, 0, Qt.AlignVCenter | Qt.AlignLeft)
        channel_hlayout.addWidget(self.channel_name_label, 0, Qt.AlignVCenter | Qt.AlignLeft)
        channel_row.setLayout(channel_hlayout)
        info_layout.addWidget(channel_row, 0)
        desc_scroll = QScrollArea()
        desc_scroll.setWidgetResizable(True)
        desc_widget = QWidget()
        desc_layout = QVBoxLayout(desc_widget)
        desc_layout.setContentsMargins(0, 0, 0, 0)
        self.video_desc_label = QLabel("Video description goes here. Potentially very long.")
        self.video_desc_label.setWordWrap(True)
        desc_layout.addWidget(self.video_desc_label, 1)
        desc_widget.setLayout(desc_layout)
        desc_scroll.setWidget(desc_widget)
        desc_scroll.setFixedHeight(200)
        info_layout.addWidget(desc_scroll)
        info_container.setLayout(info_layout)
        vlayout.addWidget(info_container, 1)
        return container

    def create_channel_page(self):
        page = QWidget()
        vlayout = QVBoxLayout(page)
        vlayout.setContentsMargins(10, 10, 10, 10)
        top_container = QWidget()
        top_layout = QHBoxLayout(top_container)
        top_layout.setSpacing(10)
        top_layout.setContentsMargins(0, 0, 0, 0)
        self.channel_avatar_big = QLabel()
        self.channel_avatar_big.setFixedSize(100, 100)
        self.channel_avatar_big.setStyleSheet("background-color: #ccc;")
        vchan = QVBoxLayout()
        self.channel_name_big = QLabel("Channel Name")
        ch_font = QFont()
        ch_font.setPointSize(16)
        ch_font.setBold(True)
        self.channel_name_big.setFont(ch_font)
        self.channel_subs_label = QLabel("Subscriber count: ???")
        vchan.addWidget(self.channel_name_big, 0, Qt.AlignLeft)
        vchan.addWidget(self.channel_subs_label, 0, Qt.AlignLeft)
        top_layout.addWidget(self.channel_avatar_big, 0, Qt.AlignVCenter)
        top_layout.addLayout(vchan, 1)
        vlayout.addWidget(top_container, 0, Qt.AlignLeft)
        self.channel_scroll = QScrollArea()
        self.channel_scroll.setWidgetResizable(True)
        self.channel_videos_container = QWidget()
        self.channel_videos_layout = QGridLayout(self.channel_videos_container)
        self.channel_videos_layout.setSpacing(10)
        self.channel_videos_layout.setContentsMargins(10, 10, 10, 10)
        self.channel_scroll.setWidget(self.channel_videos_container)
        vlayout.addWidget(self.channel_scroll, 1)
        page.setLayout(vlayout)
        return page

    def show_channel_page(self):
        if self.channel_url and not self.channel_avatar_url:
            self.channel_avatar_url = scrape_channel_avatar(self.channel_url)
        for i in reversed(range(self.channel_videos_layout.count())):
            item = self.channel_videos_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)
        if self.channel_avatar_url:
            if self.channel_avatar_url in thumbnail_cache:
                data = thumbnail_cache[self.channel_avatar_url]
                pix = QPixmap()
                pix.loadFromData(data)
                pix = pix.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.channel_avatar_big.setPixmap(pix)
            else:
                try:
                    r = session.get(self.channel_avatar_url, timeout=5)
                    r.raise_for_status()
                    thumbnail_cache[self.channel_avatar_url] = r.content
                    pix = QPixmap()
                    pix.loadFromData(r.content)
                    pix = pix.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.channel_avatar_big.setPixmap(pix)
                except Exception as e:
                    print("Error fetching channel avatar:", e)
        else:
            self.channel_avatar_big.setStyleSheet("background-color: #ccc;")
        self.channel_name_big.setText(self.channel_name or "Unknown Channel")
        self.channel_subs_label.setText("Subscriber count: ???")
        worker = Worker(self.fetch_channel_videos_bg, self.channel_url)
        worker.signals.finished.connect(self.on_channel_videos_fetched)
        self.threadpool.start(worker)
        self.stacked_widget.setCurrentIndex(2)

    def fetch_channel_videos_bg(self, channel_url):
        return get_channel_videos(channel_url, max_results=20)

    def on_channel_videos_fetched(self, result):
        if isinstance(result, Exception):
            self.console_output.append(f"Error fetching channel videos: {result}")
            return
        self.populate_channel_grid(result)

    def populate_channel_grid(self, videos):
        for i in reversed(range(self.channel_videos_layout.count())):
            item = self.channel_videos_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)
        row, col = 0, 0
        max_cols = 4
        for vid in videos:
            widget = self.create_channel_video_thumb(vid)
            self.channel_videos_layout.addWidget(widget, row, col)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

    def create_channel_video_thumb(self, video_data):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        thumb_label = QLabel()
        thumb_label.setFixedSize(320, 180)
        thumb_label.setStyleSheet("background-color: #000;")
        url = video_data.get("thumbnail")
        if url:
            low_res_url = get_low_res_thumbnail(url)
            if low_res_url in thumbnail_cache:
                px = QPixmap()
                px.loadFromData(thumbnail_cache[low_res_url])
                px = px.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                thumb_label.setPixmap(px)
            else:
                def fetch_thumb(u):
                    r = session.get(u, timeout=5)
                    r.raise_for_status()
                    return r.content
                worker = Worker(fetch_thumb, low_res_url)
                def done(res):
                    if not isinstance(res, Exception):
                        thumbnail_cache[low_res_url] = res
                        px = QPixmap()
                        px.loadFromData(res)
                        px = px.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        thumb_label.setPixmap(px)
                worker.signals.finished.connect(done)
                self.threadpool.start(worker)
        title_label = QLabel(video_data.get("title", "Untitled"))
        title_label.setFixedWidth(320)
        title_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(11)
        title_label.setFont(font)
        layout.addWidget(thumb_label, 0, Qt.AlignCenter)
        layout.addWidget(title_label, 0, Qt.AlignCenter)
        def on_thumb_click(_):
            self.console_output.append(f"Channel video clicked: {video_data['title']}")
            self.start_extraction(video_data["videoId"])
        thumb_label.mousePressEvent = on_thumb_click
        title_label.mousePressEvent = on_thumb_click
        return w

    def do_search(self):
        query = self.search_field.text().strip()
        if not query:
            self.console_output.append("No search query.")
            return
        self.console_output.append(f"Searching: {query}")
        save_search_history(query)
        self.populate_home_grid([])
        worker = Worker(search_youtube, query, 20)
        worker.signals.finished.connect(self.on_search_results)
        self.threadpool.start(worker)

    def on_search_results(self, result):
        if isinstance(result, Exception):
            self.console_output.append(f"Search error: {result}")
            return
        self.console_output.append(f"Got {len(result)} results.")
        self.populate_home_grid(result)
        self.stacked_widget.setCurrentIndex(0)

    def start_extraction(self, url):
        self.console_output.append(f"Extracting info for: {url}")
        worker = Worker(self.extract_with_fallback, url)
        worker.signals.finished.connect(self.on_extraction_done)
        self.threadpool.start(worker)
        self.stacked_widget.setCurrentIndex(1)

    def extract_with_fallback(self, video_url):
        try:
            info = extract_formats(video_url)
            return ("no_cookies", info)
        except Exception:
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
        if "original_url" in info:
            self.current_video_url = info["original_url"]
        elif "webpage_url" in info:
            self.current_video_url = info["webpage_url"]
        else:
            self.current_video_url = None
        self.channel_name = info.get("uploader", "Unknown Channel")
        self.channel_url = info.get("uploader_url", "")
        self.channel_avatar_url = scrape_channel_avatar(self.channel_url)
        self.video_title = info.get("title", "Untitled")
        self.video_description = info.get("description", "No description available.")
        if mode == "no_cookies":
            self.console_output.append("Extraction succeeded without cookies.")
        else:
            self.console_output.append("Extraction succeeded with cookies fallback.")
        self.qualities_available = available_buckets(info)
        self.quality_combo.clear()
        for q in self.qualities_available:
            self.quality_combo.addItem(q)
        if self.qualities_available:
            self.quality_combo.setCurrentIndex(0)
        self.update_video_info_fields()
        best = self.qualities_available[0] if self.qualities_available else "360p"
        self.launch_mpv_merged(best)
        self.console_output.append("Available qualities: " + ", ".join(self.qualities_available))
        self.update_suggested_videos()

    def update_video_info_fields(self):
        self.video_title_label.setText(self.video_title or "Untitled")
        self.video_desc_label.setText(self.video_description or "No description.")
        self.channel_name_label.setText(self.channel_name or "Unknown Channel")
        if self.channel_avatar_url:
            if self.channel_avatar_url in thumbnail_cache:
                data = thumbnail_cache[self.channel_avatar_url]
                px = QPixmap()
                px.loadFromData(data)
                px = px.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.channel_avatar_label.setPixmap(px)
            else:
                def fetch_avatar(u):
                    r = session.get(u, timeout=5)
                    r.raise_for_status()
                    return r.content
                worker = Worker(fetch_avatar, self.channel_avatar_url)
                def done(res):
                    if not isinstance(res, Exception):
                        thumbnail_cache[self.channel_avatar_url] = res
                        px = QPixmap()
                        px.loadFromData(res)
                        px = px.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self.channel_avatar_label.setPixmap(px)
                worker.signals.finished.connect(done)
                self.threadpool.start(worker)
        else:
            self.channel_avatar_label.setStyleSheet("background-color: #ccc;")
        def on_channel_clicked(_):
            self.console_output.append(f"Channel clicked: {self.channel_name}")
            self.show_channel_page()
        self.channel_name_label.mousePressEvent = on_channel_clicked
        self.channel_avatar_label.mousePressEvent = on_channel_clicked

    def update_suggested_videos(self):
        for i in reversed(range(self.suggested_layout.count())):
            item = self.suggested_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)
        hist = load_search_history()
        queries = hist.get("queries", [])
        if not queries:
            label = QLabel("No suggestions. Search for something first.")
            self.suggested_layout.addWidget(label)
            return
        last_query = queries[-1]
        recs = search_youtube(last_query, max_results=8)
        for vid in recs:
            w = self.create_suggested_thumb(vid)
            self.suggested_layout.addWidget(w)

    def create_suggested_thumb(self, video_data):
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setSpacing(5)
        layout.setContentsMargins(0, 0, 0, 0)
        thumb_label = QLabel()
        thumb_label.setFixedSize(120, 70)
        thumb_label.setStyleSheet("background-color: #000;")
        url = video_data.get("thumbnail")
        if url:
            low_res_url = get_low_res_thumbnail(url)
            if low_res_url in thumbnail_cache:
                px = QPixmap()
                px.loadFromData(thumbnail_cache[low_res_url])
                px = px.scaled(120, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                thumb_label.setPixmap(px)
            else:
                def fetch_thumb(u):
                    r = session.get(u, timeout=5)
                    r.raise_for_status()
                    return r.content
                worker = Worker(fetch_thumb, low_res_url)
                def done(res):
                    if not isinstance(res, Exception):
                        thumbnail_cache[low_res_url] = res
                        px = QPixmap()
                        px.loadFromData(res)
                        px = px.scaled(120, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        thumb_label.setPixmap(px)
                worker.signals.finished.connect(done)
                self.threadpool.start(worker)
        text_label = QLabel(video_data.get("title", "Untitled"))
        text_label.setWordWrap(True)
        text_label.setFixedWidth(200)
        layout.addWidget(thumb_label, 0, Qt.AlignVCenter)
        layout.addWidget(text_label, 1)
        def on_thumb_click(_):
            self.console_output.append(f"Suggested video clicked: {video_data['title']}")
            self.start_extraction(video_data["videoId"])
        thumb_label.mousePressEvent = on_thumb_click
        text_label.mousePressEvent = on_thumb_click
        return w

    def launch_mpv_merged(self, quality_label, start_time=0.0):
        if not self.current_video_url:
            self.console_output.append("No URL to play.")
            return
        mpv_format = FORMAT_MAPPING.get(quality_label, "best")
        # Build base arguments without buffering options yet.
        mpv_args = [
            "--osc",
            "--demuxer-thread=yes",
            f"--ytdl-format={mpv_format}",
            f"--log-file={LOG_FILE}",
            "--msg-level=all=v",
            "--input-ipc-server=/tmp/mpvsocket",
            self.current_video_url
        ]
        if start_time > 0:
            mpv_args.insert(0, f"--start={start_time}")
        if not self.is_detached:
            wid = str(int(self.mpv_playback_widget.winId()))
            mpv_args.insert(0, f"--wid={wid}")
        # If low latency mode is enabled, use minimal buffering; else, use full buffering.
        if self.low_latency_mode:
            low_latency_options = [
                "--cache=no",
                "--demuxer-readahead-secs=0",
                "--demuxer-max-bytes=524288",
                "--demuxer-max-back-bytes=131072"
            ]
            mpv_args = low_latency_options + mpv_args
        else:
            buffering_options = [
                "--cache=yes",
                "--cache-secs=30",
                "--demuxer-readahead-secs=10"
            ]
            mpv_args = buffering_options + mpv_args
        self.kill_mpv()
        self.console_output.append(f"Launching mpv with '{quality_label}' at {start_time:.1f}s")
        self.player_process = QProcess(self)
        self.player_process.start("mpv", mpv_args)

    def on_quality_changed(self):
        if not self.current_video_url or not self.player_process:
            return
        time_pos = get_current_playback_time("/tmp/mpvsocket")
        new_q = self.quality_combo.currentText()
        self.console_output.append(f"Switching quality to {new_q} at {time_pos:.1f}s")
        self.launch_mpv_merged(new_q, start_time=time_pos)

    def kill_mpv(self):
        if self.player_process:
            self.player_process.terminate()
            self.player_process.waitForFinished(3000)
            self.player_process = None
        if self.video_process:
            self.video_process.terminate()
            self.video_process.waitForFinished(3000)
            self.video_process = None
        if self.audio_process:
            self.audio_process.terminate()
            self.audio_process.waitForFinished(3000)
            self.audio_process = None

    def watch_separate_streams(self):
        if not self.current_info:
            self.console_output.append("No video info for separate streams.")
            return
        formats = self.current_info.get("formats", [])
        video_only_url = None
        audio_only_url = None
        for f in formats:
            if f.get("acodec") == "none" and not video_only_url:
                video_only_url = f["url"]
            if f.get("vcodec") == "none" and not audio_only_url:
                audio_only_url = f["url"]
        if not video_only_url or not audio_only_url:
            self.console_output.append("No separate streams found; fallback merged.")
            self.launch_mpv_merged(self.quality_combo.currentText())
            return
        self.kill_mpv()
        video_ipc = "/tmp/mpv_video"
        audio_ipc = "/tmp/mpv_audio"
        video_args = [
            "--no-audio", "--osc", "--cache=yes", "--demuxer-thread=yes",
            f"--input-ipc-server={video_ipc}", video_only_url
        ]
        audio_args = [
            "--no-video", "--osc", "--cache=yes", "--demuxer-thread=yes",
            f"--input-ipc-server={audio_ipc}", audio_only_url
        ]
        if not self.is_detached:
            wid = str(int(self.mpv_playback_widget.winId()))
            video_args.insert(0, f"--wid={wid}")
        self.console_output.append("Launching separate mpv processes for video and audio.")
        self.video_process = QProcess(self)
        self.video_process.start("mpv", video_args)
        self.audio_process = QProcess(self)
        self.audio_process.start("mpv", audio_args)
        self.sync_timer.start(1000)

    def check_sync(self):
        self.console_output.append("Sync check (stub).")

    def toggle_detach(self):
        self.is_detached = not self.is_detached
        if self.is_detached:
            self.detach_button.setText("Attach")
            self.console_output.append("Now in detached mode.")
        else:
            self.detach_button.setText("Detach")
            self.console_output.append("Now in embedded mode.")
        if self.current_video_url and self.player_process is not None:
            time_pos = get_current_playback_time("/tmp/mpvsocket")
            self.launch_mpv_merged(self.quality_combo.currentText(), start_time=time_pos)

    def toggle_fullscreen_manual(self):
        try:
            cmd = b'cycle fullscreen\n'
            with open("/tmp/mpvsocket", "wb") as f:
                f.write(cmd)
            self.console_output.append("Fullscreen toggled via IPC.")
        except Exception as e:
            self.console_output.append(f"Fullscreen toggle failed: {e}")

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

def main():
    app = QApplication(sys.argv)
    client = ModernYouTubeClient()
    client.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
