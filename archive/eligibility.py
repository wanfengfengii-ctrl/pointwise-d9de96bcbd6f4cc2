from dataclasses import dataclass

from control.closure import get_shift_closure
from control.operations import list_shift_operations
from control.shifts import get_shift
from intake.completeness import check_lane_interval
from recorder.slots import LaneRef


@dataclass(frozen=True)
class Boundary:
    operation_id: str
    old_lane: LaneRef
    new_lane: LaneRef
    enabled_after: int
    retired_through: int
    boundary: int


@dataclass(frozen=True)
class LaneInterval:
    lane: LaneRef
    active_after: int
    start: int
    end: int


@dataclass(frozen=True)
class PublicationBasis:
    shift_id: str
    recording: str
    end_seq: int
    changes: tuple[Boundary, ...]
    intervals: tuple[LaneInterval, ...]


def assess_eligibility(db, shift_id):
    shift = get_shift(db, shift_id)
    closure = get_shift_closure(db, shift_id)
    if closure is None:
        return None
    intervals = []
    changes = []
    lane = shift.initial_lane
    active_after = 0
    start = 1
    for op in list_shift_operations(db, shift.recording):
        boundary = op.boundary
        if boundary is None or op.enabled_after is None or op.retired_through is None:
            return None
        if (op.old_lane != lane or not op.enabled_after <= boundary <= op.retired_through
            or not start - 1 <= boundary <= closure.end_seq):
            return None
        if start <= boundary:
            intervals.append(LaneInterval(lane, active_after, start, boundary))
        changes.append(Boundary(op.operation_id, lane, op.new_lane, op.enabled_after, op.retired_through, boundary))
        lane = op.new_lane
        active_after = op.enabled_after
        start = boundary + 1
    if start <= closure.end_seq:
        intervals.append(LaneInterval(lane, active_after, start, closure.end_seq))
    for interval in intervals:
        evidence = check_lane_interval(db, interval.lane, interval.start, interval.end,
                                       active_after=interval.active_after)
        if not evidence.complete:
            return None
    return PublicationBasis(shift_id, shift.recording, closure.end_seq, tuple(changes), tuple(intervals))
