import sqlite3

from archive.history import get_report
from archive.publication import publish
from control.changeover import change
from control.closure import get_shift_closure
from control.shifts import get_shift
from intake.receipts import read_lane_receipts, read_lane_seals
from recorder.recording import read_source_cursor
from rules.catalog import get_rule
from storage import initialize
from tests.fixture import ColdChainCase, fail_publication_commit


class HandoverTests(ColdChainCase):
    def test_commit_failure_leaves_no_partial_issue_and_can_retry(self):
        self.open()
        reading, = self.sample(9700)
        self.deliver(*self.seal(), reading)
        denied = fail_publication_commit(self.db)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                publish(self.db, 'shift', 'handover')
        finally:
            self.db.set_authorizer(None)
        self.assertEqual(denied, ['COMMIT'])
        self.assertFalse(self.db.in_transaction)
        observer = self.connection()
        self.assertIsNone(get_report(observer, 'handover'))
        self.assertEqual(observer.execute('SELECT COUNT(*) FROM reports').fetchone()[0], 0)
        self.assertEqual(observer.execute('SELECT COUNT(*) FROM publication_receipts').fetchone()[0], 0)
        self.reopen()
        report = publish(self.db, 'shift', 'handover')
        self.assertEqual(report.alerts.readings, (reading,))
        self.assertEqual((report.alerts.count, report.alerts.peak_mc), (1, 9700))
        self.assertEqual(report.receipt.report_id, 'handover')
        self.assertEqual(get_report(observer, 'handover'), report)
        self.assertEqual(publish(self.db, 'shift', 'handover'), report)
        self.assertEqual(observer.execute('SELECT COUNT(*) FROM reports').fetchone()[0], 1)
        self.assertEqual(observer.execute('SELECT COUNT(*) FROM publication_receipts').fetchone()[0], 1)

    def test_history_reads_frozen_boundary_and_provenance_without_source(self):
        self.open(self.rule(threshold=8500))
        first, = self.sample(8700)
        new = self.rule('night', threshold=9000)
        op = change(self.db, 'rec', new, 'switch', self.device)
        second, = self.sample(9300)
        self.deliver(*self.seal(), second, first)
        report = publish(self.db, 'shift', 'original')
        self.assertEqual(report.changes[0].boundary, op.boundary)
        self.assertEqual(report.alerts.readings, (first, second))
        self.assertEqual((report.alerts.count, report.alerts.peak_mc), (2, 9300))
        self.rule('third', threshold=11000)
        self.source.close()
        self.reopen()

        def archive_only(action, table, field, database, origin):
            if action == sqlite3.SQLITE_READ and table not in {'reports', 'publication_receipts'}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.db.set_authorizer(archive_only)
        self.assertEqual(get_report(self.db, 'original'), report)
        self.assertEqual(publish(self.db, 'shift', 'original'), report)
        self.assertIsNone(get_report(self.db, 'unknown'))

    def test_initialize_twice_preserves_user_revisions_and_recording(self):
        original = self.rule('custom', threshold=8234)
        shift = self.open(original)
        first, = self.sample(8301)
        self.deliver(first)
        self.reopen()
        initialize(self.db)
        self.assertEqual(get_rule(self.db, original).threshold_mc, 8234)
        self.assertEqual(get_shift(self.db, 'shift'), shift)
        self.assertEqual(read_source_cursor(self.db, 'rec'), 1)
        self.assertEqual(read_lane_receipts(self.db, shift.initial_lane), (first,))
        second, = self.sample(8456)
        self.assertEqual((second.reading.source_seq, second.delivery_no), (2, 2))
        seals = self.seal()
        self.deliver(*seals, second)
        report = publish(self.db, 'shift', 'retained')
        self.reopen()
        initialize(self.db)
        self.assertEqual(get_report(self.db, 'retained'), report)
        self.assertEqual(read_lane_receipts(self.db, shift.initial_lane), (first, second))
        self.assertEqual(read_lane_seals(self.db, shift.initial_lane), seals)
        self.assertEqual(get_shift_closure(self.db, 'shift').end_seq, 2)
