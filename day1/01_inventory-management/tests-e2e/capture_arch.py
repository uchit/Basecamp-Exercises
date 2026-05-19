"""Capture the rendered ARCHITECTURE.html as a clean screenshot for the gallery."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ARCH = ROOT / "docs" / "ARCHITECTURE.html"
OUT = ROOT / "screenshots" / "00-architecture.png"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_context(viewport={"width": 1300, "height": 900},
                                device_scale_factor=2).new_page()
    page.goto(f"file://{ARCH}", wait_until="load")
    page.screenshot(path=str(OUT), full_page=True)
    print(f"wrote {OUT}")
    browser.close()
