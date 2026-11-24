from archive.snapshots import load_snapshot


def get_report(db, report_id):
    return load_snapshot(db, report_id)
