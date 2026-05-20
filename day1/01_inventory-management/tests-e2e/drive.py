"""
D1-S1 · Step 9 — Playwright browser drive of the Catalyst Components inventory app.

Drives a real Chromium against http://localhost:3000 to:

  1. Load the dashboard, snapshot it
  2. Click through every nav route, snapshot each
  3. Exercise the Restocking flow (slider → Place Order → verify Submitted Orders)
  4. Verify the language switcher (EN → JA flips nav labels)
  5. Verify the sidebar collapse toggle
  6. Smoke-check console for noisy logs (Reports.vue used to spam console.log)
  7. Smoke-check FilterBar reactivity on the Reports page

Run servers first (backend on :8001, frontend on :3000), then:

    source .venv/bin/activate
    python drive.py
"""
from __future__ import annotations

import json, sys, time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect, ConsoleMessage

SCREENS = Path(__file__).resolve().parent.parent / "screenshots"
SCREENS.mkdir(parents=True, exist_ok=True)

BASE = "http://localhost:3000"

NAV_ROUTES = [
    ("/",          "01-overview"),
    ("/inventory", "02-inventory"),
    ("/orders",    "03-orders"),
    ("/spending",  "04-finance"),
    ("/demand",    "05-demand"),
    ("/restocking","06-restocking"),
    ("/backlog",   "07-backlog"),
    ("/reports",   "08-reports"),
]


def log(msg: str) -> None:
    print(f"  • {msg}")


def shot(page, name: str) -> Path:
    p = SCREENS / f"{name}.png"
    page.screenshot(path=str(p), full_page=True)
    log(f"snapshot → screenshots/{p.name}")
    return p


def main() -> int:
    findings: list[str] = []
    console_msgs: list[ConsoleMessage] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_msgs.append(m))

        # ────────────────────────────────────────────────────────────────
        # 1. Cold-load the dashboard
        # ────────────────────────────────────────────────────────────────
        print("\n[1] Cold-load /")
        page.goto(f"{BASE}/", wait_until="networkidle")
        title = page.title()
        log(f"<title> = {title!r}")
        # Sidebar brand visible
        brand = page.locator(".brand h1").inner_text(timeout=5000)
        log(f"brand h1 = {brand!r}")
        # KPI section
        kpi_count = page.locator(".kpi-card").count()
        log(f"KPI cards visible: {kpi_count}")
        if kpi_count == 0:
            findings.append("Dashboard had 0 KPI cards on load")
        shot(page, "01-overview")

        # ────────────────────────────────────────────────────────────────
        # 2. Click through every nav route
        # ────────────────────────────────────────────────────────────────
        print("\n[2] Click through every nav route (with ARIA assertion)")
        for path, slug in NAV_ROUTES:
            link = page.locator(f'a[href="{path}"]').first
            # ARIA contract: every nav link must carry an aria-label so screen
            # readers retain access even when the visible label is hidden
            # (collapsed sidebar). Inline icons must be aria-hidden.
            aria_label = link.get_attribute("aria-label")
            if not aria_label or not aria_label.strip():
                findings.append(f"{path}: missing aria-label on nav link")
            icon = link.locator(".nav-icon").first
            icon_hidden = icon.get_attribute("aria-hidden") if icon.count() else None
            if icon_hidden != "true":
                findings.append(f"{path}: .nav-icon missing aria-hidden=true")
            link.click()
            page.wait_for_url(f"{BASE}{path}", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=10000)
            # Look for a page-header h2
            try:
                h2 = page.locator(".page-header h2").first.inner_text(timeout=3000)
            except Exception:
                h2 = "(no .page-header h2)"
                findings.append(f"{path} had no .page-header h2")
            # Loading/error visibility
            has_loading = page.locator(".loading").count() > 0
            has_error = page.locator(".error").count() > 0
            log(f"{path:<14}  h2={h2!r:40}  loading={has_loading}  error={has_error}")
            shot(page, slug)

        # ────────────────────────────────────────────────────────────────
        # 3. Restocking flow
        # ────────────────────────────────────────────────────────────────
        print("\n[3] Restocking flow: slider → Place Order → verify Submitted Orders")
        page.locator('a[href="/restocking"]').first.click()
        page.wait_for_url(f"{BASE}/restocking")
        page.wait_for_load_state("networkidle")
        # Initial budget value
        budget_now = page.locator(".budget-value").inner_text()
        log(f"initial budget display = {budget_now!r}")
        # Recommendations count
        recs_initial = page.locator(".recs-count").inner_text() if page.locator(".recs-count").count() else "(none)"
        log(f"recs-count tag = {recs_initial!r}")

        # Drag the slider to ~$22,000 by setting value + dispatching events
        slider = page.locator(".budget-slider")
        slider.evaluate(
            """el => {
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, '22000');
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
        page.wait_for_timeout(800)  # let the API call settle
        page.wait_for_load_state("networkidle")
        budget_after = page.locator(".budget-value").inner_text()
        recs_after = page.locator(".recs-count").inner_text() if page.locator(".recs-count").count() else "(none)"
        log(f"after slider: budget={budget_after!r}  recs={recs_after!r}")
        shot(page, "09-restocking-slider")

        # Click Place Order
        btn = page.locator(".btn-primary")
        btn_text_before = btn.inner_text()
        log(f"button before click = {btn_text_before!r}")
        btn.click()
        # Wait for success message OR error
        try:
            success = page.locator(".success-msg").inner_text(timeout=15000)
            log(f"success msg = {success!r}")
        except Exception:
            err = page.locator(".error").first.inner_text(timeout=1000) if page.locator(".error").count() else "(no .error)"
            findings.append(f"Place Order did not show success message; saw: {err!r}")
        shot(page, "10-restocking-after-submit")

        # Verify in Orders → Submitted Orders. Wait for the async
        # loadRestockingOrders() to populate — networkidle alone can race
        # against Vue's onMounted async fetch.
        page.locator('a[href="/orders"]').first.click()
        page.wait_for_url(f"{BASE}/orders")
        page.wait_for_load_state("networkidle")
        try:
            page.wait_for_selector(".submitted-card", timeout=10000)
            sub_title = page.locator(".submitted-card .card-title").inner_text()
            sub_rows = page.locator(".submitted-table tbody tr").count()
            log(f"Submitted Orders section: title={sub_title!r}  rows={sub_rows}")
            if sub_rows == 0:
                findings.append("Submitted Orders section is empty after restock")
        except Exception:
            findings.append("Submitted Orders card did not render within 10s")
        shot(page, "11-orders-submitted")

        # ────────────────────────────────────────────────────────────────
        # 4. Language switcher
        # ────────────────────────────────────────────────────────────────
        print("\n[4] Language switcher EN → 日本語")
        page.locator('a[href="/"]').first.click()
        page.wait_for_url(f"{BASE}/")
        # Capture an English label first
        english_label = page.locator('a[href="/inventory"]').first.inner_text().strip()
        log(f"EN inventory label = {english_label!r}")
        # The LanguageSwitcher component lives in the sidebar footer; find its toggle.
        ls = page.locator(".language-switcher, [class*='language']").first
        if ls.count() == 0:
            findings.append("Could not find a LanguageSwitcher control")
        else:
            ls.click()
            # Try clicking a 'ja' option
            ja_option = page.get_by_text("日本語", exact=False).first
            if ja_option.count() > 0:
                ja_option.click()
                page.wait_for_timeout(400)
                japanese_label = page.locator('a[href="/inventory"]').first.inner_text().strip()
                log(f"JA inventory label = {japanese_label!r}")
                if japanese_label == english_label:
                    findings.append(f"Inventory label did not switch from EN={english_label!r} to JA")
                shot(page, "12-japanese")
                # Switch back to English
                ls.click()
                page.get_by_text("English", exact=False).first.click()
                page.wait_for_timeout(300)

        # ────────────────────────────────────────────────────────────────
        # 5. Sidebar collapse toggle
        # ────────────────────────────────────────────────────────────────
        print("\n[5] Sidebar collapse toggle")
        before_expanded = page.locator(".app-shell").get_attribute("class") or ""
        log(f"shell class before = {before_expanded!r}")
        toggle = page.locator(".collapse-toggle")
        if toggle.count() == 0:
            findings.append("Could not find .collapse-toggle")
        else:
            toggle.click()
            page.wait_for_timeout(300)
            after_collapsed = page.locator(".app-shell").get_attribute("class") or ""
            log(f"shell class after collapse = {after_collapsed!r}")
            if "is-collapsed" not in after_collapsed:
                findings.append("Sidebar did not get is-collapsed class")
            shot(page, "13-sidebar-collapsed")
            # Expand back
            page.locator(".collapse-toggle").click()
            page.wait_for_timeout(200)

        # ────────────────────────────────────────────────────────────────
        # 6. Console noise check on /reports (used to spam console.log)
        # ────────────────────────────────────────────────────────────────
        print("\n[6] Console noise check on /reports")
        console_msgs.clear()
        page.locator('a[href="/reports"]').first.click()
        page.wait_for_url(f"{BASE}/reports")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        log_msgs = [m for m in console_msgs if m.type == "log"]
        warn_msgs = [m for m in console_msgs if m.type == "warning"]
        err_msgs = [m for m in console_msgs if m.type == "error"]
        log(f"after /reports: log={len(log_msgs)}  warn={len(warn_msgs)}  error={len(err_msgs)}")
        if log_msgs:
            findings.append(f"Reports.vue still emits console.log ({len(log_msgs)} entries)")
        # Filter reactivity smoke: change Time Period via the FilterBar select
        try:
            period_select = page.locator('.filter-select').first
            period_select.select_option("2025-03")
            page.wait_for_load_state("networkidle", timeout=8000)
            page.wait_for_timeout(400)
            # Quarterly table should still have rows (Q1-2025 includes March)
            quarterly_rows = page.locator("table").first.locator("tbody tr").count()
            log(f"after period=2025-03: quarterly_rows={quarterly_rows}")
            if quarterly_rows == 0:
                findings.append("Reports quarterly table empty after filtering to 2025-03 (expected Q1-2025)")
            shot(page, "14-reports-filtered")
        except Exception as e:
            findings.append(f"FilterBar smoke on /reports raised: {e!s}")

        browser.close()

    # ─────────────────────────────────────────────────────────────────────
    # Report
    # ─────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  E2E summary")
    print("=" * 72)
    print(f"  screenshots written to: {SCREENS}")
    if not findings:
        print("  ✓ all checks passed — no findings")
        return 0
    print(f"  ✗ {len(findings)} finding(s):")
    for f in findings:
        print(f"    - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
