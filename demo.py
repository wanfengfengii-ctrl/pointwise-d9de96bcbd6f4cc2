import argparse
import json
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.history import get_report
from archive.publication import publish
from control.changeover import change
from control.closure import confirm_shift_end
from control.operations import accept_operation, save_verified_progress
from control.shifts import open_shift
from intake.receipts import receive
from recorder.actions import LocalDevice
from recorder.recording import record_reading, stop_recording
from rules.catalog import register_rule
from storage import connect, initialize


def run(path):
    data = json.loads(Path(__file__).with_name('sample.json').read_text(encoding='utf-8'))
    recording, shift, point = data['recording'], data['shift_id'], data['point']
    operation_id, report_id = 'evening-rule-change', 'evening-handover'
    with closing(connect(path)) as db, closing(connect(path)) as source:
        initialize(db)
        old = register_rule(db, 'warm', 'day', point, data['old_threshold_mc'])
        new = register_rule(db, 'warm', 'evening', point, data['new_threshold_mc'])
        open_shift(db, recording, shift, old)
        for _ in range(data['prefix_count']):
            record_reading(source, recording, point, data['prefix_temperature_mc'])
        op = accept_operation(db, recording, new, operation_id)
        device = LocalDevice(source)
        device.request(recording, operation_id, 'enable', op.new_lane)
        enabled = device.lookup(recording, operation_id, 'enable')
        save_verified_progress(db, recording, operation_id, enabled)
        overlap = []
        for temperature in data['overlap_mc']:
            overlap.extend(record_reading(source, recording, point, temperature))
        op = change(db, recording, new, operation_id, device=device)
        final, = record_reading(source, recording, point, data['after_retirement_mc'])
        seals = stop_recording(source, recording)
        confirm_shift_end(db, shift)
        delayed, = (item for item in overlap if item.lane == op.new_lane)
        for item in (*seals, final, *overlap):
            if item != delayed:
                receive(db, item)
        pending = publish(db, shift, report_id)
        before = get_report(db, report_id)
        assert pending is None and before is None
        receive(db, delayed)
        report = publish(db, shift, report_id)
        assert report is not None
        actual = {
            'before_missing_delivery': 'pending' if pending is None else 'issued',
            'boundary': report.changes[0].boundary,
            'source_sequences': [item.reading.source_seq for item in report.alerts.readings],
            'delivery_numbers': [item.delivery_no for item in report.alerts.readings],
            'lane_generations': [item.lane.generation for item in report.alerts.readings],
            'count': report.alerts.count,
            'peak_mc': report.alerts.peak_mc,
        }
        expected = {'before_missing_delivery': 'pending', 'boundary': 41, 'source_sequences': [43, 45],
                    'delivery_numbers': [1, 2], 'lane_generations': [1, 1], 'count': 2, 'peak_mc': 9300}
        assert actual == expected
    with closing(connect(path)) as reopened:
        historical = get_report(reopened, report_id)
        assert historical == report
    event = {
        'case_id': 'v5',
        'input': {**data, 'held': {'source_seq': delayed.reading.source_seq, 'delivery_no': delayed.delivery_no}},
        'expected': expected,
        'actual': actual,
        'downstream_expected': {'available_before': False, 'same_snapshot_on_reopen': True},
        'downstream_actual': {'available_before': before is not None, 'same_snapshot_on_reopen': historical == report},
        'code_paths': ['control/changeover.py', 'intake/completeness.py', 'archive/publication.py', 'archive/history.py'],
        'oracle': '前 41 读均不命中。第 42 读仅旧路，已越过旧路负责段。43 与 45 是新路 1 和 2，先缺 1 时不能领取。',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(event, ensure_ascii=False))
    print(json.dumps(asdict(historical), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description='北湾试验库告警交班演示')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--temporary', action='store_true')
    group.add_argument('--db', type=Path)
    args = parser.parse_args()
    if args.temporary:
        with TemporaryDirectory(prefix='coldchain-demo-') as directory:
            run(Path(directory) / 'demo.sqlite3')
    else:
        try:
            with args.db.open('xb'):
                pass
        except FileExistsError:
            parser.error('demo requires a new database file')
        run(args.db)


if __name__ == '__main__':
    main()
