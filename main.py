import sys
import re
import json
import aiohttp
import asyncio
import requests
from PyQt5.QtCore import QProcess
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton,
                             QLineEdit, QTextEdit, QLabel, QProgressBar, QFileDialog, QComboBox,
                             QHBoxLayout, QListWidget, QListWidgetItem, QAction, QSystemTrayIcon, QMessageBox,
                             QScrollArea, QDialog)
from PyQt5.QtCore import Qt, QRunnable, QThreadPool, pyqtSignal, QObject
from PyQt5.QtGui import QPixmap, QIcon, QPalette, QColor
from bs4 import BeautifulSoup
from aiohttp.client_exceptions import ClientConnectorError

class FetchCommentsWorker(QRunnable):
    class Signals(QObject):
        result = pyqtSignal(list)
        console = pyqtSignal(str)
    
    def __init__(self, video_id, offset, limit):
        super().__init__()
        self.video_id = video_id
        self.offset = offset
        self.limit = limit
        self.signals = FetchCommentsWorker.Signals()
        self.failed_instances = {}

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        comments = loop.run_until_complete(self.fetch_comments())
        loop.close()
        self.signals.result.emit(comments)

    async def fetch_comments(self):
        invidious_instances = [
            "https://invidious.darkness.services/",
            "https://invidious.incogniweb.net/",
        ]

        comments = []

        async def fetch_comments_from_instance(session, instance):
            url = f"{instance}/api/v1/comments/{self.video_id}?offset={self.offset}&limit={self.limit}"
            try:
                async with session.get(url) as response:
                    self.signals.console.emit(f"Fetching comments from {instance}, status: {response.status}")
                    if response.status == 200:
                        response_text = await response.text()
                        if response_text.strip() and 'html' not in response.headers.get('Content-Type', ''):
                            json_data = json.loads(response_text)
                            if 'comments' in json_data:
                                json_data = json_data['comments']
                            if isinstance(json_data, list):
                                instance_comments = []
                                for item in json_data:
                                    if isinstance(item, dict):
                                        username = item.get('author', 'Unknown')
                                        text = item.get('content', '')
                                        profile_pic_url = item['authorThumbnails'][0]['url'] if 'authorThumbnails' in item and item['authorThumbnails'] else ''
                                        instance_comments.append({
                                            'username': username,
                                            'text': text,
                                            'profile_pic': profile_pic_url
                                        })
                                self.signals.console.emit(f"Fetched {len(instance_comments)} comments from {instance}")
                                return instance_comments
                            else:
                                self.signals.console.emit(f"Unexpected JSON structure: {json_data}")
                    else:
                        self.signals.console.emit(f"Failed to fetch comments from {instance}, status code: {response.status}")
                    return []
            except ClientConnectorError as e:
                self.signals.console.emit(f"Failed to connect to {instance}: {e}")
                self.failed_instances[instance] = self.failed_instances.get(instance, 0) + 1
                return []
            except Exception as e:
                self.signals.console.emit(f"Error fetching comments from {instance}: {e}")
                return []

        async with aiohttp.ClientSession() as session:
            tasks = [fetch_comments_from_instance(session, instance) for instance in invidious_instances if self.failed_instances.get(instance, 0) < 3]
            completed_tasks = await asyncio.gather(*tasks)
            for instance_comments in completed_tasks:
                if instance_comments:
                    comments.extend(instance_comments)
                    break  # Stop after successfully retrieving comments from one instance

        self.signals.console.emit(f"Total comments fetched: {len(comments)}")
        return comments

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
        self.quality_combo.setVisible(False)
        self.quality_combo.addItems(['Best', '1080p', '720p', '480p', '360p'])
        self.left_layout.addWidget(self.quality_combo)

        self.watch_button = QPushButton('Watch', self)
        self.watch_button.setVisible(False)
        self.watch_button.clicked.connect(self.watch_video)
        self.left_layout.addWidget(self.watch_button)

        self.show_comments_button = QPushButton('Show Comments', self)
        self.show_comments_button.setVisible(False)
        self.show_comments_button.clicked.connect(self.show_comments)
        self.left_layout.addWidget(self.show_comments_button)

        self.next_page_button = QPushButton('Next Page', self)
        self.next_page_button.setVisible(False)
        self.next_page_button.clicked.connect(self.next_page)
        self.left_layout.addWidget(self.next_page_button)

        self.prev_page_button = QPushButton('Previous Page', self)
        self.prev_page_button.setVisible(False)
        self.prev_page_button.clicked.connect(self.prev_page)
        self.left_layout.addWidget(self.prev_page_button)

        self.dark_mode_button = QPushButton('Toggle Dark Mode', self)
        self.dark_mode_button.clicked.connect(self.toggle_dark_mode)
        self.left_layout.addWidget(self.dark_mode_button)

        self.tray_icon = QSystemTrayIcon(QIcon("icon.png"), self)
        self.tray_icon.show()

        self.video_list.itemClicked.connect(self.display_video_details)
        self.init_menu()

        # Embed MPV player
        self.mpv_widget = QWidget(self)
        self.right_layout.addWidget(self.mpv_widget)
        self.mpv_process = None

        self.current_page = 1
        self.next_page_token = None
        self.prev_page_token = None

        self.playlist = []

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

        self.pip_button = QPushButton('Picture in Picture', self)
        self.pip_button.clicked.connect(self.toggle_picture_in_picture)
        self.media_controls_layout.addWidget(self.pip_button)

        self.right_layout.addLayout(self.media_controls_layout)

        self.is_fullscreen = False
        self.fullscreen_process = None
        self.current_video_url = None

        self.thread_pool = QThreadPool()

    def init_menu(self):
        menubar = self.menuBar()
        settings_menu = menubar.addMenu('Settings')
        default_dir_action = QAction('Set Default Download Directory', self)
        default_dir_action.triggered.connect(self.set_default_directory)
        settings_menu.addAction(default_dir_action)

    def set_default_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Default Download Directory")
        if directory:
            with open('settings.json', 'w') as f:
                json.dump({'default_directory': directory}, f)

    def get_default_directory(self):
        if os.path.exists('settings.json'):
            with open('settings.json', 'r') as f:
                settings = json.load(f)
                return settings.get('default_directory', '')
        return ''

    def start_search(self):
        asyncio.run(self.perform_search())

    async def perform_search(self):
        query = self.url_input.text().strip()
        if not query:
            self.console_output.append('Please enter a search query.')
            return

        self.console_output.append(f'Starting search for: {query}')
        videos, next_page_token, prev_page_token = await self.search_videos(query)
        self.display_search_results(videos, next_page_token, prev_page_token)

    async def next_page(self):
        if self.next_page_token:
            self.current_page += 1
            query = self.url_input.text().strip()
            videos, next_page_token, prev_page_token = await self.search_videos(query, self.next_page_token)
            self.display_search_results(videos, next_page_token, prev_page_token)

    async def prev_page(self):
        if self.prev_page_token:
            self.current_page -= 1
            query = self.url_input.text().strip()
            videos, next_page_token, prev_page_token = await self.search_videos(query, self.prev_page_token)
            self.display_search_results(videos, next_page_token, prev_page_token)

    async def search_videos(self, query, page_token=None):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }
        url = f"https://www.youtube.com/results?search_query={query}"
        if page_token:
            url += f"&page_token={page_token}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                self.console_output.append(f"HTTP GET to {url} returned status {response.status}")
                videos = []
                next_page_token = None
                prev_page_token = None

                if response.status == 200:
                    try:
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
                                if 'continuations' in json_data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']:
                                    continuation = json_data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['continuations'][0]['nextContinuationData']
                                    next_page_token = continuation['continuation']
                                    if 'prevContinuationData' in continuation:
                                        prev_page_token = continuation['prevContinuationData']['continuation']
                        self.console_output.append(f"Scraped {len(videos)} videos from YouTube")
                    except (json.JSONDecodeError, KeyError, AttributeError, re.error) as e:
                        self.console_output.append(f"Error parsing YouTube results: {str(e)}")
                else:
                    self.console_output.append(f"Failed to retrieve YouTube results, status code: {response.status}")

                return videos, next_page_token, prev_page_token

    def display_search_results(self, videos, next_page_token, prev_page_token):
        self.console_output.append('Search finished, displaying results...')
        self.video_list.clear()
        for video in videos:
            if 'title' not in video or 'videoId' not in video:
                self.console_output.append(f"Skipping video entry due to missing keys: {video}")
                continue

            item_widget = QWidget()
            item_layout = QHBoxLayout()

            thumbnail_label = QLabel()
            if 'thumbnail' in video:
                thumbnail = QPixmap()
                thumbnail.loadFromData(requests.get(video['thumbnail']).content)
                thumbnail_label.setPixmap(thumbnail.scaled(120, 90, Qt.KeepAspectRatio))
            else:
                thumbnail_label.setText("No Thumbnail")
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

        self.next_page_token = next_page_token
        self.prev_page_token = prev_page_token
        self.next_page_button.setVisible(bool(next_page_token))
        self.prev_page_button.setVisible(bool(prev_page_token))

    def display_video_details(self, item):
        video = item.data(Qt.UserRole)
        self.console_output.clear()
        self.console_output.append(f"Title: {video['title']}")
        self.console_output.append(f"Description: {video.get('description', 'No description available')}")
        self.console_output.append(f"Duration: {self.format_duration(video['duration'])}")
        self.console_output.append(f"Author: {video['author']}")

        self.current_video = video
        self.current_video_url = f"https://www.youtube.com/watch?v={video['videoId']}"
        self.quality_combo.setVisible(True)
        self.watch_button.setVisible(True)
        self.show_comments_button.setVisible(True)

    def format_duration(self, duration):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

    def get_quality_option(self):
        quality = self.quality_combo.currentText()
        quality_map = {
            'Best': 'best',
            '1080p': 'bestvideo[height<=1080]+bestaudio/best',
            '720p': 'bestvideo[height<=720]+bestaudio/best',
            '480p': 'bestvideo[height<=480]+bestaudio/best',
            '360p': 'bestvideo[height<=360]+bestaudio/best'
        }
        return quality_map.get(quality, 'best')

    def watch_video(self):
        if not self.current_video:
            QMessageBox.warning(self, 'Error', 'No video selected.')
            return

        self.console_output.append(f"Playing video: {self.current_video_url}")
        self.play_video_with_mpv(self.current_video_url)

    def play_video_with_mpv(self, url):
        if self.mpv_process:
            self.mpv_process.terminate()
            self.mpv_process.waitForFinished()

        quality_option = self.get_quality_option()

        command = [
            'mpv',
            f'--wid={int(self.mpv_widget.winId())}',
            '--no-cache',
            '--osc',
            '--force-window=immediate',
            '--geometry=100%x100%',
            url,
            f'--ytdl-format={quality_option}',
            '--input-ipc-server=/tmp/mpvsocket'
        ]

        self.console_output.append(f"MPV command: {' '.join(command)}")

        self.mpv_process = QProcess(self)
        self.mpv_process.start(command[0], command[1:])
        self.mpv_process.readyReadStandardOutput.connect(self.handle_mpv_output)
        self.mpv_process.readyReadStandardError.connect(self.handle_mpv_output)

    def handle_mpv_output(self):
        output = self.mpv_process.readAllStandardOutput().data().decode()
        error_output = self.mpv_process.readAllStandardError().data().decode()
        if output:
            self.console_output.append(f"MPV Output: {output}")
        if error_output:
            self.console_output.append(f"MPV Error: {error_output}")
        if "Exiting... (Quit)" in error_output and self.is_fullscreen:
            self.is_fullscreen = False
            self.console_output.append("Fullscreen exited, reattaching MPV player.")
            self.play_video_with_mpv(self.current_video_url)

    def toggle_dark_mode(self):
        palette = QPalette()
        if self.dark_mode_button.text() == "Toggle Dark Mode":
            palette.setColor(QPalette.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(25, 25, 25))
            palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
            palette.setColor(QPalette.ToolTipBase, Qt.white)
            palette.setColor(QPalette.ToolTipText, Qt.white)
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(53, 53, 53))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.BrightText, Qt.red)
            palette.setColor(QPalette.Link, QColor(42, 130, 218))
            palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
            palette.setColor(QPalette.HighlightedText, Qt.black)
            self.dark_mode_button.setText("Toggle Light Mode")
        else:
            palette = QApplication.style().standardPalette()
            self.dark_mode_button.setText("Toggle Dark Mode")

        QApplication.setPalette(palette)

    def play_pause_video(self):
        if self.mpv_process:
            self.mpv_process.write(b'cycle pause\n')

    def toggle_fullscreen(self):
        if not self.is_fullscreen:
            self.console_output.append('Detaching MPV player for fullscreen')
            self.is_fullscreen = True
            self.mpv_process.terminate()
            self.mpv_process.waitForFinished()
            quality_option = self.get_quality_option()
            command = [
                'mpv',
                self.current_video_url,
                '--fullscreen',
                '--no-cache',
                '--osc',
                '--geometry=100%x100%',
                f'--ytdl-format={quality_option}',
                '--input-ipc-server=/tmp/mpvsocket'
            ]
            self.console_output.append(f"MPV fullscreen command: {' '.join(command)}")
            self.fullscreen_process = QProcess(self)
            self.fullscreen_process.start(command[0], command[1:])
            self.fullscreen_process.finished.connect(self.on_fullscreen_exit)
        else:
            self.console_output.append('Fullscreen already active')

    def on_fullscreen_exit(self):
        self.is_fullscreen = False
        self.console_output.append('Fullscreen exited, reattaching MPV player')
        self.play_video_with_mpv(self.current_video_url)

    def fast_forward_video(self):
        if self.mpv_process:
            self.mpv_process.write(b'seek 10\n')

    def toggle_picture_in_picture(self):
        if self.mpv_process:
            self.mpv_process.terminate()
            self.mpv_process.waitForFinished()

        quality_option = self.get_quality_option()
        command = [
            'mpv',
            '--no-cache',
            '--osc',
            '--force-window=immediate',
            '--geometry=20%x20%+80%+80%',
            '--ontop',
            '--autofit=640x360',
            self.current_video_url,
            f'--ytdl-format={quality_option}',
            '--input-ipc-server=/tmp/mpvsocket'
        ]

        self.console_output.append(f"MPV PiP command: {' '.join(command)}")

        self.mpv_process = QProcess(self)
        self.mpv_process.start(command[0], command[1:])
        self.mpv_process.readyReadStandardOutput.connect(self.handle_mpv_output)
        self.mpv_process.readyReadStandardError.connect(self.handle_mpv_output)

    def show_comments(self):
        if not self.current_video:
            QMessageBox.warning(self, 'Error', 'No video selected.')
            return

        self.video_id = self.current_video['videoId']
        self.comments_offset = 0

        self.comments_dialog = QDialog(self)
        self.comments_dialog.setWindowTitle("Comments")
        self.comments_layout = QVBoxLayout()
        self.comments_scroll_area = QScrollArea()
        self.comments_widget = QWidget()
        self.comments_widget_layout = QVBoxLayout()
        self.comments_widget.setLayout(self.comments_widget_layout)
        self.comments_scroll_area.setWidget(self.comments_widget)
        self.comments_scroll_area.setWidgetResizable(True)
        self.comments_layout.addWidget(self.comments_scroll_area)

        self.show_more_button = QPushButton("Show More Comments")
        self.show_more_button.clicked.connect(self.load_more_comments)
        self.comments_layout.addWidget(self.show_more_button)

        self.comments_dialog.setLayout(self.comments_layout)

        self.load_more_comments()
        self.comments_dialog.exec_()

    def load_more_comments(self):
        worker = FetchCommentsWorker(self.video_id, self.comments_offset, 10)
        worker.signals.result.connect(self.display_comments)
        worker.signals.console.connect(self.update_console)
        self.thread_pool.start(worker)

    def update_console(self, text):
        self.console_output.append(text)

    def display_comments(self, comments):
        self.console_output.append(f"Displaying {len(comments)} comments")
        for comment in comments:
            self.console_output.append(f"Comment by {comment['username']}: {comment['text']}")
            comment_layout = QHBoxLayout()
            profile_pic_label = QLabel()
            if comment['profile_pic']:
                profile_pic = QPixmap()
                profile_pic.loadFromData(requests.get(comment['profile_pic']).content)
                profile_pic_label.setPixmap(profile_pic.scaled(40, 40, Qt.KeepAspectRatio))
            else:
                profile_pic_label.setText("No Profile Pic")
            comment_layout.addWidget(profile_pic_label)

            comment_text = QLabel(f"{comment['username']}: {comment['text']}")
            comment_text.setWordWrap(True)
            comment_layout.addWidget(comment_text)

            self.comments_widget_layout.addLayout(comment_layout)

        self.comments_offset += 10

    def parse_duration(self, duration_text):
        parts = duration_text.split(':')
        if len(parts) == 2:  # MM:SS
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:  # HH:MM:SS
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0

if __name__ == '__main__':
    app = QApplication(sys.argv)
    client = YouTubeClient()
    client.show()
    sys.exit(app.exec_())
