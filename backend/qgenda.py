from __future__ import annotations
"""
QGenda integration — two modes:

1. CSV upload  (works today, no credentials needed)
   QGenda → Reports → Schedule Export → download CSV
   Handles QGenda's calendar-grid format (the default export).

2. API sync  (requires QGenda API credentials from your admin)
   Set QGENDA_EMAIL, QGENDA_PASSWORD, QGENDA_COMPANY_KEY
   in the environment or a .env file, then use /sync-qgenda.
"""

import csv
import os
import re
from datetime import date, datetime
from io import StringIO
from typing import Optional

import httpx
from dateutil import parser as dateutil_parser

from shift_parser import parse_shift_name, parse_shift_area, is_shift_swappable, infer_resident_level, SKIP_ENTRY_RE
from models import SeniorityLevel


# ---------------------------------------------------------------------------
# Calendar-grid CSV parser  (QGenda's default export format)
# ---------------------------------------------------------------------------
# Layout per week block:
#   Row 1:  Sunday,,Monday,,Tuesday,,Wednesday,,Thursday,,Friday,,Saturday,
#   Row 2:  "June 28, 2026",,"June 29, 2026",,... (dates in every other col)
#   Row N+: Name, Shift, Name, Shift, ...   (14 cols = 7 days × 2)

_DAY_HEADER_RE = re.compile(
    r'^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)$',
    re.IGNORECASE,
)
_DATE_RE = re.compile(r'^\w+ \d{1,2},\s+\d{4}$')  # "July 5, 2026"

# QGenda placeholders that are not real people
_NON_PERSON_RE = re.compile(r'^(CLOSED|TBD|OPEN|VACANT|UNFILLED)$', re.IGNORECASE)


def _clean_name(raw: str) -> str:
    """'Arriaga-Castellanos (Arriaga-Castella)' → 'Arriaga-Castellanos'"""
    return re.sub(r'\s*\([^)]*\)\s*$', '', raw.strip()).strip()


def parse_qgenda_calendar(content: str | bytes) -> list[dict]:
    """
    Parse QGenda's calendar-grid CSV.
    Returns [{ name, work_date, shift_name, shift_type, seniority }, ...]
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")

    reader = csv.reader(StringIO(content))
    rows = list(reader)

    current_dates: list[Optional[date]] = [None] * 7
    results: list[dict] = []

    for row in rows:
        # Normalize to 14 columns
        while len(row) < 14:
            row.append("")

        cells = [c.strip() for c in row]
        first = next((c for c in cells if c), "")

        if not first:
            continue

        # Day-of-week header row → skip
        if _DAY_HEADER_RE.match(first):
            continue

        # Date row → capture the 7 dates (every other column starting at 0)
        if _DATE_RE.match(first):
            for i in range(7):
                raw = cells[i * 2]
                if _DATE_RE.match(raw):
                    try:
                        current_dates[i] = dateutil_parser.parse(raw).date()
                    except Exception:
                        current_dates[i] = None
                else:
                    current_dates[i] = None
            continue

        # Skip title / metadata rows (no dates loaded yet, or non-data content)
        if not any(current_dates):
            continue

        # Data row: 7 pairs of (name, shift)
        for i in range(7):
            work_date = current_dates[i]
            if work_date is None:
                continue

            name_raw = cells[i * 2]
            shift_raw = cells[i * 2 + 1] if i * 2 + 1 < len(cells) else ""

            if not name_raw or not shift_raw:
                continue

            name = _clean_name(name_raw)
            if not name or _NON_PERSON_RE.match(name):
                continue

            if SKIP_ENTRY_RE.match(shift_raw):
                continue

            shift_type, seniority = parse_shift_name(shift_raw)
            results.append(
                {
                    "name": name,
                    "work_date": work_date,
                    "shift_name": shift_raw,
                    "shift_type": shift_type.value,
                    "seniority": seniority.value,
                    "shift_area": parse_shift_area(shift_raw),
                    "is_swappable": is_shift_swappable(shift_raw, shift_type),
                }
            )

    return results


# ---------------------------------------------------------------------------
# Generic tabular CSV fallback
# ---------------------------------------------------------------------------
# Expected columns (auto-detected, case-insensitive):
#   date / start date       → work date
#   staff name / resident   → resident name
#   task / shift / shift name → shift name

_COL_DATE = re.compile(r'(^date$|start.?date|work.?date)', re.IGNORECASE)
_COL_NAME = re.compile(r'(staff.?name|resident|employee|name)', re.IGNORECASE)
_COL_SHIFT = re.compile(r'(task|shift.?name|shift|activity)', re.IGNORECASE)


def _find_col(columns: list[str], pattern: re.Pattern) -> Optional[str]:
    for c in columns:
        if pattern.search(c):
            return c
    return None


def _parse_tabular(content: str) -> list[dict]:
    reader = csv.DictReader(StringIO(content))
    cols = reader.fieldnames or []

    date_col = _find_col(list(cols), _COL_DATE)
    name_col = _find_col(list(cols), _COL_NAME)
    shift_col = _find_col(list(cols), _COL_SHIFT)

    missing = [
        label for label, col in [("date", date_col), ("name", name_col), ("shift", shift_col)]
        if col is None
    ]
    if missing:
        raise ValueError(
            f"Could not find columns for: {', '.join(missing)}. "
            f"Available columns: {list(cols)}"
        )

    rows = []
    for row in reader:
        raw_date = row[date_col].strip()
        if not raw_date:
            continue
        try:
            work_date = dateutil_parser.parse(raw_date).date()
        except Exception:
            continue

        name = row[name_col].strip()
        shift_name = row[shift_col].strip()
        if not name or not shift_name:
            continue

        shift_type, seniority = parse_shift_name(shift_name)
        rows.append(
            {
                "name": name,
                "work_date": work_date,
                "shift_name": shift_name,
                "shift_type": shift_type.value,
                "seniority": seniority.value,
                "shift_area": parse_shift_area(shift_name),
                "is_swappable": is_shift_swappable(shift_name, shift_type),
            }
        )
    return rows


def parse_csv(content: str | bytes) -> list[dict]:
    """
    Auto-detect format and parse.
    Tries calendar-grid first (QGenda default), falls back to tabular.
    """
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig")
    else:
        text = content

    # Try calendar format: look for a day-of-week header in the first 10 rows
    sample_rows = text.split("\n")[:10]
    is_calendar = any(
        _DAY_HEADER_RE.match(r.split(",")[0].strip()) for r in sample_rows
    )

    if is_calendar:
        return parse_qgenda_calendar(text)

    try:
        return _parse_tabular(text)
    except Exception as e:
        raise ValueError(f"Could not parse CSV: {e}")


# ---------------------------------------------------------------------------
# QGenda REST API
# ---------------------------------------------------------------------------

QGENDA_BASE = "https://qgenda.com/api"


class QGendaClient:
    def __init__(self, email: str, password: str, company_key: str):
        self.email = email
        self.password = password
        self.company_key = company_key
        self._token: Optional[str] = None

    def _authenticate(self) -> str:
        resp = httpx.post(
            f"{QGENDA_BASE}/login",
            data={
                "email": self.email,
                "password": self.password,
                "companyKey": self.company_key,
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        if not self._token:
            self._authenticate()
        return {"Authorization": f"Bearer {self._token}"}

    def fetch_schedule(self, start_date: date, end_date: date) -> list[dict]:
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "companyKey": self.company_key,
            "$select": "StaffFName,StaffLName,TaskName,StartDate",
            "$orderby": "StartDate",
        }
        resp = httpx.get(
            f"{QGENDA_BASE}/schedule",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        rows = []
        for item in resp.json():
            name = f"{item.get('StaffLName', '')}, {item.get('StaffFName', '')}".strip(", ")
            shift_name = item.get("TaskName", "")
            raw_date = item.get("StartDate", "")
            try:
                work_date = datetime.fromisoformat(raw_date).date()
            except Exception:
                continue
            shift_type, seniority = parse_shift_name(shift_name)
            rows.append(
                {
                    "name": name,
                    "work_date": work_date,
                    "shift_name": shift_name,
                    "shift_type": shift_type.value,
                    "seniority": seniority.value,
                    "shift_area": parse_shift_area(shift_name),
                    "is_swappable": is_shift_swappable(shift_name, shift_type),
                }
            )
        return rows


def get_api_client() -> Optional[QGendaClient]:
    email = os.getenv("QGENDA_EMAIL")
    password = os.getenv("QGENDA_PASSWORD")
    company_key = os.getenv("QGENDA_COMPANY_KEY")
    if email and password and company_key:
        return QGendaClient(email, password, company_key)
    return None
