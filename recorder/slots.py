from dataclasses import dataclass

from rules.revisions import RuleRef


@dataclass(frozen=True)
class LaneRef:
    recording: str
    generation: int
    slot: int
    rule: RuleRef


@dataclass(frozen=True)
class SlotBinding:
    lane: LaneRef
    active_after: int
    retired_through: int | None


def _binding(row):
    ref = RuleRef(row['rule_id'], row['revision'])
    lane = LaneRef(row['recording'], row['generation'], row['slot'], ref)
    return SlotBinding(lane, row['active_after'], row['retired_through'])


def bind_slot(db, lane, boundary):
    occupied = db.execute(
        '''SELECT 1 FROM lanes WHERE recording = ? AND slot = ?
           AND retired_through IS NULL''',
        (lane.recording, lane.slot),
    ).fetchone()
    if occupied is not None:
        raise ValueError('device slot is occupied')
    db.execute(
        '''INSERT INTO lanes (recording, generation, slot, rule_id, revision, active_after)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (lane.recording, lane.generation, lane.slot, lane.rule.rule_id, lane.rule.revision, boundary),
    )
    return SlotBinding(lane, boundary, None)


def retire_slot(db, lane, boundary):
    current = load_binding(db, lane.recording, lane.generation)
    if current.lane != lane or current.retired_through is not None or boundary < current.active_after:
        raise ValueError('device retirement requires the matching active lane')
    db.execute(
        'UPDATE lanes SET retired_through = ? WHERE recording = ? AND generation = ?',
        (boundary, lane.recording, lane.generation),
    )
    return SlotBinding(lane, current.active_after, boundary)


def active_slots(db, recording):
    rows = db.execute(
        'SELECT * FROM lanes WHERE recording = ? AND retired_through IS NULL ORDER BY slot',
        (recording,),
    )
    return tuple(_binding(row) for row in rows)


def load_binding(db, recording, generation):
    row = db.execute(
        'SELECT * FROM lanes WHERE recording = ? AND generation = ?', (recording, generation),
    ).fetchone()
    if row is None:
        raise KeyError((recording, generation))
    return _binding(row)


def recording_lanes(db, recording):
    rows = db.execute('SELECT * FROM lanes WHERE recording = ? ORDER BY generation', (recording,))
    return tuple(_binding(row) for row in rows)
