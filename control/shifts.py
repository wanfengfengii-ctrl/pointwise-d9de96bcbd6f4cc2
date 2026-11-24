from dataclasses import dataclass

from recorder.recording import start_recording
from recorder.slots import LaneRef, load_binding
from rules.catalog import get_rule
from storage import atomic, require_name


@dataclass(frozen=True)
class ShiftBinding:
    shift_id: str
    recording: str
    initial_lane: LaneRef


def open_shift(db, recording, shift_id, rule):
    require_name(shift_id, 'shift_id')
    with atomic(db):
        get_rule(db, rule)
        existing = db.execute('SELECT 1 FROM shifts WHERE shift_id = ?', (shift_id,)).fetchone()
        if existing is not None:
            raise ValueError('shift is already occupied')
        initial = start_recording(db, recording, rule).lane
        db.execute(
            'INSERT INTO shifts (shift_id, recording, initial_generation) VALUES (?, ?, ?)',
            (shift_id, recording, initial.generation),
        )
        return ShiftBinding(shift_id, recording, initial)


def _as_shift(db, row, key):
    if row is None:
        raise KeyError(key)
    recording = row['recording']
    initial = load_binding(db, recording, row['initial_generation']).lane
    return ShiftBinding(row['shift_id'], recording, initial)


def get_shift(db, shift_id):
    row = db.execute('SELECT * FROM shifts WHERE shift_id = ?', (shift_id,)).fetchone()
    return _as_shift(db, row, shift_id)


def get_recording_shift(db, recording):
    row = db.execute('SELECT * FROM shifts WHERE recording = ?', (recording,)).fetchone()
    return _as_shift(db, row, recording)
