from __future__ import annotations
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Security, UploadFile, File, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from database import get_db, init_db, ResidentRow, ShiftAssignmentRow
from models import (
    Resident, ShiftAssignment, ShiftType, SeniorityLevel,
    SwapRequest, SwapResponse,
)
from qgenda import parse_csv, get_api_client
from shift_parser import infer_resident_level
from swap_engine import find_swap_options

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
        schedules[r.resident_id][r.work_date] = ShiftAssignment(
            work_date=r.work_date,
            shift_name=r.shift_name,
            shift_type=ShiftType(r.shift_type),
            seniority=SeniorityLevel(r.seniority),
            shift_area=r.shift_area or "",
            is_swappable=bool(r.is_swappable),
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

    # Upsert assignments
    # Some residents appear multiple times on the same day (e.g. Chief On Call
    # plus a clinical shift). Prefer clinical shifts over Unknown-type entries.
    _CLINICAL = {"Day", "Swing", "Night"}
    inserted = updated = skipped = 0
    for row in parsed_rows:
        rid = name_to_id[row["name"]]
        existing = (
            db.query(ShiftAssignmentRow)
            .filter(
                ShiftAssignmentRow.resident_id == rid,
                ShiftAssignmentRow.work_date == row["work_date"],
            )
            .first()
        )
        if existing:
            existing_clinical = existing.shift_type in _CLINICAL
            new_clinical = row["shift_type"] in _CLINICAL
            if new_clinical or not existing_clinical:
                existing.shift_name   = row["shift_name"]
                existing.shift_type   = row["shift_type"]
                existing.seniority    = row["seniority"]
                existing.shift_area   = row.get("shift_area", "")
                existing.is_swappable = row.get("is_swappable", False)
                updated += 1
            else:
                skipped += 1
        else:
            db.add(
                ShiftAssignmentRow(
                    resident_id  = rid,
                    work_date    = row["work_date"],
                    shift_name   = row["shift_name"],
                    shift_type   = row["shift_type"],
                    seniority    = row["seniority"],
                    shift_area   = row.get("shift_area", ""),
                    is_swappable = row.get("is_swappable", False),
                )
            )
            inserted += 1

    db.commit()
    return {
        "residents": len(name_to_id),
        "inserted": inserted,
        "updated": updated,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a QGenda CSV schedule export."""
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


@app.get("/residents/{resident_id}/schedule", response_model=list[ShiftAssignment])
def get_resident_schedule(
    resident_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ShiftAssignmentRow).filter(
        ShiftAssignmentRow.resident_id == resident_id
    )
    if start_date:
        q = q.filter(ShiftAssignmentRow.work_date >= start_date)
    if end_date:
        q = q.filter(ShiftAssignmentRow.work_date <= end_date)
    rows = q.order_by(ShiftAssignmentRow.work_date).all()
    return [
        ShiftAssignment(
            work_date=r.work_date,
            shift_name=r.shift_name,
            shift_type=ShiftType(r.shift_type),
            seniority=SeniorityLevel(r.seniority),
            shift_area=r.shift_area or "",
            is_swappable=bool(r.is_swappable),
        )
        for r in rows
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
