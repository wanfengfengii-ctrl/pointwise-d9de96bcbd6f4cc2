from dataclasses import dataclass

from recorder.slots import LaneRef


@dataclass(frozen=True)
class SourceReading:
    recording: str
    source_seq: int
    point: str
    temperature_mc: int


@dataclass(frozen=True)
class ReadingDelivery:
    lane: LaneRef
    delivery_no: int
    reading: SourceReading


def emit_reading(db, lane, reading):
    delivery_no = last_delivery_through(db, lane, reading.source_seq) + 1
    db.execute(
        '''INSERT INTO emissions (recording, generation, delivery_no, source_seq, point, temperature_hex)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (lane.recording, lane.generation, delivery_no, reading.source_seq,
         reading.point, format(reading.temperature_mc, 'x')),
    )
    return ReadingDelivery(lane, delivery_no, reading)


def last_delivery_through(db, lane, cutoff):
    row = db.execute(
        '''SELECT MAX(delivery_no) AS delivery_no FROM (
               SELECT delivery_no
               FROM emissions
               WHERE recording = ? AND generation = ? AND source_seq <= ?
               UNION ALL
               SELECT delivery_no
               FROM custody_delivery_history
               WHERE recording = ? AND generation = ? AND source_seq <= ?
           )''',
        (lane.recording, lane.generation, cutoff,
         lane.recording, lane.generation, cutoff),
    ).fetchone()
    return 0 if row is None or row['delivery_no'] is None else row['delivery_no']


def read_delivery(db, lane, delivery_no):
    row = db.execute(
        '''SELECT source_seq, point, temperature_hex FROM emissions
           WHERE recording = ? AND generation = ? AND delivery_no = ?''',
        (lane.recording, lane.generation, delivery_no),
    ).fetchone()
    if row is None:
        return None
    reading = SourceReading(lane.recording, row['source_seq'], row['point'], int(row['temperature_hex'], 16))
    return ReadingDelivery(lane, delivery_no, reading)
