from archive.aggregation import aggregate_alerts
from archive.eligibility import assess_eligibility
from archive.snapshots import load_snapshot, store_snapshot
from storage import atomic, require_name


def publish(db, shift_id, report_id):
    require_name(report_id, 'report_id')
    if db.in_transaction:
        raise ValueError('publication requires a connection outside a transaction')
    with atomic(db):
        snapshot = load_snapshot(db, report_id)
        if snapshot is not None:
            if snapshot.shift_id != shift_id:
                raise ValueError('report id belongs to another shift')
            return snapshot
        basis = assess_eligibility(db, shift_id)
        if basis is None:
            return None
        return store_snapshot(db, report_id, basis, aggregate_alerts(db, basis))
