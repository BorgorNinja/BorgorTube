import sys
import os
import re
import json
import requests
from PyQt5.QtCore import QProcess, Qt
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton,
                             QLineEdit, QTextEdit, QLabel, QProgressBar, QComboBox,
                             QHBoxLayout, QListWidget, QListWidgetItem, QFileDialog, QMessageBox)
from PyQt5.QtGui import QPixmap, QPalette, QColor
from bs4 import BeautifulSoup
import aiohttp
import asyncio
import platform

# Define the path for the settings file
SETTINGS_FILE = "settings.json"

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

        # Attach/Detach Button
        self.attach_detach_button = QPushButton('Detach Video', self)
        self.attach_detach_button.clicked.connect(self.toggle_attach_detach)
        self.left_layout.addWidget(self.attach_detach_button)

        # Embed MPV player
        self.mpv_widget = QWidget(self)
        self.right_layout.addWidget(self.mpv_widget)
        self.mpv_widget.setMouseTracking(True)
        self.mpv_widget.mouseMoveEvent = self.show_mpv_controls
        self.mpv_process = None

        # Media Controls
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

        self.is_fullscreen = False
        self.fullscreen_process = None

        # Playlist and current video URL
        self.playlist = []
        self.current_video_url = None

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
        if self.mpv_process:
            if self.is_fullscreen:
                self.is_fullscreen = False
                self.console_output.append("Exiting fullscreen and detaching MPV player.")
                self.mpv_process.terminate()
                self.mpv_process.waitForFinished()

            # Detach the video
            self.mpv_process.write(b'set pause false\n')  # Resume the video if it was paused
            self.console_output.append("Video detached to a new window.")
            self.attach_detach_button.setText("Attach Video")
            
            command = [
                'mpv',
                '--no-cache',
                '--osc',
                self.current_video_url,
                f'--ytdl-format={self.get_quality_option()}',
                '--input-ipc-server=/tmp/mpvsocket'
            ]
            
            # Start a new MPV process for detached playback
            self.fullscreen_process = QProcess(self)
            self.fullscreen_process.start(command[0], command[1:])

        else:
            # Reattach the video to the PyQt window
            self.console_output.append("Reattaching video to the PyQt window.")
            self.attach_detach_button.setText("Detach Video")
            self.start_mpv_player()  # Start the player again embedded in the window

    def watch_video(self):
        current_item = self.video_list.currentItem()
        if current_item:
            video_data = current_item.data(Qt.UserRole)
            self.current_video_url = f"https://www.youtube.com/watch?v={video_data['videoId']}"
            self.console_output.append(f'Watching video: {video_data["title"]}')

            # Kill all existing MPV instances before starting a new one
            os_type = platform.system()  # Command to kill all running MPV instances
            if os_type == "Windows":
                os.system("taskkill /F /IM mpv.exe")
            elif os_type == "Linux":
                os.system("pkill mpv")
            elif os_type == "Darwin":
                os.system("pkilk mpv")
            else:
                self.console_output.append('Unsupported Operating System')
            self.start_mpv_player()

    def start_mpv_player(self):
        # Get the handle of the mpv_widget for embedding
        wid = str(int(self.mpv_widget.winId()))

        command = [
            'mpv',
            '--no-cache',
            '--osc',
            f'--wid={wid}',  # Embed in this widget
            self.current_video_url,
            f'--ytdl-format={self.get_quality_option()}',
            '--input-ipc-server=/tmp/mpvsocket'
        ]

        self.mpv_process = QProcess(self)
        self.mpv_process.start(command[0], command[1:])
        self.console_output.append('MPV player started.')

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
            self.start_mpv_player()
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
        if self.mpv_process:
            self.mpv_process.write(b'set pause toggle\n')

    def toggle_fullscreen(self):
        if self.mpv_process:
            if self.is_fullscreen:
                self.mpv_process.write(b'quit\n')
                self.is_fullscreen = False
                self.console_output.append("Exited fullscreen.")
            else:
                self.mpv_process.write(b'fullscreen\n')
                self.is_fullscreen = True
                self.console_output.append("Entered fullscreen.")

    def fast_forward_video(self):
        if self.mpv_process:
            self.mpv_process.write(b'set speed 2.0\n')

    def show_mpv_controls(self, event):
        if self.mpv_process:
            self.mpv_process.write(b'osc\n')

if __name__ == '__main__':
    app = QApplication(sys.argv)
    client = YouTubeClient()
    client.show()
    sys.exit(app.exec_())
