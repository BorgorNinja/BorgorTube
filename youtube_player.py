import sys
import subprocess
import re
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton,
                             QLineEdit, QTextEdit, QLabel, QProgressBar, QFileDialog, QComboBox,
                             QHBoxLayout, QListWidget, QListWidgetItem, QMenu, QAction, QSystemTrayIcon, QMessageBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QProcess
from PyQt5.QtGui import QPixmap, QIcon
import requests
from bs4 import BeautifulSoup
import json
import os

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

        self.next_page_button = QPushButton('Next Page', self)
        self.next_page_button.setVisible(False)
        self.next_page_button.clicked.connect(self.next_page)
        self.left_layout.addWidget(self.next_page_button)

        self.prev_page_button = QPushButton('Previous Page', self)
        self.prev_page_button.setVisible(False)
        self.prev_page_button.clicked.connect(self.prev_page)
        self.left_layout.addWidget(self.prev_page_button)

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
        query = self.url_input.text().strip()
        if not query:
            self.console_output.append('Please enter a search query.')
            return

        self.console_output.append(f'Starting search for: {query}')
        self.search_thread = SearchThread(query)
        self.search_thread.search_finished.connect(self.display_search_results)
        self.search_thread.console_update.connect(self.update_console)
        self.search_thread.start()

    def next_page(self):
        if self.next_page_token:
            self.current_page += 1
            self.search_thread = SearchThread(self.url_input.text().strip(), self.next_page_token)
            self.search_thread.search_finished.connect(self.display_search_results)
            self.search_thread.console_update.connect(self.update_console)
            self.search_thread.start()

    def prev_page(self):
        if self.prev_page_token:
            self.current_page -= 1
            self.search_thread = SearchThread(self.url_input.text().strip(), self.prev_page_token)
            self.search_thread.search_finished.connect(self.display_search_results)
            self.search_thread.console_update.connect(self.update_console)
            self.search_thread.start()

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
        self.quality_combo.setVisible(True)
        self.watch_button.setVisible(True)

    def format_duration(self, duration):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

    def update_console(self, text):
        self.console_output.append(text)

    def watch_video(self):
        if not self.current_video:
            QMessageBox.warning(self, 'Error', 'No video selected.')
            return

        video_id = self.current_video['videoId']
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        self.console_output.append(f"Playing video: {video_url}")
        self.play_video_with_mpv(video_url)

    def play_video_with_mpv(self, url):
        if self.mpv_process:
            self.mpv_process.terminate()
            self.mpv_process.waitForFinished()
        command = ['mpv', f'--wid={int(self.mpv_widget.winId())}', '--no-cache', '--no-osc', '--force-window=immediate', '--geometry=100%x100%', url]
        self.console_output.append(f"MPV command: {' '.join(command)}")
        self.mpv_process = QProcess(self)
        self.mpv_process.start(command[0], command[1:])
        self.mpv_process.readyReadStandardOutput.connect(self.handle_mpv_output)
        self.mpv_process.readyReadStandardError.connect(self.handle_mpv_output)

    def handle_mpv_output(self):
        output = self.mpv_process.readAllStandardOutput().data().decode()
        error_output = self.mpv_process.readAllStandardError().data().decode()
        if output:
            self.console_output.append(output)
        if error_output:
            self.console_output.append(error_output)

class SearchThread(QThread):
    search_finished = pyqtSignal(list, str, str)
    console_update = pyqtSignal(str)

    def __init__(self, query, page_token=None):
        super().__init__()
        self.query = query
        self.page_token = page_token

    def run(self):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }
        url = f"https://www.youtube.com/results?search_query={self.query}"
        if self.page_token:
            url += f"&page_token={self.page_token}"
        response = requests.get(url, headers=headers)
        self.console_update.emit(f"HTTP GET to {url} returned status {response.status_code}")

        videos = []
        next_page_token = None
        prev_page_token = None

        if response.status_code == 200:
            try:
                soup = BeautifulSoup(response.text, 'html.parser')
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
                self.console_update.emit(f"Scraped {len(videos)} videos from YouTube")
            except (json.JSONDecodeError, KeyError, AttributeError, re.error) as e:
                self.console_update.emit(f"Error parsing YouTube results: {str(e)}")
        else:
            self.console_update.emit(f"Failed to retrieve YouTube results, status code: {response.status_code}")

        self.search_finished.emit(videos, next_page_token, prev_page_token)

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
