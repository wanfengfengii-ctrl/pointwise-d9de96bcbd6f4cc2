import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from control.closure import confirm_shift_end
from control.shifts import open_shift
from intake.receipts import receive
from recorder.actions import LocalDevice
from recorder.recording import record_reading, stop_recording
from rules.catalog import register_rule
from storage import connect, initialize


class ScheduledDevice:
    def __init__(self, db):
        self.device = LocalDevice(db)
        self.hold_requests = set()
        self.hide_results = set()
        self.queued = {}

    def lookup(self, recording, operation_id, action):
        if action in self.hide_results:
            return None
        return self.device.lookup(recording, operation_id, action)

    def request(self, recording, operation_id, action, lane):
        key = recording, operation_id, action
        if action in self.hold_requests:
            previous = self.queued.setdefault(key, lane)
            if previous != lane:
                raise ValueError('queued device request changed lane')
            return None
        self.device.request(*key, lane)
        return self.lookup(*key)

    def perform(self, recording, operation_id, action):
        key = recording, operation_id, action
        lane = self.queued.pop(key)
        self.hold_requests.discard(action)
        return self.device.request(*key, lane)


class ColdChainCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='coldchain-test-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'station.sqlite3'
        self.db = self.connection()
        initialize(self.db)
        self.source = self.connection()
        self.device = ScheduledDevice(self.source)

    def connection(self):
        db = connect(self.path)
        self.addCleanup(db.close)
        return db

    def reopen(self):
        self.db.close()
        self.db = self.connection()

    def rule(self, revision='day', point='west', threshold=9000):
        return register_rule(self.db, 'warm', revision, point, threshold)

    def open(self, rule=None):
        return open_shift(self.db, 'rec', 'shift', self.rule() if rule is None else rule)

    def sample(self, *temperatures, point='west'):
        deliveries = []
        for temp in temperatures:
            deliveries.extend(record_reading(self.source, 'rec', point, temp))
        return tuple(deliveries)

    def seal(self):
        seals = stop_recording(self.source, 'rec')
        confirm_shift_end(self.db, 'shift')
        return seals

    def deliver(self, *items):
        return tuple(receive(self.db, item) for item in items)


def fail_publication_commit(db):
    denied = []

    def authorize(action, operation, detail, database, trigger):
        if action == sqlite3.SQLITE_TRANSACTION and operation == 'COMMIT':
            denied.append(operation)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorize)
    return denied
