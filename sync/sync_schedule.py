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

        print(f"  Page title: {page.title()}")
        print(f"  Page URL:   {page.url}")

        # ── 2. Click the Reports sidebar item ────────────────────────────
        # The schedule is already loaded on the landing page.
        # The sidebar item reads "Reports - Printing and Exporting" and is
        # NOT a <button>/<a> — it's a <div> or <li>, so we use text= matching.
        print("Opening Reports panel ...")

        _reports_selectors = [
            'text=Reports - Printing and Exporting',
            ':text("Reports - Printing and Exporting")',
            'text=Reports',
            'li:has-text("Reports - Printing and Exporting")',
            'div:has-text("Reports - Printing and Exporting")',
            'span:has-text("Reports - Printing and Exporting")',
            'button:has-text("Reports")',
            'a:has-text("Reports")',
            '[aria-label*="Reports" i]',
            '[title*="Reports" i]',
            '[class*="report" i]',
        ]

        clicked = False
        for sel in _reports_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    tag = loc.evaluate("e => e.tagName")
                    txt = loc.inner_text().strip()[:60]
                    print(f"  Clicking <{tag}> {repr(txt)} via {sel!r}")
                    loc.click(timeout=5_000)
                    clicked = True
                    break
            except Exception as e:
                print(f"  Selector {sel!r} failed: {e}")

        if not clicked:
            print("ERROR: Could not find the Reports button.")
            page.screenshot(path=str(download_dir / "debug.png"))
            browser.close()
            sys.exit(1)

        time.sleep(2)  # wait for Reports panel to animate open

        # ── Dump everything visible in the Reports panel ──────────────────
        try:
            all_text = page.locator("select, input, button, a, option, li, div, label, span").all()
            panel_items = []
            for el in all_text[:80]:
                try:
                    t = el.inner_text().strip()
                    tag = el.evaluate("e => e.tagName")
                    typ = ""
                    try:
                        typ = el.get_attribute("type") or el.get_attribute("name") or el.get_attribute("id") or ""
                    except Exception:
                        pass
                    if t and len(t) < 80:
                        panel_items.append(f"{tag}[{typ}]:{repr(t)}")
                except Exception:
                    pass
            print(f"  Reports panel elements: {', '.join(panel_items[:60])}")
        except Exception as e:
            print(f"  Could not enumerate panel elements: {e}")

        page.screenshot(path=str(download_dir / "debug.png"))
        print("  Screenshot saved.")

        # ── 3. Select report type via React Select ────────────────────────
        # The dropdown uses class "qgenda-select__input-container" which intercepts
        # pointer events. Click that container directly (it IS the interactive layer).
        print("Selecting 'Calendar by Staff' ...")
        try:
            # Walk up from the known input id to its parent input-container div
            page.locator('#react-select-5-input').locator('xpath=..').click(timeout=5_000)
            time.sleep(0.5)
            page.get_by_role('option', name='Calendar by Staff').click(timeout=8_000)
            print("  ✓ Report type set")
        except Exception as e:
            print(f"  ERROR: Report type selection failed: {e}")
            page.screenshot(path=str(download_dir / "debug.png"))
            browser.close()
            sys.exit(1)

        time.sleep(1)

        # ── 4. Select format via React Select ────────────────────────────
        print("Selecting Excel format ...")
        try:
            page.locator('#react-select-6-input').locator('xpath=..').click(timeout=5_000)
            time.sleep(0.5)
            page.get_by_role('option', name='Excel').click(timeout=8_000)
            print("  ✓ Format set to Excel")
        except Exception as e:
            print(f"  ERROR: Format selection failed: {e}")
            page.screenshot(path=str(download_dir / "debug.png"))
            browser.close()
            sys.exit(1)

        time.sleep(1)

        # ── 5. Set date range ─────────────────────────────────────────────
        # Date inputs appear after type+format are selected; log them for debugging
        print(f"Setting dates {start_date} → {end_date} ...")
        try:
            inputs = page.locator("input").all()
            input_info = []
            for inp in inputs[:20]:
                try:
                    attrs = inp.evaluate(
                        "e => ({type:e.type, name:e.name, id:e.id, "
                        "placeholder:e.placeholder, value:e.value})"
                    )
                    input_info.append(str(attrs))
                except Exception:
                    pass
            print(f"  Inputs after type+format selection: {input_info}")
        except Exception as e:
            print(f"  Could not enumerate inputs: {e}")

        # Date inputs have no id/name/placeholder attributes at all (not even "").
        # CSS [attr=""] only matches explicitly empty attributes; XPath not(@attr)
        # correctly matches elements where the attribute is absent entirely.
        anon = page.locator(
            'xpath=//input[@type="text" and not(@id) and not(@name) and not(@placeholder)]'
        )
        try:
            start_inp = anon.nth(0)
            # Use React-compatible fill: set native value + fire input/change events
            start_inp.evaluate(
                """(el, v) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, v);
                    el.dispatchEvent(new Event('input',  {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                }""",
                start_date,
            )
            print(f"  ✓ Start date: {start_date}")
        except Exception as e:
            print(f"  WARNING: start date fill failed ({e})")

        try:
            end_inp = anon.nth(1)
            end_inp.evaluate(
                """(el, v) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, v);
                    el.dispatchEvent(new Event('input',  {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                }""",
                end_date,
            )
            print(f"  ✓ End date: {end_date}")
        except Exception as e:
            print(f"  WARNING: end date fill failed ({e})")

        time.sleep(0.5)

        # ── 6. Click "Run Report" (triggers the file download) ────────────
        # The button is a styled div, not a <button>, so use text= matching.
        print("Clicking 'Run Report' ...")
        try:
            with page.expect_download(timeout=90_000) as dl_info:
                page.locator('text=Run Report').last.click(timeout=10_000)
            download = dl_info.value
        except Exception as e:
            print(f"ERROR: Run Report failed: {e}")
            try:
                page.screenshot(path=str(download_dir / "debug.png"))
            except Exception:
                pass
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
