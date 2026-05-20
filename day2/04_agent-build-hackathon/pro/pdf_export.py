"""PDF export — renders the per-RFP HTML viewer to a single PDF via
headless Chromium (Playwright).

Two outputs supported:
  - data viewer PDF: the full audit-drawer HTML rendered with all sections
    expanded so a reviewer can read top-to-bottom in print.
  - keynote PDF: presentation deck rendered one slide per page.

Headless. Idempotent. No browser visible. Caller passes in the .html input
and the desired .pdf output. If Playwright isn't installed, raises a clear
error rather than failing silently.
"""
from __future__ import annotations

from pathlib import Path


def html_to_pdf(html_path: str | Path, pdf_path: str | Path,
                *, format: str = "Letter",
                landscape: bool = False,
                expand_all: bool = True) -> Path:
    """Render an HTML file to PDF. Works against file:// URLs so no server
    needs to be running.

    expand_all: when True (default for the data viewer), injects JS that opens
    every collapsible <details> + adds .open to every .answer so the printed
    document shows full audit drawers. For the keynote (one slide per page),
    pass landscape=True + expand_all=False.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright not installed. `pip install playwright && python -m playwright install chromium`"
        ) from e

    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(
            device_scale_factor=2,
            viewport={"width": 1400, "height": 900},
        ).new_page()
        page.goto(f"file://{html_path}", wait_until="networkidle")

        if expand_all:
            # Force open every collapsible drawer so the PDF shows full audit.
            page.evaluate("""
                document.querySelectorAll('.answer').forEach(el => el.classList.add('open'));
                document.querySelectorAll('details').forEach(el => el.open = true);
            """)

        page.pdf(
            path=str(pdf_path),
            format=format,
            landscape=landscape,
            print_background=True,
            margin={"top": "0.5in", "right": "0.5in",
                     "bottom": "0.5in", "left": "0.5in"},
            prefer_css_page_size=False,
        )
        browser.close()

    return pdf_path
