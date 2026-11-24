from recorder.emissions import SourceReading, emit_reading
from recorder.seals import seal_lane
from recorder.slots import LaneRef, active_slots, bind_slot, recording_lanes
from rules.catalog import get_rule
from rules.predicates import matches
from rules.units import parse_millicelsius
from storage import atomic, require_name


def _recording(db, recording):
    row = db.execute('SELECT cursor, end_seq FROM recordings WHERE recording = ?', (recording,)).fetchone()
    if row is None:
        raise KeyError(recording)
    return row


def start_recording(db, recording, rule):
    require_name(recording, 'recording')
    with atomic(db):
        get_rule(db, rule)
        cursor = db.execute(
            'INSERT INTO recordings (recording) VALUES (?) ON CONFLICT(recording) DO NOTHING', (recording,),
        )
        if cursor.rowcount == 0:
            raise ValueError('recording is already open or ended')
        return bind_slot(db, LaneRef(recording, 0, 0, rule), 0)


def record_reading(db, recording, point, temperature_mc, source_seq=None, unit='mC'):
    require_name(point, 'point')
    temperature = parse_millicelsius(temperature_mc, unit)
    with atomic(db):
        state = _recording(db, recording)
        if state['end_seq'] is not None:
            raise ValueError('recording has ended')
        next_seq = state['cursor'] + 1
        if source_seq is not None and (type(source_seq) is not int or source_seq != next_seq):
            raise ValueError('source sequence must advance by one')
        reading = SourceReading(recording, next_seq, point, temperature)
        deliveries = []
        for binding in active_slots(db, recording):
            rule = get_rule(db, binding.lane.rule)
            if matches(rule, point, temperature):
                deliveries.append(emit_reading(db, binding.lane, reading))
        db.execute('UPDATE recordings SET cursor = ? WHERE recording = ?', (next_seq, recording))
        return tuple(deliveries)


def stop_recording(db, recording):
    with atomic(db):
        state = _recording(db, recording)
        end = state['cursor']
        if state['end_seq'] is None:
            db.execute('UPDATE recordings SET end_seq = ? WHERE recording = ?', (end, recording))
        seals = []
        for binding in recording_lanes(db, recording):
            cutoff = end if binding.retired_through is None else binding.retired_through
            seals.append(seal_lane(db, binding.lane, cutoff))
        return tuple(seals)


def read_source_cursor(db, recording):
    return _recording(db, recording)['cursor']


def read_recording_end(db, recording):
    return _recording(db, recording)['end_seq']
