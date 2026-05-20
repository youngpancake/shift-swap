from __future__ import annotations
"""
Swap marketplace: find multi-party swap cycles.

Given a list of (resident_id, wanted_date) requests, build a directed graph
where edge (A, date_a) → (B, date_b) means "B can cover A's shift on date_a".
Then find all simple directed cycles of length 2–max_len where:
  - Every participant gets their requested date off
  - All scheduling rules are satisfied for every participant
"""

from datetime import date
from models import Resident, ShiftAssignment, SwapLeg, MarketplaceSwapCycle
from rules import can_cover, validate_schedule_change

# Node in the graph: (resident_id, date_they_want_off)
Node = tuple[int, date]


def _build_graph(
    requests: list[Node],
    schedules: dict[int, dict[date, ShiftAssignment]],
    residents: dict[int, Resident],
) -> dict[Node, list[Node]]:
    """
    Edge (A, date_a) → (B, date_b):
      - A has a swappable shift on date_a
      - B is free on date_a
      - B can cover A's shift (seniority + area gates)
    """
    graph: dict[Node, list[Node]] = {node: [] for node in requests}
    for (rid_a, date_a) in requests:
        shift_a = schedules.get(rid_a, {}).get(date_a)
        if not shift_a or not shift_a.is_swappable:
            continue
        for (rid_b, date_b) in requests:
            if rid_b == rid_a:
                continue
            if date_a in schedules.get(rid_b, {}):
                continue  # B already working that day
            if not can_cover(shift_a.seniority, residents[rid_b].level, shift_a.shift_area):
                continue
            graph[(rid_a, date_a)].append((rid_b, date_b))
    return graph


def _canonicalize(cycle: list[Node]) -> tuple:
    """Rotate cycle to start at the smallest node so duplicates collapse."""
    min_i = min(range(len(cycle)), key=lambda i: (cycle[i][0], str(cycle[i][1])))
    r = cycle[min_i:] + cycle[:min_i]
    return tuple(r)


def _find_raw_cycles(graph: dict[Node, list[Node]], max_len: int) -> list[list[Node]]:
    seen: set[tuple] = set()
    results: list[list[Node]] = []

    for start in graph:
        # DFS: (current_node, path_so_far, set_of_resident_ids_in_path)
        stack = [(start, [start], {start[0]})]
        while stack:
            node, path, seen_rids = stack.pop()
            # Try to close the cycle back to start
            if len(path) >= 2 and start in graph.get(node, []):
                canon = _canonicalize(path)
                if canon not in seen:
                    seen.add(canon)
                    results.append(list(path))
            if len(path) >= max_len:
                continue
            for nb in graph.get(node, []):
                if nb[0] in seen_rids:
                    continue
                if nb == start:
                    continue  # closing handled above
                stack.append((nb, path + [nb], seen_rids | {nb[0]}))

    return results


def _validate_cycle(cycle: list[Node], schedules: dict[int, dict[date, ShiftAssignment]]) -> bool:
    """
    In cycle [node_0, node_1, ..., node_{k-1}]:
      node_i.resident gives up node_i.date
      node_i.resident picks up node_{(i-1) % k}.date (takes the previous person's shift)
    Validate schedule rules for every participant.
    """
    k = len(cycle)
    for i, (rid, give_up_date) in enumerate(cycle):
        prev_rid, prev_date = cycle[(i - 1) % k]
        pick_up_shift = schedules[prev_rid][prev_date]
        violations = validate_schedule_change(
            schedules[rid],
            add=[(prev_date, pick_up_shift)],
            remove=[give_up_date],
        )
        if violations:
            return False
    return True


def _build_cycle_model(
    cycle: list[Node],
    schedules: dict[int, dict[date, ShiftAssignment]],
    residents: dict[int, Resident],
) -> MarketplaceSwapCycle:
    k = len(cycle)
    legs: list[SwapLeg] = []
    for i, (rid, give_up_date) in enumerate(cycle):
        prev_rid, prev_date = cycle[(i - 1) % k]
        give_shift = schedules[rid][give_up_date]
        pick_shift = schedules[prev_rid][prev_date]
        legs.append(SwapLeg(
            resident=residents[rid],
            gives_up_date=give_up_date,
            gives_up_shift_name=give_shift.shift_name,
            gives_up_shift_type=give_shift.shift_type,
            gives_up_shift_area=give_shift.shift_area,
            picks_up_date=prev_date,
            picks_up_shift_name=pick_shift.shift_name,
            picks_up_shift_type=pick_shift.shift_type,
            picks_up_shift_area=pick_shift.shift_area,
        ))
    return MarketplaceSwapCycle(cycle_length=k, legs=legs)


def find_swap_cycles(
    requests: list[Node],
    schedules: dict[int, dict[date, ShiftAssignment]],
    residents: dict[int, Resident],
    max_cycle_length: int = 5,
) -> list[MarketplaceSwapCycle]:
    """
    Returns valid swap cycles sorted by length (2-way first).
    """
    # Only keep requests where the resident has a swappable shift that day
    valid_requests = [
        (rid, d) for (rid, d) in requests
        if schedules.get(rid, {}).get(d) and schedules[rid][d].is_swappable
    ]
    graph = _build_graph(valid_requests, schedules, residents)
    raw = _find_raw_cycles(graph, max_cycle_length)
    valid = [c for c in raw if _validate_cycle(c, schedules)]
    valid.sort(key=lambda c: len(c))
    return [_build_cycle_model(c, schedules, residents) for c in valid]
