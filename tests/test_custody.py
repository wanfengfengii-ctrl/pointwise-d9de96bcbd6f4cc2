import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import custody
import custody_demo
from archive.history import get_report
from archive.publication import publish
from custody_fixture import (
    DEVICE,
    KEY,
    POINT,
    SHIFT,
    STATION,
    Relay,
    ReplyLost,
    reject_commit,
    sample as custody_fixture_sample,
    seed,
    verify_fixture,
)
from control.shifts import get_shift
from intake.receipts import read_lane_receipts
from recorder.recording import record_reading
from storage import connect


class CustodyCase(unittest.TestCase):
    def setUp(self):
        self._directory = TemporaryDirectory(prefix="custody-tests-")
        self.addCleanup(self._directory.cleanup)
        root = Path(self._directory.name)
        self.device_path = root / "device.sqlite3"
        self.station_path = root / "station.sqlite3"
        self.device = connect(self.device_path)
        self.station = connect(self.station_path)
        self.addCleanup(self.device.close)
        self.addCleanup(self.station.close)
        seed(self.device)
        seed(self.station)
        self.pair_both()

    def pair_both(self, key=KEY, device_id=DEVICE, station_id=STATION):
        custody.pair(
            self.device, SHIFT, role="device", key=key,
            device_id=device_id, station_id=station_id,
        )
        custody.pair(
            self.station, SHIFT, role="station", key=key,
            device_id=device_id, station_id=station_id,
        )

    def envelopes(self):
        return {
            item.delivery_no: (item, proof)
            for item, proof in custody.pending(self.device, SHIFT)
        }

    def reopen_both(self):
        self.device.close()
        self.station.close()
        self.device = connect(self.device_path)
        self.station = connect(self.station_path)
        self.addCleanup(self.device.close)
        self.addCleanup(self.station.close)

    def reopen_station(self):
        self.station.close()
        self.station = connect(self.station_path)
        self.addCleanup(self.station.close)


class CustodyFlowTests(CustodyCase):
    def test_lost_confirmation_restart_release_gap_seal_and_report(self):
        first, middle, third = custody_fixture_sample(self.device)
        envelopes = self.envelopes()
        self.assertEqual(list(envelopes), [1, 2, 3])
        self.assertEqual(
            [item.reading.temperature_mc for item, _ in envelopes.values()],
            [8100, 9300, 8500],
        )

        first_confirmation = custody.receive(
            self.station, SHIFT, *envelopes[1]
        )
        self.assertEqual(first_confirmation[0], 1)

        relay = Relay()
        relay.drop_next_reply = True
        with self.assertRaises(ReplyLost):
            relay.send(custody.receive, self.station, SHIFT, *envelopes[3])

        self.reopen_both()
        custody.pair(
            self.device, SHIFT, role="device",
            device_id=DEVICE, station_id=STATION, key=KEY,
        )
        custody.pair(
            self.station, SHIFT, role="station",
            device_id=DEVICE, station_id=STATION, key=KEY,
        )

        replayed_third = custody.receive(
            self.station, SHIFT, *envelopes[3]
        )
        self.assertEqual(replayed_third, first_confirmation)
        self.assertEqual(
            custody.release(self.device, SHIFT, replayed_third), (1,)
        )
        self.assertEqual(
            tuple(item.delivery_no for item, _ in custody.pending(self.device, SHIFT)),
            (2, 3),
        )

        seal_envelope = custody.finish(self.device, SHIFT)
        seal, _proof = seal_envelope
        self.assertEqual((seal.cutoff, seal.last_delivery), (3, 3))
        self.assertIsNone(custody.receive(self.station, SHIFT, *seal_envelope))
        self.assertIsNone(publish(self.station, SHIFT, "night-report"))
        self.assertIsNone(get_report(self.station, "night-report"))

        middle_confirmation = custody.receive(
            self.station, SHIFT, *envelopes[2]
        )
        self.assertEqual(middle_confirmation[0], 3)
        report = publish(self.station, SHIFT, "night-report")
        self.assertIsNotNone(report)
        self.assertEqual(
            [item.delivery_no for item in report.alerts.readings], [1, 2, 3]
        )
        self.assertEqual(
            [item.reading.source_seq for item in report.alerts.readings], [1, 2, 3]
        )
        self.assertEqual(report.alerts.count, 3)
        self.assertEqual(report.alerts.peak_mc, 9300)

        self.assertEqual(
            custody.release(self.device, SHIFT, middle_confirmation), (2, 3)
        )
        self.assertEqual(custody.pending(self.device, SHIFT), ())
        self.assertEqual(custody.finish(self.device, SHIFT)[0], seal)

        self.reopen_station()
        self.assertEqual(get_report(self.station, "night-report"), report)

    def test_released_numbers_are_not_reused_and_old_prefix_is_limited(self):
        custody_fixture_sample(self.device)
        envelopes = self.envelopes()
        complete_confirmation = None
        for number in (1, 2, 3):
            complete_confirmation = custody.receive(
                self.station, SHIFT, *envelopes[number]
            )
        self.assertEqual(complete_confirmation[0], 3)
        self.assertEqual(
            custody.release(self.device, SHIFT, complete_confirmation), (1, 2, 3)
        )
        self.assertEqual(custody.pending(self.device, SHIFT), ())
        self.reopen_both()

        record_reading(self.device, "north-bay-custody-1", POINT, 7999)
        fourth, = record_reading(
            self.device, "north-bay-custody-1", POINT, 8400
        )
        self.assertEqual(
            (fourth.reading.source_seq, fourth.delivery_no), (5, 4)
        )
        fourth_envelope = next(iter(custody.pending(self.device, SHIFT)))

        seal, _proof = custody.finish(self.device, SHIFT)
        self.assertEqual((seal.cutoff, seal.last_delivery), (5, 4))

        # The old n=3 confirmation cannot cover delivery 4.
        self.assertEqual(
            custody.release(self.device, SHIFT, complete_confirmation), ()
        )
        self.assertEqual(
            [item.delivery_no for item, _ in custody.pending(self.device, SHIFT)],
            [4],
        )

        with self.assertRaises(sqlite3.DatabaseError):
            with reject_commit(self.station):
                custody.receive(self.station, SHIFT, *fourth_envelope)

        shift = get_shift(self.station, SHIFT)
        self.assertEqual(
            [item.delivery_no for item in read_lane_receipts(
                self.station, shift.initial_lane
            )],
            [1, 2, 3],
        )
        self.assertEqual(
            [item.delivery_no for item, _ in custody.pending(self.device, SHIFT)],
            [4],
        )

        fourth_confirmation = custody.receive(
            self.station, SHIFT, *fourth_envelope
        )
        self.assertEqual(fourth_confirmation[0], 4)
        self.assertEqual(
            custody.release(self.device, SHIFT, complete_confirmation), ()
        )
        self.assertEqual(
            [item.delivery_no for item, _ in custody.pending(self.device, SHIFT)],
            [4],
        )
        self.assertEqual(
            custody.release(self.device, SHIFT, fourth_confirmation), (4,)
        )

        custody.receive(self.station, SHIFT, seal, _proof)
        report = publish(self.station, SHIFT, "second-round")
        self.assertIsNotNone(report)
        self.assertEqual(report.end_seq, 5)
        self.assertEqual(report.alerts.count, 4)
        self.assertEqual(report.alerts.peak_mc, 9300)

    def test_prefix_zero_is_valid_before_first_delivery(self):
        custody_fixture_sample(self.device)
        envelopes = self.envelopes()
        coverage_zero = custody.receive(self.station, SHIFT, *envelopes[3])
        self.assertEqual(coverage_zero[0], 0)
        coverage_one = custody.receive(self.station, SHIFT, *envelopes[1])
        self.assertEqual(coverage_one[0], 1)
        replayed_third = custody.receive(self.station, SHIFT, *envelopes[3])
        self.assertEqual(replayed_third[0], 1)

    def test_empty_shift_can_publish_after_zero_seal(self):
        seal_envelope = custody.finish(self.device, SHIFT)
        self.assertEqual(
            (seal_envelope[0].cutoff, seal_envelope[0].last_delivery), (0, 0)
        )
        self.assertIsNone(custody.receive(self.station, SHIFT, *seal_envelope))
        report = publish(self.station, SHIFT, "empty")
        self.assertIsNotNone(report)
        self.assertEqual(report.alerts.count, 0)
        self.assertIsNone(report.alerts.peak_mc)
        self.reopen_station()
        self.assertEqual(get_report(self.station, "empty"), report)


class CustodySecurityTests(CustodyCase):
    def test_pair_parameters_are_idempotent_but_not_rebound(self):
        custody.pair(
            self.device, SHIFT, role="device",
            device_id=DEVICE, station_id=STATION, key=KEY,
        )
        with self.assertRaises(ValueError):
            custody.pair(
                self.device, SHIFT, role="device",
                device_id=DEVICE, station_id="other-station", key=KEY,
            )
        with self.assertRaises(ValueError):
            custody.pending(self.station, SHIFT)

    def test_tampered_delivery_and_proof_do_not_update_station(self):
        custody_fixture_sample(self.device)
        item, proof = self.envelopes()[1]
        tampered = replace(
            item,
            reading=replace(item.reading, temperature_mc=9999),
        )
        with self.assertRaises(ValueError):
            custody.receive(self.station, SHIFT, tampered, proof)
        with self.assertRaises(ValueError):
            custody.receive(self.station, SHIFT, item, b"not-the-proof")

        shift = get_shift(self.station, SHIFT)
        self.assertEqual(
            read_lane_receipts(self.station, shift.initial_lane), ()
        )

    def test_wrong_pair_key_rejects_authentication(self):
        root = Path(self._directory.name)
        wrong_station = connect(root / "wrong-station.sqlite3")
        self.addCleanup(wrong_station.close)
        seed(wrong_station)
        custody.pair(
            wrong_station, SHIFT, role="station",
            device_id=DEVICE, station_id=STATION, key=b"different-key",
        )
        custody_fixture_sample(self.device)
        item, proof = self.envelopes()[1]
        with self.assertRaises(ValueError):
            custody.receive(wrong_station, SHIFT, item, proof)

    def test_same_delivery_identity_with_different_content_is_conflict(self):
        custody_fixture_sample(self.device)
        original, original_proof = self.envelopes()[1]
        custody.receive(self.station, SHIFT, original, original_proof)

        rival_path = Path(self._directory.name) / "rival-device.sqlite3"
        rival = connect(rival_path)
        self.addCleanup(rival.close)
        seed(rival)
        custody.pair(
            rival, SHIFT, role="device",
            device_id=DEVICE, station_id=STATION, key=KEY,
        )
        record_reading(rival, "north-bay-custody-1", POINT, 9999)
        rival_item, rival_proof = custody.pending(rival, SHIFT)[0]
        self.assertEqual(rival_item.delivery_no, original.delivery_no)
        self.assertNotEqual(rival_item, original)

        with self.assertRaises(ValueError):
            custody.receive(self.station, SHIFT, rival_item, rival_proof)

        shift = get_shift(self.station, SHIFT)
        receipts = read_lane_receipts(self.station, shift.initial_lane)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0], original)

    def test_invalid_or_failed_release_keeps_originals(self):
        custody_fixture_sample(self.device)
        item, proof = self.envelopes()[1]
        confirmation = custody.receive(self.station, SHIFT, item, proof)

        with self.assertRaises(ValueError):
            custody.release(
                self.device, SHIFT, (confirmation[0], b"bad-proof")
            )
        self.assertEqual(
            [item.delivery_no for item, _ in custody.pending(self.device, SHIFT)],
            [1, 2, 3],
        )

        with self.assertRaises(sqlite3.DatabaseError):
            with reject_commit(self.device):
                custody.release(self.device, SHIFT, confirmation)

        self.assertEqual(
            [item.delivery_no for item, _ in custody.pending(self.device, SHIFT)],
            [1, 2, 3],
        )
        self.assertEqual(
            custody.release(self.device, SHIFT, confirmation), (1,)
        )
        self.assertEqual(
            [item.delivery_no for item, _ in custody.pending(self.device, SHIFT)],
            [2, 3],
        )

    def test_receive_commit_failure_leaves_no_delivery_or_confirmation(self):
        custody_fixture_sample(self.device)
        envelope = self.envelopes()[1]
        with self.assertRaises(sqlite3.DatabaseError):
            with reject_commit(self.station):
                custody.receive(self.station, SHIFT, *envelope)

        shift = get_shift(self.station, SHIFT)
        self.assertEqual(
            read_lane_receipts(self.station, shift.initial_lane), ()
        )
        confirmation = custody.receive(self.station, SHIFT, *envelope)
        self.assertEqual(confirmation[0], 1)


class CustodyFixtureControlTests(unittest.TestCase):
    def test_dual_database_demo_entry_passes(self):
        with TemporaryDirectory(prefix="custody-demo-check-") as directory:
            root = Path(directory)
            event, report = custody_demo.run(
                root / "device.sqlite3", root / "station.sqlite3", verbose=False
            )
        self.assertEqual(event["delivery_numbers"], [1, 2, 3])
        self.assertEqual(event["peak_mc"], 9300)
        self.assertTrue(event["station_reopen_same_report"])
        self.assertEqual(report.alerts.count, 3)

    def test_original_fixture_still_self_checks(self):
        with TemporaryDirectory(prefix="custody-original-fixture-") as directory:
            root = Path(directory)
            with closing(connect(root / "device.sqlite3")) as source, closing(
                connect(root / "station.sqlite3")
            ) as station:
                result = verify_fixture(source, station)
        self.assertEqual(result["replay_order"], [1, 3, 2])
        self.assertEqual(result["peak_mc"], 9300)


if __name__ == "__main__":
    unittest.main()
