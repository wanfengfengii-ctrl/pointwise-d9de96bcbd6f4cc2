from dataclasses import dataclass

from intake.receipts import read_lane_receipts, read_lane_seals
from recorder.seals import LaneSeal


@dataclass(frozen=True)
class LaneEvidence:
    complete: bool
    covering_seal: LaneSeal | None
    first_missing_delivery: int | None


def check_lane_interval(db, lane, start, end, *, active_after):
    if start > end:
        return LaneEvidence(True, None, None)
    seals = read_lane_seals(db, lane)
    cover = next((seal for seal in seals if seal.cutoff >= end), None)
    if cover is None:
        return LaneEvidence(False, None, None)
    deliveries = read_lane_receipts(db, lane)
    # The lane has rank zero at activation. Strictly increasing integer source
    # positions also bound every missing rank from each observed read or seal.
    first = 1
    last = min(cover.last_delivery, end - active_after)
    for seal in seals:
        rank = seal.last_delivery
        first = max(first, min(rank + 1, rank + start - seal.cutoff))
        last = min(last, max(rank, rank + end - seal.cutoff))
    for item in deliveries:
        rank = item.delivery_no
        source = item.reading.source_seq
        first = max(first, min(rank + 1, rank + start - source))
        last = min(last, max(rank - 1, rank + end - source))
    # Ranks outside these bounds are forced outside the responsible interval.
    candidate = first
    for item in deliveries:
        if candidate > last or item.delivery_no > candidate:
            break
        if item.delivery_no == candidate:
            candidate += 1
    if candidate <= last:
        return LaneEvidence(False, cover, candidate)
    return LaneEvidence(True, cover, None)
