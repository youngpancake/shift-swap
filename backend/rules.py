from __future__ import annotations
"""
Swap rules engine.

All five rules are validated by validate_schedule_change(), which takes a
resident's current schedule plus a set of dates to add/remove and returns
a list of human-readable violation strings (empty = valid).
"""

from datetime import date, timedelta
from models import ShiftAssignment, ShiftType, SeniorityLevel, SHIFT_ORDER


def _week_sunday(d: date) -> date:
    """Return the Sunday that starts the Sun–Sat week containing d."""
    # weekday(): Mon=0 … Sun=6  →  offset to preceding Sunday
    return d - timedelta(days=(d.weekday() + 1) % 7)


def validate_schedule_change(
    current: dict[date, ShiftAssignment],
    add: list[tuple[date, ShiftAssignment]],
    remove: list[date],
) -> list[str]:
    """
    Simulate adding/removing shifts and return all rule violations.

    Rules checked:
      1. Minimum 1 day off per calendar week (Sun–Sat)
      2. Maximum 6 consecutive working days
      3. Shift waterfall: within a consecutive run, Day→Swing→Night only
    """
    modified: dict[date, ShiftAssignment] = {**current}
    for d in remove:
        modified.pop(d, None)
    for d, shift in add:
        modified[d] = shift

    working: set[date] = set(modified.keys())
    violations: list[str] = []

    # Only need to re-check weeks / runs that were actually touched
    touched = {d for d, _ in add} | set(remove)

    # --- Rule 1: Min 1 day off per week ---
    checked_sundays: set[date] = set()
    for d in touched:
        sunday = _week_sunday(d)
        if sunday in checked_sundays:
            continue
        checked_sundays.add(sunday)
        days_on = sum(1 for i in range(7) if (sunday + timedelta(i)) in working)
        if days_on == 7:
            violations.append(
                f"No day off in week of {sunday.strftime('%b %d')}–"
                f"{(sunday + timedelta(6)).strftime('%b %d')}"
            )

    # --- Rule 2: Max 6 consecutive shifts ---
    for d, _ in add:
        if d not in working:
            continue
        run_start = d
        while (run_start - timedelta(1)) in working:
            run_start -= timedelta(1)
        run_end = d
        while (run_end + timedelta(1)) in working:
            run_end += timedelta(1)
        run_len = (run_end - run_start).days + 1
        if run_len > 6:
            violations.append(
                f"Would create {run_len} consecutive shifts "
                f"({run_start.strftime('%b %d')}–{run_end.strftime('%b %d')})"
            )

    # --- Rule 3: Shift waterfall (forward only within consecutive runs) ---
    for d, shift in add:
        if shift.shift_type == ShiftType.UNKNOWN:
            continue
        new_order = SHIFT_ORDER[shift.shift_type]

        prev = d - timedelta(1)
        if prev in modified and modified[prev].shift_type != ShiftType.UNKNOWN:
            prev_order = SHIFT_ORDER[modified[prev].shift_type]
            if prev_order > new_order:
                violations.append(
                    f"Waterfall violation on {d.strftime('%b %d')}: "
                    f"{modified[prev].shift_type.value} → {shift.shift_type.value} "
                    f"(rotation must go Day→Swing→Night, not backward)"
                )

        nxt = d + timedelta(1)
        if nxt in modified and modified[nxt].shift_type != ShiftType.UNKNOWN:
            nxt_order = SHIFT_ORDER[modified[nxt].shift_type]
            if new_order > nxt_order:
                violations.append(
                    f"Waterfall violation on {d.strftime('%b %d')}: "
                    f"{shift.shift_type.value} → {modified[nxt].shift_type.value} "
                    f"the next day (rotation must go Day→Swing→Night, not backward)"
                )

    return violations


def can_cover(
    shift_seniority: SeniorityLevel,
    coverer_level: SeniorityLevel,
) -> bool:
    """
    Sr shift → only Sr can cover.
    Jr shift → anyone can cover.
    """
    if shift_seniority == SeniorityLevel.SR:
        return coverer_level == SeniorityLevel.SR
    return True  # Jr or Unknown shift: open to all
