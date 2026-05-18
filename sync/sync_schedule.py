"""
Automated QGenda → Shift Swap Finder sync.

Logs into QGenda, exports the schedule CSV for the next N months,
and uploads it to the Shift Swap Finder app.

Environment variables required:
  QGENDA_EMAIL      - QGenda login email
  QGENDA_PASSWORD   - QGenda login password
  APP_URL           - Base URL of your deployed app, e.g. https://shift-swap.onrender.com
  APP_ACCESS_CODE   - The ACCESS_CODE set on Render (leave blank if none)
  MONTHS_AHEAD      - How many months to export (default: 3)
"""

import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

QGENDA_URL   = os.environ.get("QGENDA_URL", "https://app.qgenda.com")
EMAIL        = os.environ["QGENDA_EMAIL"]
PASSWORD     = os.environ["QGENDA_PASSWORD"]
APP_URL      = os.environ["APP_URL"].rstrip("/")
ACCESS_CODE  = os.environ.get("APP_ACCESS_CODE", "")
MONTHS_AHEAD = int(os.environ.get("MONTHS_AHEAD", "3"))


def month_range(months_ahead: int) -> tuple[str, str]:
    today = date.today()
    start = today.replace(day=1)
    # End = first day of (month + months_ahead) minus 1 day
    m = start.month + months_ahead
    y = start.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    end = date(y, m, 1) - timedelta(days=1)
    return start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y")


def login(page) -> None:
    print(f"Navigating to {QGENDA_URL} ...")
    page.goto(QGENDA_URL, wait_until="networkidle")

    # Fill email
    page.fill('input[type="email"], input[name="email"], #email', EMAIL)
    page.fill('input[type="password"], input[name="password"], #password', PASSWORD)
    page.click('button[type="submit"], input[type="submit"], .login-btn, button:has-text("Sign in"), button:has-text("Log in")')
    page.wait_for_load_state("networkidle")
    print("Logged in.")


def export_csv(page, start_date: str, end_date: str, download_dir: Path) -> Path:
    print(f"Exporting schedule {start_date} → {end_date} ...")

    # Navigate to Reports → Schedule Export
    # Try the direct URL path first; fall back to clicking through nav
    try:
        page.goto(f"{QGENDA_URL}/#/reports/scheduleexport", wait_until="networkidle", timeout=10_000)
    except PWTimeout:
        pass

    # If not on the export page, try to navigate via the menu
    if "scheduleexport" not in page.url.lower() and "export" not in page.url.lower():
        try:
            page.click('a:has-text("Reports"), button:has-text("Reports")', timeout=5_000)
            page.click('a:has-text("Schedule Export"), a:has-text("Export")', timeout=5_000)
            page.wait_for_load_state("networkidle")
        except PWTimeout:
            print("ERROR: Could not find the Reports → Schedule Export menu.")
            print("Please check QGENDA_NAVIGATION_NOTES in sync_schedule.py and update selectors.")
            sys.exit(1)

    # Set start date
    try:
        start_input = page.locator('input[placeholder*="start" i], input[name*="start" i], input[id*="start" i]').first
        start_input.fill("")
        start_input.type(start_date)
    except Exception as e:
        print(f"WARNING: Could not set start date: {e}")

    # Set end date
    try:
        end_input = page.locator('input[placeholder*="end" i], input[name*="end" i], input[id*="end" i]').first
        end_input.fill("")
        end_input.type(end_date)
    except Exception as e:
        print(f"WARNING: Could not set end date: {e}")

    # Click Apply / Generate / Run
    try:
        page.click('button:has-text("Apply"), button:has-text("Generate"), button:has-text("Run"), button:has-text("Export")', timeout=5_000)
        page.wait_for_load_state("networkidle")
        time.sleep(2)
    except PWTimeout:
        pass

    # Download the CSV
    with page.expect_download(timeout=60_000) as dl_info:
        page.click(
            'button:has-text("Download"), a:has-text("Download"), '
            'button:has-text("CSV"), a:has-text("CSV"), '
            'button:has-text("Export CSV"), a[href*=".csv"]'
        )
    download = dl_info.value
    dest = download_dir / "schedule.csv"
    download.save_as(str(dest))
    print(f"Downloaded CSV to {dest} ({dest.stat().st_size} bytes)")
    return dest


def upload_csv(csv_path: Path) -> None:
    url = f"{APP_URL}/upload-csv"
    auth = ("user", ACCESS_CODE) if ACCESS_CODE else None
    print(f"Uploading to {url} ...")
    with open(csv_path, "rb") as f:
        resp = requests.post(url, files={"file": ("schedule.csv", f, "text/csv")}, auth=auth, timeout=60)
    if not resp.ok:
        print(f"ERROR: Upload failed ({resp.status_code}): {resp.text}")
        sys.exit(1)
    data = resp.json()
    print(f"Upload successful: {data.get('residents')} residents, "
          f"{data.get('inserted')} inserted, {data.get('updated')} updated.")


def main():
    start_date, end_date = month_range(MONTHS_AHEAD)
    with tempfile.TemporaryDirectory() as tmpdir:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True, downloads_path=tmpdir)
            page = context.new_page()
            try:
                login(page)
                csv_path = export_csv(page, start_date, end_date, Path(tmpdir))
                upload_csv(csv_path)
            finally:
                browser.close()


if __name__ == "__main__":
    main()
