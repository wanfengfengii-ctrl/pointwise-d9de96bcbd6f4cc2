from dataclasses import dataclass

from recorder.emissions import last_delivery_through
from recorder.slots import LaneRef, load_binding
from storage import atomic


@dataclass(frozen=True)
class LaneSeal:
    lane: LaneRef
    cutoff: int
    last_delivery: int


def seal_lane(db, lane, cutoff):
    with atomic(db):
        binding = load_binding(db, lane.recording, lane.generation)
        cursor = db.execute('SELECT cursor FROM recordings WHERE recording = ?', (lane.recording,)).fetchone()[0]
        limit = cursor if binding.retired_through is None else binding.retired_through
        if binding.lane != lane or type(cutoff) is not int or not binding.active_after <= cutoff <= limit:
            raise ValueError('seal cutoff is outside the actual lane coverage')
        existing = read_seal(db, lane, cutoff)
        if existing is not None:
            return existing
        last = last_delivery_through(db, lane, cutoff)
        db.execute(
            'INSERT INTO device_seals (recording, generation, cutoff, last_delivery) VALUES (?, ?, ?, ?)',
            (lane.recording, lane.generation, cutoff, last),
        )
        return LaneSeal(lane, cutoff, last)


def read_seal(db, lane, cutoff):
    row = db.execute(
        'SELECT last_delivery FROM device_seals WHERE recording = ? AND generation = ? AND cutoff = ?',
        (lane.recording, lane.generation, cutoff),
    ).fetchone()
    if row is None:
        return None
    return LaneSeal(lane, cutoff, row['last_delivery'])
