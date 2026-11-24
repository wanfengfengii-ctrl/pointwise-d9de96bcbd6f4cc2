from dataclasses import replace

from archive.publication import publish
from intake.receipts import read_lane_receipts, read_lane_seals, receive
from recorder.seals import seal_lane
from tests.fixture import ColdChainCase


class IntakeTests(ColdChainCase):
    def test_original_delivery_survives_replay_and_conflict(self):
        shift = self.open()
        first, second = self.sample(9100, 9100)
        self.assertEqual(receive(self.db, first).status, 'accepted')
        replay = receive(self.db, first)
        self.assertEqual((replay.status, replay.item), ('duplicate', first))
        conflicting = replace(first, reading=replace(first.reading, temperature_mc=9200))
        with self.assertRaisesRegex(ValueError, 'occupied sequence'):
            receive(self.db, conflicting)
        self.assertEqual(read_lane_receipts(self.db, shift.initial_lane), (first,))
        self.deliver(second, *self.seal())
        self.reopen()
        self.assertEqual(read_lane_receipts(self.db, shift.initial_lane), (first, second))
        self.assertEqual(publish(self.db, 'shift', 'intake').alerts.readings, (first, second))

    def test_unknown_lane_and_unemitted_reading_are_rejected(self):
        shift = self.open()
        real, = self.sample(9000)
        unknown = replace(real, lane=replace(real.lane, generation=7))
        with self.assertRaisesRegex(ValueError, 'unknown lane'):
            receive(self.db, unknown)
        altered = replace(real, reading=replace(real.reading, point='east'))
        with self.assertRaisesRegex(ValueError, 'was not emitted'):
            receive(self.db, altered)
        self.assertEqual(read_lane_receipts(self.db, shift.initial_lane), ())
        self.assertEqual(receive(self.db, real).item, real)

    def test_conflicting_seals_leave_first_facts_intact(self):
        shift = self.open()
        first, second = self.sample(9100, 9300)
        intermediate = seal_lane(self.source, shift.initial_lane, 1)
        end, = self.seal()
        self.assertEqual(receive(self.db, end).status, 'accepted')
        self.assertEqual(receive(self.db, end).status, 'duplicate')
        self.assertEqual(receive(self.db, intermediate).status, 'accepted')
        with self.assertRaisesRegex(ValueError, 'occupied cutoff'):
            receive(self.db, replace(end, last_delivery=1))
        self.assertEqual(read_lane_seals(self.db, shift.initial_lane), (intermediate, end))
        self.assertEqual(read_lane_receipts(self.db, shift.initial_lane), ())
        self.deliver(second, first)
        self.assertEqual(publish(self.db, 'shift', 'sealed').alerts.readings, (first, second))
