import json

from archive.history import get_report
from archive.publication import publish
from control.closure import confirm_shift_end
from control.shifts import get_shift, open_shift
from intake.receipts import read_lane_receipts, read_lane_seals
from recorder.recording import read_source_cursor, record_reading
from rules.catalog import get_rule, register_rule
from rules.revisions import RuleRef
from tests.fixture import ColdChainCase


class RecordShiftTests(ColdChainCase):
    def test_regular_shift(self):
        shift = self.open()
        items = self.sample(7000, 9000, 9000)
        self.assertEqual([(item.delivery_no, item.reading.source_seq) for item in items], [(1, 2), (2, 3)])
        self.assertIsNone(confirm_shift_end(self.db, 'shift'))
        self.assertIsNone(publish(self.db, 'shift', 'ordinary'))
        self.deliver(*reversed(items), *self.seal())
        self.assertEqual(read_lane_receipts(self.db, shift.initial_lane), items)
        report = publish(self.db, 'shift', 'ordinary')
        self.assertEqual(report.alerts.readings, items)
        actual = {'source_sequences': [item.reading.source_seq for item in report.alerts.readings],
                  'count': report.alerts.count, 'peak_mc': report.alerts.peak_mc}
        expected = {'source_sequences': [2, 3], 'count': 2, 'peak_mc': 9000}
        self.assertEqual(actual, expected)
        historical = get_report(self.db, 'ordinary')
        self.assertEqual(historical, report)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v1', 'input': {'temperature_mc': [7000, 9000, 9000], 'threshold_mc': 9000},
            'expected': expected, 'actual': actual,
            'downstream_expected': {'same_report': True}, 'downstream_actual': {'same_report': historical == report},
            'code_paths': ['recorder/recording.py', 'rules/predicates.py', 'archive/aggregation.py', 'archive/history.py'],
            'oracle': '逐读比较闭阈值，只有第 2、3 读命中，最高值为 9000。',
        }, ensure_ascii=False))

    def test_empty_shift_has_zero_delivery_seal(self):
        shift = self.open()
        self.assertEqual(self.sample(1000, 2000), ())
        empty, = self.seal()
        self.assertEqual((empty.lane, empty.cutoff, empty.last_delivery), (shift.initial_lane, 2, 0))
        self.assertIsNone(publish(self.db, 'shift', 'empty'))
        self.deliver(empty)
        self.assertEqual(read_lane_seals(self.db, shift.initial_lane), (empty,))
        report = publish(self.db, 'shift', 'empty')
        self.assertEqual((report.alerts.readings, report.alerts.count, report.alerts.peak_mc), ((), 0, None))
        self.assertEqual(report.receipt.report_id, 'empty')

    def test_rule_registration_and_scope(self):
        first = self.rule()
        self.assertEqual(self.rule(), first)
        for bad, unit in ((True, 'mC'), (9000.0, 'mC'), (9, 'C'), ('9000', 'mC')):
            with self.subTest(bad=bad, unit=unit), self.assertRaises(ValueError):
                register_rule(self.db, 'bad', 'r1', 'west', bad, unit)
        with self.assertRaises(ValueError):
            self.rule(threshold=9100)
        self.assertEqual(get_rule(self.db, first).threshold_mc, 9000)
        newer = self.rule('night', threshold=12000)
        self.assertEqual(get_rule(self.db, newer).threshold_mc, 12000)
        self.open(first)
        self.assertEqual(self.sample(12000, point='east'), ())
        misses_and_hits = self.sample(8999, 9000, 9001)
        self.assertEqual([item.reading.source_seq for item in misses_and_hits], [3, 4])
        self.assertTrue(all(item.lane.rule == first for item in misses_and_hits))

    def test_shift_binding_and_open_rejection(self):
        with self.assertRaises(KeyError):
            open_shift(self.db, 'rec', 'shift', RuleRef('warm', 'unknown'))
        shift = self.open()
        for recording, name in (('other', 'shift'), ('rec', 'other')):
            with self.assertRaises(ValueError):
                open_shift(self.db, recording, name, shift.initial_lane.rule)
        self.reopen()
        self.assertEqual(get_shift(self.db, 'shift'), shift)
        self.assertEqual((shift.initial_lane.generation, shift.initial_lane.slot), (0, 0))
        self.assertEqual(read_source_cursor(self.db, 'rec'), 0)

    def test_bad_read_does_not_advance_cursor(self):
        self.open()
        for temp, seq, unit in ((9000, True, 'mC'), (9000, 2, 'mC'), (True, 1, 'mC'), (9, 1, 'C')):
            with self.assertRaises(ValueError):
                record_reading(self.source, 'rec', 'west', temp, seq, unit)
        self.assertEqual(read_source_cursor(self.db, 'rec'), 0)
        first, = record_reading(self.source, 'rec', 'west', 9000, 1)
        self.assertEqual((first.delivery_no, first.reading.source_seq), (1, 1))
        with self.assertRaises(ValueError):
            record_reading(self.source, 'rec', 'west', 9000, 1)
        seals = self.seal()
        self.assertEqual(self.seal(), seals)
        with self.assertRaises(ValueError):
            self.sample(9000)
        self.assertEqual(read_source_cursor(self.db, 'rec'), 1)
