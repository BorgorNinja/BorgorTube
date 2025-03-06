import sys
import os
import re
import json
import requests
import asyncio
from pyppeteer import launch
import yt_dlp
from PyQt5.QtCore import QProcess, Qt
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton,
                             QLineEdit, QTextEdit, QLabel, QProgressBar, QComboBox,
                             QHBoxLayout, QListWidget, QListWidgetItem)
from PyQt5.QtGui import QPixmap, QPalette, QColor
from bs4 import BeautifulSoup
import aiohttp
import platform

# Define the path for the settings file
SETTINGS_FILE = "settings.json"

##############################
# Fallback functions for cookies extraction using pyppeteer and yt-dlp

async def get_cookies_headless(video_url):
    """Launch headless Chromium to fetch cookies from the given URL."""
    print("Launching headless Chromium for cookie extraction...")
    browser = await launch(headless=True, args=['--no-sandbox'])
    page = await browser.newPage()
    await page.goto(video_url, {'waitUntil': 'networkidle2'})
    # Allow some time for JavaScript to run
    await asyncio.sleep(5)
    cookies = await page.cookies()
    await browser.close()
    return cookies

def save_cookies_to_file(cookies, filename="cookies.txt"):
    """Save cookies in Netscape cookie file format."""
    with open(filename, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            domain = cookie.get("domain", "")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = cookie.get("path", "/")
            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
            expiry = str(cookie.get("expires", 0))
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            f.write("\t".join([domain, flag, path, secure, expiry, name, value]) + "\n")
    print("Cookies saved to", filename)
    return filename

def extract_video_info(video_url, cookies_file=None):
    """Attempt to extract video information using yt-dlp."""
    opts = {
        'quiet': True,
        'skip_download': True,
        'format': 'best'
    }
    if cookies_file:
        opts['cookies'] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info

##############################
# YouTube Client with integrated fallback for mpv playback

class YouTubeClient(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle('YouTube Client')
        self.setGeometry(100, 100, 1200, 800)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.main_layout = QHBoxLayout()
        self.central_widget.setLayout(self.main_layout)

        self.left_layout = QVBoxLayout()
        self.right_layout = QVBoxLayout()
        self.main_layout.addLayout(self.left_layout, 2)
        self.main_layout.addLayout(self.right_layout, 3)

        self.url_input = QLineEdit(self)
        self.url_input.setPlaceholderText('Search YouTube')
        self.left_layout.addWidget(self.url_input)

        self.search_button = QPushButton('Search', self)
        self.search_button.clicked.connect(self.start_search)
        self.left_layout.addWidget(self.search_button)

        self.video_list = QListWidget(self)
        self.left_layout.addWidget(self.video_list)

        self.console_output = QTextEdit(self)
        self.console_output.setReadOnly(True)
        self.left_layout.addWidget(self.console_output)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setVisible(False)
        self.left_layout.addWidget(self.progress_bar)

        self.quality_combo = QComboBox(self)
        self.quality_combo.setVisible(True)
        self.quality_combo.addItems(['Best', '1080p', '720p', '480p', '360p'])
        self.left_layout.addWidget(self.quality_combo)

        self.watch_button = QPushButton('Watch', self)
        self.watch_button.setVisible(False)
        self.watch_button.clicked.connect(self.watch_video)
        self.left_layout.addWidget(self.watch_button)

        self.add_to_playlist_button = QPushButton('Add to Playlist', self)
        self.add_to_playlist_button.clicked.connect(self.add_to_playlist)
        self.left_layout.addWidget(self.add_to_playlist_button)

        self.play_playlist_button = QPushButton('Play Playlist', self)
        self.play_playlist_button.clicked.connect(self.play_playlist)
        self.left_layout.addWidget(self.play_playlist_button)

        self.dark_mode_button = QPushButton('Toggle Dark Mode', self)
        self.dark_mode_button.clicked.connect(self.toggle_dark_mode)
        self.left_layout.addWidget(self.dark_mode_button)

        # Detach/Attach Button
        self.attach_detach_button = QPushButton('Detach Video', self)
        self.attach_detach_button.clicked.connect(self.toggle_attach_detach)
        self.left_layout.addWidget(self.attach_detach_button)

        # Create an mpv_widget and force it to be a native window
        self.mpv_widget = QWidget(self)
        # These attributes help ensure we get a real X11 window ID (on X11).
        self.mpv_widget.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self.mpv_widget.setAttribute(Qt.WA_NativeWindow, True)
        # Set a minimum size so it's visible
        self.mpv_widget.setMinimumSize(640, 360)
        self.right_layout.addWidget(self.mpv_widget)
        self.mpv_widget.show()

        # Media Controls – placeholders
        self.media_controls_layout = QHBoxLayout()
        self.play_pause_button = QPushButton('Play/Pause', self)
        self.play_pause_button.clicked.connect(self.play_pause_video)
        self.media_controls_layout.addWidget(self.play_pause_button)

        self.fullscreen_button = QPushButton('Fullscreen', self)
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        self.media_controls_layout.addWidget(self.fullscreen_button)

        self.fast_forward_button = QPushButton('Fast Forward', self)
        self.fast_forward_button.clicked.connect(self.fast_forward_video)
        self.media_controls_layout.addWidget(self.fast_forward_button)

        self.right_layout.addLayout(self.media_controls_layout)

        # Variables to track playback and playlist
        self.player_process = None
        self.playlist = []
        self.current_video_url = None
        self.is_detached = False  # Track whether mpv is detached or embedded

        # Load persistent settings (quality)
        self.load_quality_settings()

        # Connect quality change to save settings
        self.quality_combo.currentTextChanged.connect(self.save_quality_settings)

        # Connect Enter key press in URL input to trigger search
        self.url_input.returnPressed.connect(self.search_button.click)

    def start_search(self):
        asyncio.run(self.perform_search())

    async def perform_search(self):
        query = self.url_input.text().strip()
        if not query:
            self.console_output.append('Please enter a search query.')
            return

        self.console_output.append(f'Starting search for: {query}')
        videos, next_page_token, prev_page_token = await self.search_videos(query)
        self.display_search_results(videos)

    async def search_videos(self, query):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }
        url = f"https://www.youtube.com/results?search_query={query}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                self.console_output.append(f"HTTP GET to {url} returned status {response.status}")
                videos = []

                if response.status == 200:
                    response_text = await response.text()
                    soup = BeautifulSoup(response_text, 'html.parser')
                    scripts = soup.find_all('script')
                    for script in scripts:
                        if 'var ytInitialData = ' in script.text:
                            json_data = json.loads(re.search(r'var ytInitialData = ({.*?});', script.text).group(1))
                            video_items = json_data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
                            for item in video_items:
                                if 'videoRenderer' in item:
                                    video_info = item['videoRenderer']
                                    title = video_info['title']['runs'][0]['text']
                                    video_id = video_info['videoId']
                                    thumbnail_url = video_info['thumbnail']['thumbnails'][0]['url']
                                    duration = self.parse_duration(video_info['lengthText']['simpleText']) if 'lengthText' in video_info else 0
                                    author = video_info['ownerText']['runs'][0]['text'] if 'ownerText' in video_info else 'Unknown'
                                    videos.append({
                                        'title': title,
                                        'videoId': video_id,
                                        'thumbnail': thumbnail_url,
                                        'duration': duration,
                                        'author': author
                                    })

                return videos, None, None

    def display_search_results(self, videos):
        self.console_output.append('Search finished, displaying results...')
        self.video_list.clear()
        for video in videos:
            item_widget = QWidget()
            item_layout = QHBoxLayout()

            thumbnail_label = QLabel()
            thumbnail = QPixmap()
            thumbnail.loadFromData(requests.get(video['thumbnail']).content)
            thumbnail_label.setPixmap(thumbnail.scaled(120, 90, Qt.KeepAspectRatio))
            item_layout.addWidget(thumbnail_label)

            title_label = QLabel(f"{video['title']} ({video['videoId']})")
            title_label.setWordWrap(True)
            item_layout.addWidget(title_label)

            item_widget.setLayout(item_layout)

            item = QListWidgetItem(self.video_list)
            item.setSizeHint(item_widget.sizeHint())
            self.video_list.setItemWidget(item, item_widget)
            item.setData(Qt.UserRole, video)

            self.video_list.addItem(item)

        self.watch_button.setVisible(True)

    def toggle_attach_detach(self):
        """
        Toggle between embedded and detached mpv playback.
        """
        if not self.current_video_url:
            self.console_output.append("No video is selected to attach/detach.")
            return

        # Kill any existing mpv process first
        self.kill_mpv()

        if self.is_detached:
            # Currently detached; switch to embedded
            self.is_detached = False
            self.attach_detach_button.setText("Detach Video")
            self.console_output.append("Re-attaching video to the embedded mpv widget...")
            self.start_player(embedded=True)
        else:
            # Currently embedded; switch to detached
            self.is_detached = True
            self.attach_detach_button.setText("Attach Video")
            self.console_output.append("Detaching video to a new mpv window...")
            self.start_player(embedded=False)

    def kill_mpv(self):
        """
        Kill any existing mpv process. 
        """
        os_type = platform.system()
        if os_type == "Windows":
            os.system("taskkill /F /IM mpv.exe")
        elif os_type == "Linux":
            os.system("pkill mpv")
        elif os_type == "Darwin":
            os.system("pkill mpv")
        else:
            self.console_output.append('Unsupported Operating System')

    def watch_video(self):
        current_item = self.video_list.currentItem()
        if current_item:
            video_data = current_item.data(Qt.UserRole)
            self.current_video_url = f"https://www.youtube.com/watch?v={video_data['videoId']}"
            self.console_output.append(f'Watching video: {video_data["title"]}')

            self.kill_mpv()
            # By default, use embedded mode if user hasn't toggled detach yet
            self.start_player(embedded=not self.is_detached)

    def prepare_playback(self):
        """
        Try to extract video info using yt-dlp. If that fails, use headless Chromium to extract cookies.
        Returns the path to the cookies file if cookies were needed; otherwise, returns None.
        """
        try:
            self.console_output.append("Attempting initial video extraction without cookies...")
            extract_video_info(self.current_video_url, cookies_file=None)
            self.console_output.append("Extraction succeeded without cookies.")
            return None
        except Exception as e:
            self.console_output.append("Initial extraction failed: " + str(e))
            self.console_output.append("Attempting headless cookie extraction fallback...")
            try:
                cookies = asyncio.get_event_loop().run_until_complete(get_cookies_headless(self.current_video_url))
            except Exception as e2:
                self.console_output.append("Headless cookie extraction failed: " + str(e2))
                return None
            cookies_file = save_cookies_to_file(cookies, "cookies.txt")
            self.console_output.append("Cookies extracted and saved.")
            try:
                extract_video_info(self.current_video_url, cookies_file=cookies_file)
                self.console_output.append("Extraction succeeded with fallback cookies.")
                return cookies_file
            except Exception as e3:
                self.console_output.append("Fallback extraction failed: " + str(e3))
                return None

    def start_player(self, embedded=True):
        """
        Start mpv playback. If embedded=True, mpv is embedded in self.mpv_widget.
        Otherwise, mpv spawns a detached window.
        """
        # Prepare playback by attempting extraction (and cookie fallback if needed)
        cookies_file = self.prepare_playback()

        # Common mpv options to (hopefully) improve audio
        # and general playback quality:
        mpv_opts = [
            '--osc',
            '--cache=yes',
            '--demuxer-thread=yes',
            f'--ytdl-format={self.get_quality_option()}',
            '--input-ipc-server=/tmp/mpvsocket',
            self.current_video_url
        ]
        if cookies_file:
            mpv_opts.append(f'--ytdl-raw-options=cookies={cookies_file}')

        if embedded:
            # Embed mpv in the mpv_widget
            wid = str(int(self.mpv_widget.winId()))
            mpv_opts.insert(0, f'--wid={wid}')
            self.console_output.append('Playback started with mpv (embedded).')
        else:
            self.console_output.append('Playback started with mpv (detached).')

        self.player_process = QProcess(self)
        self.player_process.start("mpv", mpv_opts)

    def parse_duration(self, duration_text):
        """ Convert duration text to seconds """
        match = re.match(r'(\d+):(\d+)', duration_text)
        if match:
            minutes, seconds = map(int, match.groups())
            return minutes * 60 + seconds
        return 0

    def load_quality_settings(self):
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                quality = settings.get('quality', 'Best')
                self.quality_combo.setCurrentText(quality)

    def save_quality_settings(self):
        quality = self.quality_combo.currentText()
        settings = {'quality': quality}
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f)

    def get_quality_option(self):
        quality_map = {
            'Best': 'best',
            '1080p': 'best[height<=1080]',
            '720p': 'best[height<=720]',
            '480p': 'best[height<=480]',
            '360p': 'best[height<=360]',
        }
        return quality_map.get(self.quality_combo.currentText(), 'best')

    def add_to_playlist(self):
        if self.current_video_url:
            self.playlist.append(self.current_video_url)
            self.console_output.append(f'Added to playlist: {self.current_video_url}')

    def play_playlist(self):
        if not self.playlist:
            self.console_output.append('Playlist is empty.')
            return

        for video in self.playlist:
            self.current_video_url = video
            self.kill_mpv()
            self.start_player(embedded=not self.is_detached)
            self.console_output.append(f'Playing video from playlist: {video}')

    def toggle_dark_mode(self):
        palette = self.palette()
        if palette.color(QPalette.Window) == QColor(255, 255, 255):
            # Switch to dark mode
            palette.setColor(QPalette.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
            self.console_output.setStyleSheet("background-color: #2c2c2c; color: white;")
        else:
            # Switch to light mode
            palette.setColor(QPalette.Window, QColor(255, 255, 255))
            palette.setColor(QPalette.WindowText, QColor(0, 0, 0))
            self.console_output.setStyleSheet("background-color: white; color: black;")
        
        self.setPalette(palette)

    def play_pause_video(self):
        self.console_output.append("Play/Pause control is not wired for mpv embedding (use mpv keybindings).")

    def toggle_fullscreen(self):
        self.console_output.append("Fullscreen control is not wired for mpv embedding (use mpv keybindings).")

    def fast_forward_video(self):
        self.console_output.append("Fast forward control is not wired for mpv embedding (use mpv keybindings).")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    client = YouTubeClient()
    client.show()
    sys.exit(app.exec_())
