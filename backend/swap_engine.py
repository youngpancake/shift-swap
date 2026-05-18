from __future__ import annotations
"""
Core swap-finding logic.

Given a requester and the date they want off, find every valid swap option
across all other residents. Mutual options (both parties exchange a shift)
are returned first; one-sided options (someone just picks up the shift) follow.
"""

from datetime import date
from models import (
    Resident, ShiftAssignment, ShiftType, SwapOption, SwapOptionType,
    MutualDetail, SeniorityLevel,
)
from rules import validate_schedule_change, can_cover

# Only clinical ED shifts can be swapped. Non-clinical entries (Vacation,
# ICU rotations, Jeopardy, etc.) are stored so they mark a resident as busy,
# but they can't be offered as swap targets.
# kept for waterfall rule checks; actual swappability uses shift.is_swappable
_CLINICAL_TYPES = {ShiftType.DAY, ShiftType.SWING, ShiftType.NIGHT}


def find_swap_options(
    requester: Resident,
    request_date: date,
    requester_schedule: dict[date, ShiftAssignment],
    all_residents: list[Resident],
    all_schedules: dict[int, dict[date, ShiftAssignment]],
) -> tuple[list[SwapOption], list[SwapOption]]:
    """
    Returns (mutual_options, one_sided_options), each sorted by coverer name.
    """
    if request_date not in requester_schedule:
        return [], []

    request_shift = requester_schedule[request_date]

    mutual: list[SwapOption] = []
    one_sided: list[SwapOption] = []

    # Requester's schedule after handing off request_date
    requester_minus = {d: s for d, s in requester_schedule.items() if d != request_date}

    for coverer in all_residents:
        if coverer.id == requester.id:
            continue

        # --- Seniority gate ---
        if not can_cover(request_shift.seniority, coverer.level, request_shift.shift_area):
            continue

        coverer_schedule = all_schedules.get(coverer.id, {})

        # Coverer must be free on request_date
        if request_date in coverer_schedule:
            continue

        # Validate coverer picking up request_date
        coverer_violations = validate_schedule_change(
            coverer_schedule,
            add=[(request_date, request_shift)],
            remove=[],
        )
        if coverer_violations:
            continue

        # --- Look for mutual swap days ---
        found_mutual = False
        for coverer_date, coverer_shift in sorted(coverer_schedule.items()):
            # Only swappable shifts can be offered as mutual swap days
            if not coverer_shift.is_swappable:
                continue

            # Requester must be free that day (after giving up request_date)
            if coverer_date in requester_minus:
                continue

            # Seniority gate for the reverse direction
            if not can_cover(coverer_shift.seniority, requester.level, coverer_shift.shift_area):
                continue

            # Validate requester picking up coverer_date
            req_violations = validate_schedule_change(
                requester_minus,
                add=[(coverer_date, coverer_shift)],
                remove=[],
            )
            if req_violations:
                continue

            # Also re-validate coverer's side with the mutual removal
            cov_mutual_violations = validate_schedule_change(
                coverer_schedule,
                add=[(request_date, request_shift)],
                remove=[coverer_date],
            )
            if cov_mutual_violations:
                continue

            mutual.append(
                SwapOption(
                    type=SwapOptionType.MUTUAL,
                    coverer=coverer,
                    covered_shift_name=request_shift.shift_name,
                    covered_shift_type=request_shift.shift_type,
                    covered_shift_area=request_shift.shift_area,
                    covered_seniority=request_shift.seniority,
                    mutual=MutualDetail(
                        requester_covers_date=coverer_date,
                        requester_covers_shift_name=coverer_shift.shift_name,
                        requester_covers_shift_type=coverer_shift.shift_type,
                        requester_covers_shift_area=coverer_shift.shift_area,
                    ),
                )
            )
            found_mutual = True

        if not found_mutual:
            one_sided.append(
                SwapOption(
                    type=SwapOptionType.ONE_SIDED,
                    coverer=coverer,
                    covered_shift_name=request_shift.shift_name,
                    covered_shift_type=request_shift.shift_type,
                    covered_shift_area=request_shift.shift_area,
                    covered_seniority=request_shift.seniority,
                )
            )

    mutual.sort(key=lambda o: o.coverer.name)
    one_sided.sort(key=lambda o: o.coverer.name)
    return mutual, one_sided
