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

    # Check whether the requester giving away request_date (with nothing in return)
    # is itself valid — needed for the min-2 EM shifts / consecutive rules.
    requester_giveaway_ok = not validate_schedule_change(
        requester_schedule,
        add=[],
        remove=[request_date],
    )

    for coverer in all_residents:
        if coverer.id == requester.id:
            continue

        # --- Seniority / area gate ---
        if not can_cover(request_shift.seniority, coverer.level, request_shift.shift_area):
            continue

        coverer_schedule = all_schedules.get(coverer.id, {})

        # Coverer must be free on request_date
        if request_date in coverer_schedule:
            continue

        # Validate coverer picking up request_date
        if validate_schedule_change(
            coverer_schedule,
            add=[(request_date, request_shift)],
            remove=[],
        ):
            continue

        # --- Look for mutual swap days ---
        found_mutual = False
        for coverer_date, coverer_shift in sorted(coverer_schedule.items()):
            # Only swappable shifts can be offered as mutual swap days
            if not coverer_shift.is_swappable:
                continue

            # Requester must be free that day
            if coverer_date in requester_schedule and coverer_date != request_date:
                continue

            # Seniority / area gate for the reverse direction
            if not can_cover(coverer_shift.seniority, requester.level, coverer_shift.shift_area):
                continue

            # Validate requester's full exchange: remove request_date, add coverer_date.
            # Passing both in add/remove ensures every affected week is rule-checked.
            if validate_schedule_change(
                requester_schedule,
                add=[(coverer_date, coverer_shift)],
                remove=[request_date],
            ):
                continue

            # Re-validate coverer's side with the mutual removal
            if validate_schedule_change(
                coverer_schedule,
                add=[(request_date, request_shift)],
                remove=[coverer_date],
            ):
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

        if not found_mutual and requester_giveaway_ok:
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
