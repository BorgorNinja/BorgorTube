# BorgorTube

## Overview

BorgorTube is a desktop application that allows users to search for YouTube videos, view search results, and watch selected videos using MPV media player. The application is built with PyQt5 and integrates with YouTube's API to fetch and display video information.

## Features

- Search for YouTube videos
- Display search results with video thumbnails and details
- Watch selected videos using the MPV media player
- Pagination support for search results
- Basic error handling and logging

## Requirements

- Python 3.x
- PyQt5
- requests
- MPV media player (installed separately)
- pyppeteer
## Installation

1. **Clone the Repository**

   ```bash
   git clone https://github.com/yourusername/YouTubeClient.git
   cd YouTubeClient
   ```

2. **Install Dependencies**

   Create a virtual environment (optional but recommended):

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

   Install the required Python packages:

   ```bash
   pip install -r requirements.txt
   ```

3. **Install MPV**

   Download and install MPV from [MPV's official website](https://mpv.io/). Ensure that MPV is added to your system's PATH.

## Configuration

1. **Settings**

   The application saves settings such as the default download directory in a `settings.json` file. You can set the default download directory via the "Settings" menu in the application.

## Usage

1. **Run the Application**

   ```bash
   python main.py
   ```

2. **Search for Videos**

   Enter a search query in the input field and click the "Search" button to retrieve and display YouTube videos.

3. **Watch a Video**

   Click on a video in the search results to view its details. Click the "Watch" button to play the video using MPV.

4. **Pagination**

   Use the "Next Page" and "Previous Page" buttons to navigate through search result pages.

## Code Overview

### `YouTubeClient` Class

- **`__init__`**: Initializes the main window, UI components, and MPV player integration.
- **`init_menu`**: Initializes the settings menu for configuring default directories.
- **`set_default_directory`**: Opens a dialog to set the default download directory.
- **`get_default_directory`**: Retrieves the default download directory from `settings.json`.
- **`start_search`**: Starts a search for YouTube videos based on the input query.
- **`next_page`** and **`prev_page`**: Navigate through search result pages.
- **`display_search_results`**: Displays search results in the UI.
- **`display_video_details`**: Shows selected video details and prepares for playback.
- **`format_duration`**: Formats the video duration for display.
- **`update_console`**: Updates the console output with log messages.
- **`watch_video`**: Plays the selected video using MPV.
- **`play_video_with_mpv`**: Handles MPV process creation and video playback.
- **`handle_mpv_output`**: Handles MPV output and logs it to the console.

### `SearchThread` Class

Handles background search operations using YouTube's API and updates the UI with results.

## Contributing

Feel free to fork the repository and submit pull requests. For any issues or feature requests, please open an issue on the GitHub repository.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [YouTube Data API](https://developers.google.com/youtube/v3)
- [PyQt5 Documentation](https://www.riverbankcomputing.com/software/pyqt/intro)
- [MPV Media Player](https://mpv.io/)


### Steps to Create GitHub Documentation

1. **Create a New Repository**: Go to GitHub and create a new repository for your project.
2. **Upload Files**: Push your code and the `README.md` file to the new repository.
3. **Add a License**: Include a `LICENSE` file if your project is open source, or choose an appropriate license for your project.
4. **Contributing Guidelines**: If you expect contributions from others, you might want to add a `CONTRIBUTING.md` file with guidelines.
