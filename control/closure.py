from dataclasses import dataclass

from control.shifts import get_shift
from recorder.recording import read_recording_end
from storage import atomic


@dataclass(frozen=True)
class ShiftClosure:
    shift_id: str
    end_seq: int


def get_shift_closure(db, shift_id):
    row = db.execute('SELECT end_seq FROM shift_closures WHERE shift_id = ?', (shift_id,)).fetchone()
    return None if row is None else ShiftClosure(shift_id, row['end_seq'])


def confirm_shift_end(db, shift_id):
    with atomic(db):
        shift = get_shift(db, shift_id)
        closure = get_shift_closure(db, shift_id)
        if closure is not None:
            return closure
        end = read_recording_end(db, shift.recording)
        if end is None:
            return None
        db.execute('INSERT INTO shift_closures (shift_id, end_seq) VALUES (?, ?)', (shift_id, end))
        return ShiftClosure(shift_id, end)
