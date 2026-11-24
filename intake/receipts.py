from dataclasses import dataclass

from intake.conflicts import classify_delivery, classify_seal
from intake.identity import delivery_key, seal_key
from intake.provenance import resolve_lane, validate_envelope, verify_origin
from recorder.emissions import ReadingDelivery, SourceReading
from recorder.seals import LaneSeal
from storage import atomic


@dataclass(frozen=True)
class ReceiptResult:
    status: str
    item: ReadingDelivery | LaneSeal


def read_lane_receipts(db, lane):
    rows = db.execute(
        '''SELECT delivery_no, source_seq, point, temperature_hex FROM received_deliveries
           WHERE recording = ? AND generation = ? ORDER BY delivery_no''',
        (lane.recording, lane.generation),
    )
    deliveries = []
    for row in rows:
        reading = SourceReading(lane.recording, row['source_seq'], row['point'], int(row['temperature_hex'], 16))
        deliveries.append(ReadingDelivery(lane, row['delivery_no'], reading))
    return tuple(deliveries)


def read_lane_seals(db, lane):
    rows = db.execute(
        '''SELECT cutoff, last_delivery FROM received_seals
           WHERE recording = ? AND generation = ? ORDER BY cutoff''',
        (lane.recording, lane.generation),
    )
    return tuple(LaneSeal(lane, row['cutoff'], row['last_delivery']) for row in rows)


def receive(db, item):
    if not isinstance(item, (ReadingDelivery, LaneSeal)):
        raise ValueError('unsupported receipt type')
    with atomic(db):
        binding = resolve_lane(db, item.lane)
        validate_envelope(item, binding)
        deliveries = read_lane_receipts(db, binding.lane)
        seals = read_lane_seals(db, binding.lane)
        if isinstance(item, ReadingDelivery):
            status = classify_delivery(item, deliveries, seals)
            if status == 'accepted':
                verify_origin(db, item)
                reading = item.reading
                db.execute(
                    '''INSERT INTO received_deliveries
                       (recording, generation, delivery_no, source_seq, point, temperature_hex)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (*delivery_key(item), reading.source_seq, reading.point, format(reading.temperature_mc, 'x')),
                )
        else:
            status = classify_seal(item, deliveries, seals)
            if status == 'accepted':
                verify_origin(db, item)
                db.execute(
                    'INSERT INTO received_seals (recording, generation, cutoff, last_delivery) VALUES (?, ?, ?, ?)',
                    (*seal_key(item), item.last_delivery),
                )
        return ReceiptResult(status, item)
