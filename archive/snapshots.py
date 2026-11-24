import json
from dataclasses import asdict, dataclass

from archive.aggregation import AlertSummary
from archive.eligibility import Boundary, PublicationBasis
from recorder.emissions import ReadingDelivery, SourceReading
from recorder.slots import LaneRef
from rules.revisions import RuleRef


@dataclass(frozen=True)
class PublicationReceipt:
    report_id: str
    serial: int


@dataclass(frozen=True)
class ReportSnapshot:
    report_id: str
    shift_id: str
    recording: str
    end_seq: int
    changes: tuple[Boundary, ...]
    alerts: AlertSummary
    receipt: PublicationReceipt


def _encode(basis, alerts):
    readings = []
    for reading in alerts.readings:
        item = asdict(reading)
        item['reading']['temperature_mc'] = format(reading.reading.temperature_mc, 'x')
        readings.append(item)
    return json.dumps({
        'changes': [asdict(change) for change in basis.changes],
        'readings': readings,
        'count': alerts.count,
        'peak_mc': None if alerts.peak_mc is None else format(alerts.peak_mc, 'x'),
    }, ensure_ascii=False, sort_keys=True)


def _decode_lane(data):
    return LaneRef(data['recording'], data['generation'], data['slot'], RuleRef(**data['rule']))


def _decode_reading(data):
    fields = data['reading'].copy()
    fields['temperature_mc'] = int(fields['temperature_mc'], 16)
    return ReadingDelivery(_decode_lane(data['lane']), data['delivery_no'], SourceReading(**fields))


def _decode_boundary(data):
    return Boundary(data['operation_id'], _decode_lane(data['old_lane']), _decode_lane(data['new_lane']),
                    data['enabled_after'], data['retired_through'], data['boundary'])


def store_snapshot(db, report_id, basis: PublicationBasis, alerts: AlertSummary):
    if not db.in_transaction:
        raise ValueError('snapshot storage requires a publication transaction')
    db.execute(
        'INSERT INTO reports (report_id, shift_id, recording, end_seq, payload) VALUES (?, ?, ?, ?, ?)',
        (report_id, basis.shift_id, basis.recording, basis.end_seq, _encode(basis, alerts)),
    )
    cursor = db.execute('INSERT INTO publication_receipts (report_id) VALUES (?)', (report_id,))
    receipt = PublicationReceipt(report_id, cursor.lastrowid)
    return ReportSnapshot(report_id, basis.shift_id, basis.recording, basis.end_seq, basis.changes, alerts, receipt)


def load_snapshot(db, report_id):
    row = db.execute(
        '''SELECT reports.shift_id, reports.recording, reports.end_seq, reports.payload, publication_receipts.serial
           FROM reports JOIN publication_receipts USING (report_id) WHERE reports.report_id = ?''',
        (report_id,),
    ).fetchone()
    if row is None:
        return None
    data = json.loads(row['payload'])
    changes = tuple(_decode_boundary(change) for change in data['changes'])
    readings = tuple(_decode_reading(item) for item in data['readings'])
    peak = None if data['peak_mc'] is None else int(data['peak_mc'], 16)
    alerts = AlertSummary(readings, data['count'], peak)
    return ReportSnapshot(report_id, row['shift_id'], row['recording'], row['end_seq'], changes, alerts,
                          PublicationReceipt(report_id, row['serial']))
