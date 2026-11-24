from dataclasses import dataclass

from control.shifts import get_recording_shift
from recorder.actions import ActionResult
from recorder.recording import read_recording_end
from recorder.slots import LaneRef, load_binding
from rules.catalog import get_rule
from rules.revisions import RuleRef
from storage import atomic, require_name


@dataclass(frozen=True)
class ChangeOperation:
    recording: str
    operation_id: str
    position: int
    old_lane: LaneRef
    new_lane: LaneRef
    enabled_after: int | None
    retired_through: int | None
    boundary: int | None


def _as_operation(db, row):
    recording = row['recording']
    old = load_binding(db, recording, row['old_generation']).lane
    ref = RuleRef(row['new_rule_id'], row['new_revision'])
    new = LaneRef(recording, row['position'], row['new_slot'], ref)
    return ChangeOperation(recording, row['operation_id'], row['position'], old, new,
                           row['enabled_after'], row['retired_through'], row['boundary'])


def load_operation(db, recording, operation_id):
    row = db.execute(
        'SELECT * FROM change_operations WHERE recording = ? AND operation_id = ?',
        (recording, operation_id),
    ).fetchone()
    return None if row is None else _as_operation(db, row)


def list_shift_operations(db, recording):
    rows = db.execute('SELECT * FROM change_operations WHERE recording = ? ORDER BY position', (recording,))
    return tuple(_as_operation(db, row) for row in rows)


def accept_operation(db, recording, new_rule, operation_id):
    require_name(operation_id, 'operation_id')
    with atomic(db):
        existing = load_operation(db, recording, operation_id)
        if existing is not None:
            if existing.new_lane.rule != new_rule:
                raise ValueError('change operation already has different parameters')
            return existing
        shift = get_recording_shift(db, recording)
        get_rule(db, new_rule)
        if read_recording_end(db, recording) is not None:
            raise ValueError('recording has ended')
        row = db.execute(
            'SELECT * FROM change_operations WHERE recording = ? ORDER BY position DESC LIMIT 1',
            (recording,),
        ).fetchone()
        if row is None:
            position = 1
            old = shift.initial_lane
        else:
            previous = _as_operation(db, row)
            if previous.boundary is None:
                raise ValueError('recording already has a pending change')
            position = previous.position + 1
            old = previous.new_lane
        db.execute(
            '''INSERT INTO change_operations
               (recording, operation_id, position, old_generation, new_slot, new_rule_id, new_revision)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (recording, operation_id, position, old.generation, 1 - old.slot, new_rule.rule_id, new_rule.revision),
        )
        return load_operation(db, recording, operation_id)


def save_verified_progress(db, recording, operation_id, result: ActionResult):
    with atomic(db):
        op = load_operation(db, recording, operation_id)
        if op is None:
            raise KeyError((recording, operation_id))
        if op.boundary is not None:
            return op
        if (result.recording, result.operation_id) != (recording, operation_id):
            return op
        if type(result.boundary) is not int or result.boundary < 0:
            return op
        if result.action == 'enable' and result.lane == op.new_lane and op.enabled_after is None:
            db.execute(
                'UPDATE change_operations SET enabled_after = ? WHERE recording = ? AND operation_id = ?',
                (result.boundary, recording, operation_id),
            )
        elif (result.action == 'retire' and result.lane == op.old_lane and op.enabled_after is not None
              and op.retired_through is None):
            db.execute(
                'UPDATE change_operations SET retired_through = ? WHERE recording = ? AND operation_id = ?',
                (result.boundary, recording, operation_id),
            )
        return load_operation(db, recording, operation_id)


def fix_change_boundary(db, recording, operation_id, boundary):
    with atomic(db):
        op = load_operation(db, recording, operation_id)
        if op is None:
            raise KeyError((recording, operation_id))
        if op.boundary is not None:
            return op
        if op.enabled_after is None or op.retired_through is None or op.enabled_after > op.retired_through:
            return op
        if type(boundary) is not int or not op.enabled_after <= boundary <= op.retired_through:
            raise ValueError('change boundary must be within the verified overlap')
        db.execute(
            'UPDATE change_operations SET boundary = ? WHERE recording = ? AND operation_id = ?',
            (boundary, recording, operation_id),
        )
        return load_operation(db, recording, operation_id)
