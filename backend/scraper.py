"""
BorgorTube – scraper utilities

Playwright is run in a dedicated thread with its own event loop because
uvicorn's ProactorEventLoop on Windows does not support subprocess creation
(NotImplementedError from asyncio.create_subprocess_exec).
Running playwright synchronously in a ThreadPoolExecutor sidesteps this.
"""

import asyncio
import concurrent.futures
import threading
from typing import Optional

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})

# One shared thread-pool executor so we don't spin up a new OS thread per request
_scraper_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="scraper")


# ---------------------------------------------------------------------------
# Internal: runs in a worker thread with its own event loop
# ---------------------------------------------------------------------------

def _scrape_in_thread(
    video_url: str,
    scroll_count: int,
    max_comments: int,
) -> list[dict]:
    """Synchronous playwright scrape — safe to call from any thread."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[scraper] playwright not installed — run setup.bat / setup.sh")
        return []

    comments: list[dict] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = browser.new_page(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            page.goto(video_url, wait_until="networkidle", timeout=30000)

            try:
                page.wait_for_selector(
                    "#contents.ytd-item-section-renderer",
                    timeout=10000,
                )
            except Exception:
                pass

            for _ in range(3 * scroll_count):
                page.evaluate("window.scrollBy(0, 1500)")
                page.wait_for_timeout(2000)

            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        seen: set[tuple] = set()
        for block in soup.select("ytd-comment-thread-renderer"):
            user_div = block.select_one("#author-text")
            user     = user_div.get_text(strip=True) if user_div else "Unknown"
            pic_div  = block.select_one("#author-thumbnail img")
            pic_url  = pic_div["src"] if pic_div and pic_div.has_attr("src") else None
            text_div = block.select_one("#content-text")
            text     = text_div.get_text(strip=True) if text_div else ""

            key = (user, text)
            if key in seen:
                continue
            seen.add(key)
            comments.append({"username": user, "avatar": pic_url, "text": text})
            if len(comments) >= max_comments:
                break

    except Exception as e:
        print(f"[scraper] playwright error: {e}")

    return comments


# ---------------------------------------------------------------------------
# Public async API — offloads to thread pool
# ---------------------------------------------------------------------------

async def scrape_comments_headless(
    video_url: str,
    scroll_count: int = 1,
    existing_ids: Optional[set] = None,
    max_comments: int = 50,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        _scraper_pool,
        _scrape_in_thread,
        video_url,
        scroll_count,
        max_comments,
    )
    if existing_ids is None:
        return raw
    # Filter duplicates if caller tracks seen ids across paginated calls
    fresh = []
    for c in raw:
        key = (c["username"], c["text"])
        if key not in existing_ids:
            existing_ids.add(key)
            fresh.append(c)
    return fresh


# ---------------------------------------------------------------------------
# Channel avatar (unchanged)
# ---------------------------------------------------------------------------

def scrape_channel_avatar(channel_url: str) -> Optional[str]:
    if not channel_url:
        return None
    try:
        r = _session.get(channel_url, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]
    except Exception as e:
        print("scrape_channel_avatar error:", e)
    return None
