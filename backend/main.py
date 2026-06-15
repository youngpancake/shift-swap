from __future__ import annotations
import os
import secrets
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Security, UploadFile, File, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from database import get_db, init_db, ResidentRow, ShiftAssignmentRow, MarketplaceRequestRow
from models import (
    Resident, ShiftAssignment, ShiftType, SeniorityLevel,
    SwapRequest, SwapResponse, MarketplaceResult,
)
from qgenda import parse_csv, parse_schedule_file, get_api_client
from shift_parser import infer_resident_level, is_shift_swappable
from swap_engine import find_swap_options
from marketplace import find_swap_cycles

# ---------------------------------------------------------------------------
# Optional password protection
# Set ACCESS_CODE env var to require a shared password.
# Leave unset for local development (no auth).
# ---------------------------------------------------------------------------
_http_basic = HTTPBasic(auto_error=False)


def require_auth(credentials: Optional[HTTPBasicCredentials] = Security(_http_basic)):
    access_code = os.getenv("ACCESS_CODE", "")
    if not access_code:
        return  # auth disabled locally
    if credentials is None or not secrets.compare_digest(
        credentials.password.encode(), access_code.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access code required",
            headers={"WWW-Authenticate": 'Basic realm="Shift Swap Finder"'},
        )


app = FastAPI(title="Shift Swap Finder", dependencies=[Depends(require_auth)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.on_event("startup")
def startup():
    init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_resident(row: ResidentRow) -> Resident:
    return Resident(id=row.id, name=row.name, level=SeniorityLevel(row.level))


def _empty_week_dates(shifts: dict[date, ShiftAssignment]) -> set[date]:
    """
    Return every date that falls inside a completely empty Mon–Sun week,
    bounded by the resident's first and last scheduled shift date.

    A week is "empty" if none of its 7 days has any shift in the database.
    This lets us infer ICU / vacation / off-service blocks that the public
    QGenda link omits.
    """
    if not shifts:
        return set()

    first = min(shifts)
    last  = max(shifts)

    # Walk week by week (Monday = weekday 0)
    monday = first - timedelta(days=first.weekday())
    blocked: set[date] = set()
    while monday <= last:
        week_days = [monday + timedelta(days=i) for i in range(7)]
        if not any(d in shifts for d in week_days):
            # Entire week is empty — block the days within the schedule span
            for d in week_days:
                if first <= d <= last:
                    blocked.add(d)
        monday += timedelta(days=7)

    return blocked


def _load_schedule(
    resident_ids: list[int], db: Session
) -> dict[int, dict[date, ShiftAssignment]]:
    rows = (
        db.query(ShiftAssignmentRow)
        .filter(ShiftAssignmentRow.resident_id.in_(resident_ids))
        .all()
    )
    schedules: dict[int, dict[date, ShiftAssignment]] = {rid: {} for rid in resident_ids}
    for r in rows:
        shift_type = ShiftType(r.shift_type)
        schedules[r.resident_id][r.work_date] = ShiftAssignment(
            work_date=r.work_date,
            shift_name=r.shift_name,
            shift_type=shift_type,
            seniority=SeniorityLevel(r.seniority),
            shift_area=r.shift_area or "",
            # Re-evaluate live so parser fixes apply without a re-upload
            is_swappable=is_shift_swappable(r.shift_name, shift_type),
        )

    # Inject synthetic "Off-Service" entries for every day that falls inside a
    # completely empty Mon–Sun week.  This handles ICU / vacation / off-service
    # rotations that don't appear on the public QGenda link.
    for rid, schedule in schedules.items():
        for d in _empty_week_dates(schedule):
            if d not in schedule:
                schedule[d] = ShiftAssignment(
                    work_date=d,
                    shift_name="Off-Service",
                    shift_type=ShiftType.UNKNOWN,
                    seniority=SeniorityLevel.UNKNOWN,
                    shift_area="",
                    is_swappable=False,
                )

    return schedules


def _deduplicate(parsed_rows: list[dict]) -> list[dict]:
    """
    Some residents appear more than once per day (e.g. Chief On Call + clinical shift).
    Keep the clinical entry (Day/Swing/Night); fall back to first occurrence.
    """
    _CLINICAL = {"Day", "Swing", "Night"}
    seen: dict[tuple, dict] = {}
    for row in parsed_rows:
        key = (row["name"], row["work_date"])
        if key not in seen:
            seen[key] = row
        else:
            existing = seen[key]
            if row["shift_type"] in _CLINICAL and existing["shift_type"] not in _CLINICAL:
                seen[key] = row
    return list(seen.values())


def _upsert_rows(parsed_rows: list[dict], db: Session) -> dict:
    parsed_rows = _deduplicate(parsed_rows)
    if not parsed_rows:
        return {"residents": 0, "inserted": 0, "deleted": 0}

    resident_names: dict[str, list[str]] = {}
    for row in parsed_rows:
        resident_names.setdefault(row["name"], []).append(row["shift_name"])

    # Upsert residents
    name_to_id: dict[str, int] = {}
    for name, shifts in resident_names.items():
        level = infer_resident_level(shifts).value
        existing = db.query(ResidentRow).filter(ResidentRow.name == name).first()
        if existing:
            existing.level = level
            name_to_id[name] = existing.id
        else:
            new_r = ResidentRow(name=name, level=level)
            db.add(new_r)
            db.flush()
            name_to_id[name] = new_r.id

    # Replace assignments within the upload's date range.
    # Delete first so removed/rescheduled shifts don't linger in the DB.
    all_dates = [row["work_date"] for row in parsed_rows]
    range_start, range_end = min(all_dates), max(all_dates)
    resident_ids = list(name_to_id.values())

    deleted = (
        db.query(ShiftAssignmentRow)
        .filter(
            ShiftAssignmentRow.resident_id.in_(resident_ids),
            ShiftAssignmentRow.work_date >= range_start,
            ShiftAssignmentRow.work_date <= range_end,
        )
        .delete(synchronize_session=False)
    )

    # Insert all rows fresh
    for row in parsed_rows:
        db.add(ShiftAssignmentRow(
            resident_id  = name_to_id[row["name"]],
            work_date    = row["work_date"],
            shift_name   = row["shift_name"],
            shift_type   = row["shift_type"],
            seniority    = row["seniority"],
            shift_area   = row.get("shift_area", ""),
            is_swappable = row.get("is_swappable", False),
        ))

    db.commit()
    return {
        "residents": len(name_to_id),
        "inserted": len(parsed_rows),
        "deleted": deleted,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/upload-schedule")
async def upload_schedule(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a QGenda schedule export — accepts both Excel (.xlsx) and CSV."""
    content = await file.read()
    filename = file.filename or ""
    try:
        rows = parse_schedule_file(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not rows:
        raise HTTPException(status_code=422, detail="No valid rows found in schedule file.")
    return _upsert_rows(rows, db)


@app.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a QGenda CSV schedule export. (Legacy — prefer /upload-schedule)"""
    content = await file.read()
    try:
        rows = parse_csv(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not rows:
        raise HTTPException(status_code=422, detail="No valid rows found in CSV.")
    return _upsert_rows(rows, db)


@app.post("/sync-qgenda")
def sync_qgenda(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """Sync schedule from QGenda API. Requires QGENDA_* env vars."""
    client = get_api_client()
    if not client:
        raise HTTPException(
            status_code=503,
            detail=(
                "QGenda API credentials not configured. "
                "Set QGENDA_EMAIL, QGENDA_PASSWORD, and QGENDA_COMPANY_KEY "
                "environment variables."
            ),
        )
    try:
        rows = client.fetch_schedule(start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"QGenda API error: {e}")
    if not rows:
        return {"residents": 0, "inserted": 0, "updated": 0}
    return _upsert_rows(rows, db)


@app.get("/residents", response_model=list[Resident])
def list_residents(db: Session = Depends(get_db)):
    rows = db.query(ResidentRow).order_by(ResidentRow.name).all()
    return [_row_to_resident(r) for r in rows]


@app.patch("/residents/{resident_id}")
def rename_resident(
    resident_id: int,
    body: dict,
    db: Session = Depends(get_db),
):
    """Rename a resident. Body: {"name": "New Name"}"""
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="Name cannot be empty.")
    row = db.query(ResidentRow).filter(ResidentRow.id == resident_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Resident not found.")
    conflict = db.query(ResidentRow).filter(ResidentRow.name == new_name).first()
    if conflict and conflict.id != resident_id:
        raise HTTPException(status_code=409, detail=f'A resident named "{new_name}" already exists.')
    row.name = new_name
    db.commit()
    return _row_to_resident(row)


@app.post("/residents/merge")
def merge_residents(
    body: dict,
    db: Session = Depends(get_db),
):
    """
    Merge a duplicate resident into the canonical one.
    Body: {"keep_id": <int>, "delete_id": <int>}
    All shift assignments from delete_id are moved to keep_id
    (skipping any date where keep_id already has a shift).
    The duplicate resident row is then deleted.
    """
    keep_id   = body.get("keep_id")
    delete_id = body.get("delete_id")
    if not keep_id or not delete_id or keep_id == delete_id:
        raise HTTPException(status_code=422, detail="Provide distinct keep_id and delete_id.")

    keep_row   = db.query(ResidentRow).filter(ResidentRow.id == keep_id).first()
    delete_row = db.query(ResidentRow).filter(ResidentRow.id == delete_id).first()
    if not keep_row or not delete_row:
        raise HTTPException(status_code=404, detail="One or both residents not found.")

    # Dates already covered by the resident we're keeping
    keep_dates = {
        r.work_date
        for r in db.query(ShiftAssignmentRow)
        .filter(ShiftAssignmentRow.resident_id == keep_id)
        .all()
    }

    # Re-assign shifts from duplicate → canonical (skip conflicts)
    moved = skipped = 0
    for shift in (
        db.query(ShiftAssignmentRow)
        .filter(ShiftAssignmentRow.resident_id == delete_id)
        .all()
    ):
        if shift.work_date in keep_dates:
            skipped += 1
        else:
            shift.resident_id = keep_id
            moved += 1

    # Delete the duplicate resident (cascade handles any remaining rows)
    db.delete(delete_row)
    db.commit()

    return {
        "kept": keep_row.name,
        "deleted": delete_row.name,
        "shifts_moved": moved,
        "shifts_skipped_conflict": skipped,
    }


@app.get("/residents/{resident_id}/schedule", response_model=list[ShiftAssignment])
def get_resident_schedule(
    resident_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    # Load the FULL schedule first (no date filter) so that empty-week
    # detection is accurate regardless of the requested display window.
    schedules = _load_schedule([resident_id], db)
    schedule = schedules.get(resident_id, {})

    return [
        sa for d, sa in sorted(schedule.items())
        if (start_date is None or d >= start_date)
        and (end_date is None or d <= end_date)
    ]


@app.post("/swap-options", response_model=SwapResponse)
def get_swap_options(req: SwapRequest, db: Session = Depends(get_db)):
    requester_row = db.query(ResidentRow).filter(ResidentRow.id == req.resident_id).first()
    if not requester_row:
        raise HTTPException(status_code=404, detail="Resident not found.")

    requester = _row_to_resident(requester_row)
    all_resident_rows = db.query(ResidentRow).all()
    all_residents = [_row_to_resident(r) for r in all_resident_rows]
    all_ids = [r.id for r in all_residents]

    all_schedules = _load_schedule(all_ids, db)
    requester_schedule = all_schedules.get(req.resident_id, {})

    if req.request_date not in requester_schedule:
        raise HTTPException(
            status_code=400,
            detail=f"{requester.name} is not scheduled to work on {req.request_date}.",
        )

    request_shift = requester_schedule[req.request_date]

    if not request_shift.is_swappable:
        raise HTTPException(
            status_code=400,
            detail=f'"{request_shift.shift_name}" cannot be swapped through this app.',
        )

    mutual, one_sided = find_swap_options(
        requester=requester,
        request_date=req.request_date,
        requester_schedule=requester_schedule,
        all_residents=all_residents,
        all_schedules=all_schedules,
    )

    return SwapResponse(
        requester=requester,
        request_date=req.request_date,
        request_shift_name=request_shift.shift_name,
        request_shift_area=request_shift.shift_area,
        mutual_options=mutual,
        one_sided_options=one_sided,
    )


@app.get("/admin/shift-names")
def list_shift_names(db: Session = Depends(get_db)):
    """Return every distinct shift name in the DB with its parsed type and swappable flag."""
    from shift_parser import parse_shift_name, is_shift_swappable
    rows = db.query(ShiftAssignmentRow.shift_name, ShiftAssignmentRow.shift_type).distinct().all()
    results = []
    for shift_name, stored_type in sorted(set(rows)):
        shift_type, _ = parse_shift_name(shift_name)
        results.append({
            "shift_name": shift_name,
            "stored_type": stored_type,
            "parsed_type": shift_type.value,
            "is_swappable": is_shift_swappable(shift_name, shift_type),
        })
    return results


@app.get("/schedule-summary")
def schedule_summary(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """Return working days for all residents in a date range (for the calendar view)."""
    rows = (
        db.query(ShiftAssignmentRow)
        .filter(
            ShiftAssignmentRow.work_date >= start_date,
            ShiftAssignmentRow.work_date <= end_date,
        )
        .all()
    )
    # resident_id -> list of {date, shift_name, shift_type, seniority}
    result: dict[int, list[dict]] = {}
    for r in rows:
        result.setdefault(r.resident_id, []).append(
            {
                "date": r.work_date.isoformat(),
                "shift_name": r.shift_name,
                "shift_type": r.shift_type,
                "seniority": r.seniority,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------

def _match_resident_name(raw_name: str, residents: list[ResidentRow]) -> Optional[ResidentRow]:
    """Match a name from the requests CSV to a ResidentRow (case-insensitive).
    Tries exact match, then reversed 'First Last' → 'Last, First'."""
    normalized = raw_name.strip().lower()
    for r in residents:
        if r.name.lower() == normalized:
            return r
    # Try reversing "First Last" → "Last, First"
    parts = raw_name.strip().split()
    if len(parts) >= 2:
        reversed_name = f"{parts[-1]}, {' '.join(parts[:-1])}".lower()
        for r in residents:
            if r.name.lower() == reversed_name:
                return r
    return None


@app.post("/marketplace/requests")
async def upload_marketplace_requests(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a CSV of date requests. Columns: Name, Date (one request per row)."""
    import csv as csv_mod
    from io import StringIO

    content = (await file.read()).decode("utf-8-sig")
    reader = csv_mod.DictReader(StringIO(content))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV has no header row.")

    # Find name and date columns (case-insensitive)
    fields = [f.strip() for f in reader.fieldnames]
    name_col = next((f for f in fields if "name" in f.lower()), None)
    date_col = next((f for f in fields if "date" in f.lower()), None)
    if not name_col or not date_col:
        raise HTTPException(
            status_code=422,
            detail=f"Need 'Name' and 'Date' columns. Found: {fields}"
        )

    from dateutil import parser as dateutil_parser
    all_residents = db.query(ResidentRow).all()
    skipped: list[str] = []
    added = 0

    for row in reader:
        raw_name = (row.get(name_col) or "").strip()
        raw_date = (row.get(date_col) or "").strip()
        if not raw_name or not raw_date:
            continue
        resident = _match_resident_name(raw_name, all_residents)
        if not resident:
            skipped.append(f"{raw_name}: not found in schedule")
            continue
        try:
            req_date = dateutil_parser.parse(raw_date).date()
        except Exception:
            skipped.append(f"{raw_name} — bad date: {raw_date}")
            continue

        existing = (
            db.query(MarketplaceRequestRow)
            .filter(MarketplaceRequestRow.resident_id == resident.id,
                    MarketplaceRequestRow.requested_date == req_date)
            .first()
        )
        if not existing:
            db.add(MarketplaceRequestRow(resident_id=resident.id, requested_date=req_date))
            added += 1

    db.commit()
    return {"added": added, "skipped": skipped}


@app.get("/marketplace/requests")
def list_marketplace_requests(db: Session = Depends(get_db)):
    """Return all current date requests grouped by resident."""
    rows = db.query(MarketplaceRequestRow).all()
    residents = {r.id: r.name for r in db.query(ResidentRow).all()}
    result: dict[str, list[str]] = {}
    for r in rows:
        name = residents.get(r.resident_id, f"ID {r.resident_id}")
        result.setdefault(name, []).append(r.requested_date.isoformat())
    return result


@app.delete("/marketplace/requests")
def clear_marketplace_requests(db: Session = Depends(get_db)):
    """Delete all marketplace date requests."""
    deleted = db.query(MarketplaceRequestRow).delete()
    db.commit()
    return {"deleted": deleted}


@app.get("/marketplace/cycles", response_model=MarketplaceResult)
def get_marketplace_cycles(db: Session = Depends(get_db)):
    """Find all valid swap cycles across the current set of date requests."""
    request_rows = db.query(MarketplaceRequestRow).all()
    if not request_rows:
        return MarketplaceResult(cycles=[], total_cycles=0, skipped_requests=[])

    resident_rows = db.query(ResidentRow).all()
    residents_map: dict[int, Resident] = {
        r.id: Resident(id=r.id, name=r.name, level=SeniorityLevel(r.level))
        for r in resident_rows
    }

    # Load schedules for all residents who have requests
    involved_ids = list({r.resident_id for r in request_rows})
    schedules = _load_schedule(involved_ids, db)

    requests = [(r.resident_id, r.requested_date) for r in request_rows]

    # Flag requests where the resident has no swappable shift that day
    skipped: list[str] = []
    for (rid, d) in requests:
        shift = schedules.get(rid, {}).get(d)
        name = residents_map.get(rid, Resident(id=rid, name=f"ID {rid}", level=SeniorityLevel.UNKNOWN)).name
        if not shift:
            skipped.append(f"{name} — not scheduled on {d}")
        elif not shift.is_swappable:
            skipped.append(f"{name} on {d} — shift '{shift.shift_name}' is not swappable")

    cycles = find_swap_cycles(requests, schedules, residents_map)
    return MarketplaceResult(cycles=cycles, total_cycles=len(cycles), skipped_requests=skipped)
