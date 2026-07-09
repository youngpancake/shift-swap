from __future__ import annotations
"""
Swap rules engine.

Rules validated by validate_schedule_change():
  1. Min 1 EM-free day per Sun–Sat week  (non-EM shifts count as "off")
  2. Max 6 consecutive EM shifts          (non-EM days reset the run counter)
  3. Min 2 effective EM shifts per week   (orientation counts as 1; retreat week exempt)
  4. Shift waterfall Day→Swing→Night      (EM shifts only; non-EM days don't break waterfall)

can_cover() enforces seniority gating including the R4-only FT Senior rule.
"""

from datetime import date, timedelta
from models import ShiftAssignment, ShiftType, SeniorityLevel, SHIFT_ORDER

# EM shift types — non-EM shifts (ICU, Vacation, etc.) are transparent for rules 1-3
_EM_TYPES = {ShiftType.DAY, ShiftType.SWING, ShiftType.NIGHT}

# Retreat / off-service exemption dates (month, day) — min-2 rule does not apply
# to any Sun–Sat week that contains one of these dates.
_RETREAT_MD: set[tuple[int, int]] = {(9, 14), (9, 15)}


def _week_sunday(d: date) -> date:
    """Return the Sunday that starts the Sun–Sat week containing d."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _is_em(shift: ShiftAssignment) -> bool:
    return shift.shift_type in _EM_TYPES


def _is_orientation(shift: ShiftAssignment) -> bool:
    return shift.shift_name.lower().startswith("orientation")


def _is_retreat_week(sunday: date) -> bool:
    """True if this Sun–Sat week contains any retreat/off-service exempt date."""
    return any((sunday + timedelta(i)).month == m and (sunday + timedelta(i)).day == d
               for i in range(7) for m, d in _RETREAT_MD)


def validate_schedule_change(
    current: dict[date, ShiftAssignment],
    add: list[tuple[date, ShiftAssignment]],
    remove: list[date],
) -> list[str]:
    """
    Simulate adding/removing shifts and return all rule violations.
    Pass BOTH sides of a swap in add/remove so every affected week is checked.
    """
    modified: dict[date, ShiftAssignment] = {**current}
    for d in remove:
        modified.pop(d, None)
    for d, shift in add:
        modified[d] = shift

    # EM-only working days (non-EM = transparent for rules 1-3)
    em_days: set[date] = {d for d, s in modified.items() if _is_em(s)}

    violations: list[str] = []
    touched = {d for d, _ in add} | set(remove)
    checked_sundays: set[date] = set()

    for d in touched:
        sunday = _week_sunday(d)
        if sunday in checked_sundays:
            continue
        checked_sundays.add(sunday)

        week_dates = [sunday + timedelta(i) for i in range(7)]

        # ---- Rule 1: Min 1 EM-free day per week ----
        em_in_week = sum(1 for wd in week_dates if wd in em_days)
        if em_in_week == 7:
            violations.append(
                f"No EM-free day in week of "
                f"{sunday.strftime('%b %d')}–{(sunday + timedelta(6)).strftime('%b %d')}"
            )

        # ---- Rule 3: Min 2 effective EM shifts per week ----
        orientation_in_week = sum(
            1 for wd in week_dates
            if wd in modified and _is_orientation(modified[wd])
        )
        effective = em_in_week + min(orientation_in_week, 1)
        # Only applies if there are any EM/orientation obligations this week
        if em_in_week > 0 and effective < 2 and not _is_retreat_week(sunday):
            violations.append(
                f"Fewer than 2 EM shifts in week of "
                f"{sunday.strftime('%b %d')}–{(sunday + timedelta(6)).strftime('%b %d')} "
                f"({effective} scheduled)"
            )

    # ---- Rule 2: Max 6 consecutive EM shifts ----
    for d, shift in add:
        if not _is_em(shift) or d not in em_days:
            continue
        run_start = d
        while (run_start - timedelta(1)) in em_days:
            run_start -= timedelta(1)
        run_end = d
        while (run_end + timedelta(1)) in em_days:
            run_end += timedelta(1)
        run_len = (run_end - run_start).days + 1
        if run_len > 6:
            violations.append(
                f"Would create {run_len} consecutive EM shifts "
                f"({run_start.strftime('%b %d')}–{run_end.strftime('%b %d')})"
            )

    # ---- Rule 4: Shift waterfall (Day→Swing→Night, EM only) ----
    for d, shift in add:
        if shift.shift_type == ShiftType.UNKNOWN:
            continue
        new_order = SHIFT_ORDER[shift.shift_type]

        prev = d - timedelta(1)
        if prev in modified and _is_em(modified[prev]):
            prev_order = SHIFT_ORDER[modified[prev].shift_type]
            if prev_order > new_order:
                violations.append(
                    f"Waterfall violation on {d.strftime('%b %d')}: "
                    f"{modified[prev].shift_type.value} → {shift.shift_type.value} "
                    f"(must go Day→Swing→Night, not backward)"
                )

        nxt = d + timedelta(1)
        if nxt in modified and _is_em(modified[nxt]):
            nxt_order = SHIFT_ORDER[modified[nxt].shift_type]
            if new_order > nxt_order:
                violations.append(
                    f"Waterfall violation on {d.strftime('%b %d')}: "
                    f"{shift.shift_type.value} → {modified[nxt].shift_type.value} "
                    f"the next day (must go Day→Swing→Night, not backward)"
                )

    return violations


def can_cover(
    shift_seniority: SeniorityLevel,
    coverer_level: SeniorityLevel,
    shift_area: str = "",
) -> bool:
    """
    Seniority / area gating:
    - Fast Track Senior → R4 only
    - Triage / RME      → Sr or R4
    - R4-labeled shift  → R4 only
    - Sr-labeled shift  → Sr or R4
    - Jr / Unknown      → anyone
    """
    if shift_area == "Fast Track" and shift_seniority in (SeniorityLevel.SR, SeniorityLevel.R4):
        return coverer_level == SeniorityLevel.R4

    if shift_area in ("Triage", "RME"):
        return coverer_level in (SeniorityLevel.SR, SeniorityLevel.R4)

    if shift_seniority == SeniorityLevel.R4:
        return coverer_level == SeniorityLevel.R4

    if shift_seniority == SeniorityLevel.SR:
        return coverer_level in (SeniorityLevel.SR, SeniorityLevel.R4)

    return True  # Jr or Unknown shift: open to all
