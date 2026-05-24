from __future__ import annotations
from datetime import date
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class ShiftType(str, Enum):
    DAY = "Day"
    SWING = "Swing"
    NIGHT = "Night"
    UNKNOWN = "Unknown"


class SeniorityLevel(str, Enum):
    R4 = "R4"   # 4th-year senior; required for FT Senior shifts
    SR = "Sr"   # 3rd-year senior
    JR = "Jr"   # 1st/2nd-year junior
    UNKNOWN = "Unknown"


# Maps shift type to an ordinal so we can enforce Day(0) → Swing(1) → Night(2)
SHIFT_ORDER: dict[ShiftType, int] = {
    ShiftType.DAY: 0,
    ShiftType.SWING: 1,
    ShiftType.NIGHT: 2,
    ShiftType.UNKNOWN: -1,
}


class ShiftAssignment(BaseModel):
    work_date: date
    shift_name: str
    shift_type: ShiftType
    seniority: SeniorityLevel
    shift_area: str = ""        # "Green", "Purple", "Peds", "Fast Track", "Triage", or ""
    is_swappable: bool = False  # False for Jeopardy, ICU, Vacation, etc.

    model_config = {"from_attributes": True}


class Resident(BaseModel):
    id: int
    name: str
    level: SeniorityLevel

    model_config = {"from_attributes": True}


class SwapOptionType(str, Enum):
    MUTUAL = "mutual"
    ONE_SIDED = "one_sided"


class MutualDetail(BaseModel):
    requester_covers_date: date
    requester_covers_shift_name: str
    requester_covers_shift_type: ShiftType
    requester_covers_shift_area: str = ""


class SwapOption(BaseModel):
    type: SwapOptionType
    coverer: Resident
    covered_shift_name: str
    covered_shift_type: ShiftType
    covered_shift_area: str = ""
    covered_seniority: SeniorityLevel
    mutual: Optional[MutualDetail] = None


class SwapLeg(BaseModel):
    """One participant's role in a marketplace swap cycle."""
    resident: Resident
    gives_up_date: date
    gives_up_shift_name: str
    gives_up_shift_type: ShiftType
    gives_up_shift_area: str
    picks_up_date: date
    picks_up_shift_name: str
    picks_up_shift_type: ShiftType
    picks_up_shift_area: str


class MarketplaceSwapCycle(BaseModel):
    cycle_length: int
    legs: list[SwapLeg]


class MarketplaceResult(BaseModel):
    cycles: list[MarketplaceSwapCycle]
    total_cycles: int
    skipped_requests: list[str]  # requested dates where resident has no swappable shift


class SwapRequest(BaseModel):
    resident_id: int
    request_date: date


class SwapResponse(BaseModel):
    requester: Resident
    request_date: date
    request_shift_name: str
    request_shift_area: str = ""
    mutual_options: list[SwapOption]
    one_sided_options: list[SwapOption]
