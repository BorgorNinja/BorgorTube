import sys
import os
import json
import asyncio
import platform
import requests
from pyppeteer import launch
import yt_dlp

from PyQt5.QtCore import (
    QProcess, Qt, QThreadPool, QRunnable, pyqtSlot, QObject, pyqtSignal,
    QSize, QTimer
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QGridLayout, QLineEdit, QPushButton, QLabel, QTextEdit,
    QComboBox, QListWidget, QListWidgetItem, QStackedWidget, QSpacerItem,
    QSizePolicy
)
from PyQt5.QtGui import QPixmap, QIcon, QPalette, QColor, QFont

#################################
# Hard-coded user agent (Chromium 132)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

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

SETTINGS_FILE = "settings.json"
LOG_FILE = "mpvlog.txt"

#################################
# Check which “buckets” are available
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
    # Return in descending order as in ALL_QUALITIES
    result = []
    for q in ALL_QUALITIES:
        if q in bucket_avail:
            result.append(q)
    return result

#################################
# Cookie fallback for restricted videos
async def get_cookies_headless(video_url):
    print("Launching headless Chromium for cookie extraction...")
    browser = await launch(headless=True, args=["--no-sandbox"])
    page = await browser.newPage()
    await page.setUserAgent(USER_AGENT)
    await page.goto(video_url, {"waitUntil": "networkidle2"})
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
            f.write("\t".join([
                domain, flag, path, secure, expiry, name, value
            ]) + "\n")
    print("Cookies saved to", filename)
    return filename

#################################
# Searching & extraction
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
# Thread worker
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
# Main Window
class ModernYouTubeClient(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BorgorTube")
        self.resize(1280, 800)

        self.threadpool = QThreadPool()
        self.current_info = None
        self.current_video_url = None
        self.qualities_available = []
        self.player_process = None
        self.is_detached = False
        self.playlist = []
        self.video_process = None
        self.audio_process = None

        # Timer for sync (separate streams)
        self.sync_timer = QTimer()
        self.sync_timer.timeout.connect(self.check_sync)

        # Build UI
        self.build_ui()

    def build_ui(self):
        # Main vertical container
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_vlayout = QVBoxLayout(central_widget)
        main_vlayout.setContentsMargins(0,0,0,0)
        main_vlayout.setSpacing(0)

        # 1) Top bar
        self.top_bar = self.create_top_bar()
        main_vlayout.addWidget(self.top_bar, 0)

        # 2) Stacked widget for pages
        self.stacked_widget = QStackedWidget()
        self.home_page = self.create_home_page()
        self.playback_page = self.create_playback_page()
        self.stacked_widget.addWidget(self.home_page)     # index 0
        self.stacked_widget.addWidget(self.playback_page) # index 1

        main_vlayout.addWidget(self.stacked_widget, 1)

        # 3) Console output at bottom
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setFixedHeight(150)
        main_vlayout.addWidget(self.console_output)

        self.stacked_widget.setCurrentIndex(0)

    # ------------------
    # Top bar
    def create_top_bar(self):
        top_widget = QWidget()
        layout = QHBoxLayout(top_widget)
        layout.setContentsMargins(10,5,10,5)

        # Search field
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Search or paste YouTube URL")
        self.search_field.returnPressed.connect(self.do_search)
        layout.addWidget(self.search_field)

        # Search button
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.do_search)
        layout.addWidget(self.search_button)

        # Quality combo
        self.quality_combo = QComboBox()
        # We'll fill it dynamically once we know what's available
        # But let's default to "360p" if none
        self.quality_combo.addItem("360p")
        layout.addWidget(self.quality_combo)

        # Detach
        self.detach_button = QPushButton("Detach")
        self.detach_button.clicked.connect(self.toggle_detach)
        layout.addWidget(self.detach_button)

        # Fullscreen
        self.fullscreen_button = QPushButton("Fullscreen")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        layout.addWidget(self.fullscreen_button)

        # Dark Mode
        self.dark_button = QPushButton("Dark Mode")
        self.dark_button.clicked.connect(self.toggle_dark_mode)
        layout.addWidget(self.dark_button)

        return top_widget

    # ------------------
    # Home page
    def create_home_page(self):
        page = QWidget()
        vlayout = QVBoxLayout(page)
        vlayout.setContentsMargins(5,5,5,5)

        # Label
        self.home_label = QLabel("Search Results")
        self.home_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        vlayout.addWidget(self.home_label, 0, Qt.AlignTop)

        # Scrollable area with grid
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setContentsMargins(10,10,10,10)
        self.scroll_area.setWidget(self.grid_container)

        vlayout.addWidget(self.scroll_area, 1)
        return page

    def populate_home_grid(self, results):
        # Clear existing
        for i in reversed(range(self.grid_layout.count())):
            item = self.grid_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                w.setParent(None)

        row, col = 0, 0
        max_cols = 4
        for i, vid in enumerate(results):
            widget = self.create_video_thumb(vid)
            self.grid_layout.addWidget(widget, row, col)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

    def create_video_thumb(self, video_data):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(5)

        # Thumbnail label
        thumb_label = QLabel()
        thumb_label.setFixedSize(320,180)
        thumb_label.setStyleSheet("background-color: #000;")
        # If we have a thumbnail, fetch it asynchronously
        if video_data.get("thumbnail"):
            url = video_data["thumbnail"]
            # We'll do a background fetch
            worker = Worker(self.fetch_thumb_image, url)
            # We pass the label so we can update once done
            def on_thumb_fetched(result):
                if isinstance(result, Exception):
                    self.console_output.append(f"Error fetching thumbnail: {result}")
                else:
                    pixmap = QPixmap()
                    pixmap.loadFromData(result)
                    pixmap = pixmap.scaled(320,180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    thumb_label.setPixmap(pixmap)
            worker.signals.finished.connect(on_thumb_fetched)
            self.threadpool.start(worker)

        # Title
        title_label = QLabel(video_data["title"])
        title_label.setFixedWidth(320)
        title_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(11)
        title_label.setFont(font)

        layout.addWidget(thumb_label, 0, Qt.AlignCenter)
        layout.addWidget(title_label, 0, Qt.AlignCenter)

        # Clicking the thumbnail => start extraction
        def on_thumb_click(_):
            self.console_output.append(f"Clicked: {video_data['title']}")
            self.start_extraction(video_data["videoId"])
        thumb_label.mousePressEvent = on_thumb_click

        return w

    def fetch_thumb_image(self, url):
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=5)
        resp.raise_for_status()
        return resp.content

    # ------------------
    # Playback page
    def create_playback_page(self):
        page = QWidget()
        hlayout = QHBoxLayout(page)
        hlayout.setContentsMargins(10,10,10,10)
        # mpv area
        self.mpv_playback_widget = QWidget()
        self.mpv_playback_widget.setStyleSheet("background-color: #333;")
        # Let it expand
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.mpv_playback_widget.setSizePolicy(sizePolicy)
        hlayout.addWidget(self.mpv_playback_widget, 1)

        # Right side (related videos if needed)
        self.related_list = QListWidget()
        self.related_list.setFixedWidth(300)
        hlayout.addWidget(self.related_list, 0)

        return page

    # ------------------
    # Searching logic
    def do_search(self):
        query = self.search_field.text().strip()
        if not query:
            self.console_output.append("No search query.")
            return
        self.console_output.append(f"Searching: {query}")
        # Clear old grid
        self.populate_home_grid([])
        # Kick off worker
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

    # Extraction fallback
    def start_extraction(self, url):
        self.console_output.append(f"Extracting info for: {url}")
        worker = Worker(self.extract_with_fallback, url)
        worker.signals.finished.connect(self.on_extraction_done)
        self.threadpool.start(worker)
        # Switch to playback page
        self.stacked_widget.setCurrentIndex(1)

    def extract_with_fallback(self, video_url):
        try:
            info = extract_formats(video_url)
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
        if "original_url" in info:
            self.current_video_url = info["original_url"]
        elif "webpage_url" in info:
            self.current_video_url = info["webpage_url"]
        else:
            self.current_video_url = None

        if mode == "no_cookies":
            self.console_output.append("Extraction ok (no cookies).")
        else:
            self.console_output.append("Extraction ok (cookies fallback).")

        # Determine available qualities
        self.qualities_available = available_buckets(info)
        if not self.qualities_available:
            self.qualities_available = ["360p"]
        # Populate top-bar combo
        self.quality_combo.clear()
        for q in self.qualities_available:
            self.quality_combo.addItem(q)
        self.console_output.append(f"Available: {self.qualities_available}")

        # Auto-play at first quality
        first_q = self.qualities_available[0]
        self.launch_mpv_merged(first_q)

        # If you want to fill "related_list" with suggestions, do so here
        self.related_list.clear()
        # Placeholder
        for i in range(5):
            self.related_list.addItem(f"Related Video {i+1}")

    # ------------------
    # mpv playback
    def launch_mpv_merged(self, quality_label):
        if not self.current_video_url:
            self.console_output.append("No URL to play.")
            return
        mpv_format = FORMAT_MAPPING.get(quality_label, "best")
        mpv_args = [
            "--osc",
            "--cache=yes",
            "--demuxer-thread=yes",
            f"--ytdl-format={mpv_format}",
            f"--log-file={LOG_FILE}",
            "--msg-level=all=v",
            "--input-ipc-server=/tmp/mpvsocket",
            self.current_video_url
        ]
        if not self.is_detached:
            # embed
            wid = str(int(self.mpv_playback_widget.winId()))
            mpv_args.insert(0, f"--wid={wid}")
        self.kill_mpv()
        self.console_output.append(f"Launching mpv with {quality_label} => {mpv_format}")
        self.player_process = QProcess(self)
        self.player_process.start("mpv", mpv_args)

    def kill_mpv(self):
        os_type = platform.system()
        if os_type == "Windows":
            os.system("taskkill /F /IM mpv.exe")
        elif os_type == "Linux":
            os.system("pkill mpv")
        elif os_type == "Darwin":
            os.system("pkill mpv")
        else:
            self.console_output.append("Unsupported OS for kill mpv.")

    def toggle_detach(self):
        self.is_detached = not self.is_detached
        if self.is_detached:
            self.detach_button.setText("Attach")
            self.console_output.append("Now in detached mode.")
        else:
            self.detach_button.setText("Detach")
            self.console_output.append("Now in embedded mode.")
        # If something is playing, relaunch
        if self.current_video_url and self.player_process:
            self.launch_mpv_merged(self.quality_combo.currentText())

    def toggle_fullscreen(self):
        # Send IPC command to mpv to cycle fullscreen
        try:
            cmd = b'cycle fullscreen\n'
            # We can do this only if we have an IPC socket
            if self.player_process:
                # Not directly accessible, so let's open /tmp/mpvsocket
                with open("/tmp/mpvsocket", "wb") as f:
                    f.write(cmd)
            self.console_output.append("Toggled fullscreen via IPC.")
        except Exception as e:
            self.console_output.append(f"Fullscreen toggle failed: {e}")

    # For separate streams
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
            self.console_output.append("No separate video/audio => fallback merged.")
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

        self.console_output.append("Launching separate mpv processes.")
        self.video_process = QProcess(self)
        self.video_process.start("mpv", video_args)
        self.audio_process = QProcess(self)
        self.audio_process.start("mpv", audio_args)
        self.sync_timer.start(1000)

    def check_sync(self):
        self.console_output.append("Stub sync check (separate streams).")

    # ------------------
    # Dark Mode
    def toggle_dark_mode(self):
        palette = self.palette()
        if palette.color(QPalette.Window) == QColor(255, 255, 255):
            # Switch to dark
            palette.setColor(QPalette.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
            self.console_output.setStyleSheet("background-color: #2c2c2c; color: white;")
        else:
            # Switch to light
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
