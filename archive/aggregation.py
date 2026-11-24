from dataclasses import dataclass

from archive.eligibility import PublicationBasis
from intake.identity import source_key
from intake.receipts import read_lane_receipts
from recorder.emissions import ReadingDelivery


@dataclass(frozen=True)
class AlertSummary:
    readings: tuple[ReadingDelivery, ...]
    count: int
    peak_mc: int | None


def aggregate_alerts(db, basis: PublicationBasis):
    chosen = {}
    for interval in basis.intervals:
        for item in read_lane_receipts(db, interval.lane):
            reading = item.reading
            if interval.start <= reading.source_seq <= interval.end:
                chosen.setdefault(source_key(reading), item)
    readings = tuple(chosen[key] for key in sorted(chosen))
    peak = max((item.reading.temperature_mc for item in readings), default=None)
    return AlertSummary(readings, len(readings), peak)
