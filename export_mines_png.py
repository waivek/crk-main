# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright"]
# ///

# First run only: uv run --with playwright playwright install firefox

from pathlib import Path
from playwright.sync_api import sync_playwright

HTML_FILE = Path(__file__).parent / "mines_what_to_mine.html"
PNG_FILE  = Path(__file__).parent / "make_mines_infographic" / "mines_what_to_mine.png"

with sync_playwright() as p:
    browser = p.firefox.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    page.goto(f"file://{HTML_FILE.resolve()}")
    page.wait_for_load_state("load")
    page.wait_for_timeout(2000)  # let fonts/images settle
    height = page.evaluate("document.documentElement.scrollHeight")
    page.set_viewport_size({"width": 1100, "height": height})
    page.screenshot(path=str(PNG_FILE), timeout=60_000)
    browser.close()

print(f"Saved: {PNG_FILE}")
