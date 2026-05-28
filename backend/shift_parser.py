from __future__ import annotations
"""
Parse shift names from QGenda into ShiftType, SeniorityLevel, shift_area, and is_swappable.

QGenda shift names look like:
  "Adult ED Green Day Senior 7:30a-6p"
  "Adult ED Peds Night Junior 11:50p-9a"
  "Fast Track Swing Junior 1p-10p"
  "Fast Track Senior Day 7a-3p"   ← R4-only shift
  "Triage Day 7a-3p"
  "Jeopardy"
  "Vacation"
  "ICU"
  etc.
"""

import re
from models import ShiftType, SeniorityLevel

# ---- Shift type (time-of-day) ----
_DAY_RE   = re.compile(r'\b(day|days|am|morn|morning)\b', re.IGNORECASE)
_SWING_RE = re.compile(r'\b(swing|eve|evening|pm|mid|afternoon)\b', re.IGNORECASE)
_NIGHT_RE = re.compile(r'\b(night|nights|noc|nocs|overnight|graveyard)\b', re.IGNORECASE)
# Fallback: detect PM start time like "1p", "2:30p", "12p" when no keyword present
_PM_TIME_RE = re.compile(r'\b(?:1[0-2]|[1-9])(?::\d+)?p\b', re.IGNORECASE)

# ---- Seniority ----
_R4_RE = re.compile(r'\bR4\b', re.IGNORECASE)
_SR_RE = re.compile(r'\b(sr|senior|snr)\b', re.IGNORECASE)
_JR_RE = re.compile(r'\b(jr|junior)\b', re.IGNORECASE)

# Fast Track Senior: "Fast Track ... Senior", "FT Senior", or reversed
_FT_SENIOR_RE = re.compile(
    r'((fast.?track|ft).+\b(sr|senior|snr)\b|\b(sr|senior|snr)\b.+(fast.?track|ft))',
    re.IGNORECASE,
)

# ---- Shift area (location/track within the ED) ----
_AREA_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b(fast.?track|ft)\b', re.IGNORECASE), "Fast Track"),
    (re.compile(r'\btriage\b',           re.IGNORECASE), "Triage"),
    (re.compile(r'\bpeds\b',             re.IGNORECASE), "Peds"),
    (re.compile(r'\bgreen\b',            re.IGNORECASE), "Green"),
    (re.compile(r'\bpurple\b',           re.IGNORECASE), "Purple"),
]

# ---- Non-swappable shift names (exact or prefix match) ----
# These mark a resident as busy but cannot be requested or offered for swapping.
_NON_SWAPPABLE_RE = re.compile(
    r'^(jeopardy|icu|selective|elective|vacation|st\.?\s*mary|social|'
    r'lbm|off.?service|ems|base.?training|chief.?on.?call|'
    r'orientation|closed)',
    re.IGNORECASE,
)

# Entries to drop entirely — not shifts, just calendar noise
SKIP_ENTRY_RE = re.compile(r'^conference$', re.IGNORECASE)


def parse_shift_name(name: str) -> tuple[ShiftType, SeniorityLevel]:
    shift_type = ShiftType.UNKNOWN
    if _NIGHT_RE.search(name):
        shift_type = ShiftType.NIGHT
    elif _SWING_RE.search(name):
        shift_type = ShiftType.SWING
    elif _DAY_RE.search(name):
        shift_type = ShiftType.DAY
    elif _PM_TIME_RE.search(name):
        # Fallback for shifts like "FT Junior 1p-10p" that have no type keyword
        shift_type = ShiftType.SWING

    seniority = SeniorityLevel.UNKNOWN
    if _R4_RE.search(name):
        seniority = SeniorityLevel.R4
    elif _SR_RE.search(name):
        seniority = SeniorityLevel.SR
    elif _JR_RE.search(name):
        seniority = SeniorityLevel.JR

    return shift_type, seniority


def parse_shift_area(name: str) -> str:
    """Return the ED area/track for a shift name, or '' if not a clinical ED shift."""
    for pattern, area in _AREA_PATTERNS:
        if pattern.search(name):
            return area
    return ""


def is_shift_swappable(name: str, shift_type: ShiftType) -> bool:
    """
    A shift is swappable if:
    - It has a recognized time-of-day (Day/Swing/Night), AND
    - It is not explicitly non-swappable (Jeopardy, ICU, Vacation, etc.)
    """
    if _NON_SWAPPABLE_RE.match(name.strip()):
        return False
    return shift_type in (ShiftType.DAY, ShiftType.SWING, ShiftType.NIGHT)


def infer_resident_level(shift_names: list[str]) -> SeniorityLevel:
    """
    Infer R4/Sr/Jr from a resident's shift history.
    - Any explicit 'R4' label → R4
    - Works FT Senior shifts → R4 (only R4s staff those)
    - Otherwise majority Sr/Jr wins
    """
    r4_count    = sum(1 for n in shift_names if _R4_RE.search(n))
    ft_sr_count = sum(1 for n in shift_names if _FT_SENIOR_RE.search(n))
    sr_count    = sum(1 for n in shift_names if _SR_RE.search(n))
    jr_count    = sum(1 for n in shift_names if _JR_RE.search(n))

    if r4_count > 0 or ft_sr_count > 0:
        return SeniorityLevel.R4
    if sr_count > jr_count:
        return SeniorityLevel.SR
    if jr_count > sr_count:
        return SeniorityLevel.JR
    return SeniorityLevel.UNKNOWN
