"""
Automated QGenda → Shift Swap Finder sync via public schedule link.

No login or API credentials needed — uses the public QGenda link.
Opens the Reports panel, selects Calendar by Staff / Excel, sets the
academic-year date range, downloads the file, and uploads it to the app.

Environment variables:
  QGENDA_PUBLIC_URL   Public QGenda link (the full URL with linkKey & landingPageId)
  APP_URL             Base URL of your deployed app, e.g. https://shift-swap.onrender.com
  APP_ACCESS_CODE     The ACCESS_CODE set on Render (leave blank if none)
"""

import os
import sys
import time
import tempfile
from datetime import date
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

QGENDA_PUBLIC_URL = os.environ.get("QGENDA_PUBLIC_URL", "").strip()
APP_URL           = os.environ.get("APP_URL", "").strip().rstrip("/")
ACCESS_CODE       = os.environ.get("APP_ACCESS_CODE", "").strip()

# Fail fast with a clear message if required vars are missing
if not QGENDA_PUBLIC_URL:
    print("ERROR: QGENDA_PUBLIC_URL secret is not set.")
    print("Go to GitHub → repo → Settings → Secrets → Actions → New secret")
    print("Name: QGENDA_PUBLIC_URL")
    print("Value: https://app.qgenda.com/Link/view?linkKey=...")
    sys.exit(1)
if not APP_URL:
    print("ERROR: APP_URL secret is not set.")
    sys.exit(1)


def academic_year() -> tuple[str, str]:
    """Return (start, end) for the current July–June academic year as MM/DD/YYYY."""
    today = date.today()
    if today.month >= 7:
        start = date(today.year,     7, 1)
        end   = date(today.year + 1, 6, 30)
    else:
        start = date(today.year - 1, 7, 1)
        end   = date(today.year,     6, 30)
    return start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y")


def _fill_date(page, label_hint: str, value: str) -> None:
    """Best-effort date field filler — tries several common selector patterns."""
    selectors = [
        f'input[placeholder*="{label_hint}" i]',
        f'input[name*="{label_hint}" i]',
        f'input[id*="{label_hint}" i]',
        f'input[aria-label*="{label_hint}" i]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count():
                loc.triple_click()
                loc.fill(value)
                loc.press("Tab")
                return
        except Exception:
            pass
    print(f"  WARNING: could not find date field for '{label_hint}'")


def download_excel(download_dir: Path) -> Path:
    start_date, end_date = academic_year()
    print(f"Syncing academic year: {start_date} → {end_date}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        # ── 1. Open the public schedule page ──────────────────────────────
        print(f"Loading {QGENDA_PUBLIC_URL} ...")
        page.goto(QGENDA_PUBLIC_URL, wait_until="networkidle", timeout=45_000)
        time.sleep(1)

        # ── 2. Click the Reports button on the left sidebar ───────────────
        print("Opening Reports panel ...")

        # Dump diagnostic info to help identify the correct selector
        print(f"  Page title: {page.title()}")
        print(f"  Page URL:   {page.url}")

        # Print all visible buttons and links for debugging
        try:
            buttons = page.locator("button, a, [role='button']").all()
            texts = []
            for b in buttons[:40]:
                try:
                    t = b.inner_text().strip()
                    if t:
                        texts.append(repr(t))
                except Exception:
                    pass
            print(f"  Visible clickables (up to 40): {', '.join(texts)}")
        except Exception as e:
            print(f"  Could not enumerate clickables: {e}")

        # Try a broad set of selectors for the Reports button
        _reports_selectors = [
            'button:has-text("Reports")',
            'a:has-text("Reports")',
            '[aria-label*="Reports" i]',
            '[title*="Reports" i]',
            '[data-label*="Reports" i]',
            '[class*="report" i]',
            '#reports-btn',
            '.reports-btn',
            'li:has-text("Reports")',
            'span:has-text("Reports")',
            '[ng-click*="report" i]',
            '[onclick*="report" i]',
        ]

        clicked = False
        for sel in _reports_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    print(f"  Found Reports button via: {sel!r}")
                    loc.click(timeout=5_000)
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            print("ERROR: Could not find the Reports button.")
            print("Please inspect the debug screenshot (uploaded as artifact) and")
            print("check the 'Visible clickables' log above to find the correct selector.")
            page.screenshot(path=str(download_dir / "debug.png"))
            browser.close()
            sys.exit(1)

        page.wait_for_load_state("networkidle")
        time.sleep(1)

        # ── 3. Select report type: Calendar by Staff ──────────────────────
        print("Selecting 'Calendar by Staff' ...")

        # Print visible page state after clicking Reports
        try:
            buttons2 = page.locator("button, a, [role='button'], option, li").all()
            texts2 = []
            for b in buttons2[:50]:
                try:
                    t = b.inner_text().strip()
                    if t:
                        texts2.append(repr(t))
                except Exception:
                    pass
            print(f"  Post-Reports clickables (up to 50): {', '.join(texts2)}")
        except Exception as e:
            print(f"  Could not enumerate post-Reports clickables: {e}")

        try:
            # Try a <select> first, then fall back to clicking a list item
            sel = page.locator(
                'select[name*="report" i], select[id*="report" i], '
                'select[name*="type" i],   select[id*="type" i]'
            ).first
            if sel.count():
                sel.select_option(label="Calendar by Staff")
            else:
                page.click(':has-text("Calendar by Staff")', timeout=5_000)
        except Exception as e:
            print(f"  WARNING: report-type selector failed ({e}), continuing anyway")

        time.sleep(0.5)

        # ── 4. Select format: Excel ───────────────────────────────────────
        print("Selecting Excel format ...")
        try:
            fmt = page.locator(
                'select[name*="format" i], select[id*="format" i], '
                'select[name*="output" i], select[id*="output" i]'
            ).first
            if fmt.count():
                fmt.select_option(label="Excel")
            else:
                page.click(':has-text("Excel")', timeout=5_000)
        except Exception as e:
            print(f"  WARNING: format selector failed ({e}), continuing anyway")

        time.sleep(0.5)

        # ── 5. Set date range ─────────────────────────────────────────────
        print(f"Setting dates {start_date} → {end_date} ...")
        _fill_date(page, "start",  start_date)
        _fill_date(page, "from",   start_date)
        _fill_date(page, "begin",  start_date)
        _fill_date(page, "end",    end_date)
        _fill_date(page, "to",     end_date)
        _fill_date(page, "finish", end_date)
        time.sleep(0.5)

        # ── 6. Download ───────────────────────────────────────────────────
        print("Clicking download ...")
        try:
            with page.expect_download(timeout=90_000) as dl_info:
                page.click(
                    'button:has-text("Download"), '
                    'button:has-text("Generate"), '
                    'button:has-text("Run"), '
                    'button:has-text("Export"), '
                    'a:has-text("Download"), '
                    'a:has-text("Export")',
                    timeout=10_000,
                )
            download = dl_info.value
        except PWTimeout:
            print("ERROR: Download button not found or download timed out.")
            page.screenshot(path=str(download_dir / "debug.png"))
            browser.close()
            sys.exit(1)

        dest = download_dir / "schedule.xlsx"
        download.save_as(str(dest))
        size = dest.stat().st_size
        print(f"Downloaded schedule.xlsx ({size:,} bytes)")
        browser.close()

    return dest


def upload(file_path: Path) -> None:
    url  = f"{APP_URL}/upload-schedule"
    auth = ("user", ACCESS_CODE) if ACCESS_CODE else None
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    print(f"Uploading to {url} ...")
    with open(file_path, "rb") as f:
        resp = requests.post(
            url,
            files={"file": (file_path.name, f, mime)},
            auth=auth,
            timeout=120,
        )

    if not resp.ok:
        print(f"ERROR: Upload failed ({resp.status_code}): {resp.text}")
        sys.exit(1)

    data = resp.json()
    print(
        f"✓ Upload successful: {data.get('residents')} residents, "
        f"{data.get('inserted')} inserted, {data.get('updated')} updated."
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        excel_path = download_excel(Path(tmpdir))
        upload(excel_path)


if __name__ == "__main__":
    main()
