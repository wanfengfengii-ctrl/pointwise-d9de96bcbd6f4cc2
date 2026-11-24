from dataclasses import dataclass

from recorder.recording import read_source_cursor
from recorder.seals import seal_lane
from recorder.slots import LaneRef, bind_slot, load_binding, retire_slot
from rules.catalog import get_rule
from storage import atomic, require_name


@dataclass(frozen=True)
class ActionResult:
    recording: str
    operation_id: str
    action: str
    lane: LaneRef
    boundary: int


def read_action_result(db, recording, operation_id, action):
    row = db.execute(
        '''SELECT generation, boundary FROM slot_actions
           WHERE recording = ? AND operation_id = ? AND action = ?''',
        (recording, operation_id, action),
    ).fetchone()
    if row is None:
        return None
    lane = load_binding(db, recording, row['generation']).lane
    return ActionResult(recording, operation_id, action, lane, row['boundary'])


def apply_slot_action(db, recording, operation_id, action, lane):
    require_name(operation_id, 'operation_id')
    if action not in ('enable', 'retire') or lane.recording != recording:
        raise ValueError('invalid slot action')
    with atomic(db):
        existing = read_action_result(db, recording, operation_id, action)
        if existing is not None:
            if existing.lane != lane:
                raise ValueError('device action key already belongs to a different lane')
            return existing
        boundary = read_source_cursor(db, recording)
        if action == 'enable':
            get_rule(db, lane.rule)
            bind_slot(db, lane, boundary)
        else:
            retire_slot(db, lane, boundary)
            seal_lane(db, lane, boundary)
        db.execute(
            '''INSERT INTO slot_actions (recording, operation_id, action, generation, boundary)
               VALUES (?, ?, ?, ?, ?)''',
            (recording, operation_id, action, lane.generation, boundary),
        )
        return ActionResult(recording, operation_id, action, lane, boundary)


class LocalDevice:
    def __init__(self, db):
        self.db = db

    def lookup(self, recording, operation_id, action):
        return read_action_result(self.db, recording, operation_id, action)

    def request(self, recording, operation_id, action, lane):
        return apply_slot_action(self.db, recording, operation_id, action, lane)
