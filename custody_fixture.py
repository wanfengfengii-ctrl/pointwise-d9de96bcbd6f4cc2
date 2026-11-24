from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from control.shifts import open_shift
from intake.receipts import read_lane_receipts, receive
from recorder.recording import record_reading
from rules.catalog import register_rule
from storage import connect, initialize


RECORDING = "north-bay-custody-1"
SHIFT = "night-1"
POINT = "west-bay"
DEVICE = "recorder-west"
STATION = "intake-north"
KEY = b"fixture-pair-key-for-north-bay-only"
TEMPERATURES = (8100, 9300, 8500)


def seed(db, recording=RECORDING, shift_id=SHIFT):
    initialize(db)
    rule = register_rule(db, "warm", "fixed", POINT, 8000)
    return open_shift(db, recording, shift_id, rule)


def sample(db, recording=RECORDING):
    return tuple(record_reading(db, recording, POINT, value)[0] for value in TEMPERATURES)


class ReplyLost(ConnectionError):
    pass


class Relay:
    def __init__(self):
        self.drop_next_reply = False

    def send(self, call, *args, **kwargs):
        drop = self.drop_next_reply
        self.drop_next_reply = False
        result = call(*args, **kwargs)
        if drop:
            raise ReplyLost("reply lost after the receiver returned")
        return result


@contextmanager
def reject_commit(db):
    def authorize(action, operation, arg2, database, trigger):
        if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorize)
    try:
        yield
    finally:
        db.set_authorizer(None)


def verify_fixture(source, station):
    # Exercise the transport and failure controls using only the original intake.
    binding = seed(source)
    assert seed(station) == binding
    first, middle, third = sample(source)
    assert [item.delivery_no for item in (first, middle, third)] == [1, 2, 3]
    relay = Relay()
    assert relay.send(receive, source, first).status == "accepted"
    relay.drop_next_reply = True
    try:
        relay.send(receive, source, third)
    except ReplyLost:
        pass
    else:
        raise AssertionError("configured reply loss did not occur")
    with reject_commit(source):
        try:
            relay.send(receive, source, middle)
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("configured commit denial did not occur")
    assert read_lane_receipts(source, binding.initial_lane) == (first, third)
    assert read_lane_receipts(station, binding.initial_lane) == ()
    assert relay.send(receive, source, third).status == "duplicate"
    assert relay.send(receive, source, middle).status == "accepted"
    assert read_lane_receipts(source, binding.initial_lane) == (first, middle, third)
    return {"replay_order": [1, 3, 2], "source_sequences": [1, 2, 3],
            "peak_mc": max(item.reading.temperature_mc for item in (first, middle, third))}


def main():
    with TemporaryDirectory(prefix="coldchain-custody-") as directory:
        root = Path(directory)
        with closing(connect(root / "device.sqlite3")) as source, closing(connect(root / "station.sqlite3")) as station:
            print(json.dumps(verify_fixture(source, station), sort_keys=True))


if __name__ == "__main__":
    main()
