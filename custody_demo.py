import argparse
import json
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import custody
from archive.history import get_report
from archive.publication import publish
from custody_fixture import (
    DEVICE,
    KEY,
    POINT,
    Relay,
    SHIFT,
    STATION,
    ReplyLost,
    TEMPERATURES,
    sample,
    seed,
)
from storage import connect


REPORT_ID = "north-bay-custody-report"


def _pair_both(device, station):
    custody.pair(
        device, SHIFT, role="device",
        device_id=DEVICE, station_id=STATION, key=KEY,
    )
    custody.pair(
        station, SHIFT, role="station",
        device_id=DEVICE, station_id=STATION, key=KEY,
    )


def run(device_path: Path, station_path: Path, *, verbose: bool = True):
    relay = Relay()

    # Each database is initialized and seeded independently with the same
    # fixed rule and shift binding.
    with closing(connect(device_path)) as device, closing(connect(station_path)) as station:
        seed(device)
        seed(station)
        _pair_both(device, station)

        first, middle, third = sample(device)
        envelopes = {
            item.delivery_no: (item, proof)
            for item, proof in custody.pending(device, SHIFT)
        }
        assert list(envelopes) == [1, 2, 3]
        assert [item.reading.temperature_mc for item, _ in envelopes.values()] == list(TEMPERATURES)

        first_confirmation = custody.receive(station, SHIFT, *envelopes[1])
        assert first_confirmation == (1, first_confirmation[1])

        # The station commits delivery 3 but the confirmation reply is lost.
        relay.drop_next_reply = True
        try:
            relay.send(custody.receive, station, SHIFT, *envelopes[3])
        except ReplyLost:
            lost_reply = 3
        else:
            raise AssertionError("configured custody reply loss did not occur")

        saved_third = envelopes[3]
        saved_middle = envelopes[2]
        saved_first_confirmation = first_confirmation

    # Both peers restart.  Pairing and received facts are persistent.
    with closing(connect(device_path)) as device, closing(connect(station_path)) as station:
        _pair_both(device, station)  # same parameters are idempotent

        replayed_third_confirmation = custody.receive(station, SHIFT, *saved_third)
        assert replayed_third_confirmation[0] == 1
        assert replayed_third_confirmation == saved_first_confirmation

        released_prefix_one = custody.release(
            device, SHIFT, replayed_third_confirmation
        )
        assert released_prefix_one == (1,)

        pending_numbers = tuple(
            item.delivery_no for item, _ in custody.pending(device, SHIFT)
        )
        assert pending_numbers == (2, 3)

        seal_envelope = custody.finish(device, SHIFT)
        seal, _seal_proof = seal_envelope
        assert (seal.cutoff, seal.last_delivery) == (3, 3)

        # The seal can arrive before the missing original.  It closes the
        # shift, but publication must still wait for delivery 2.
        assert custody.receive(station, SHIFT, *seal_envelope) is None
        assert publish(station, SHIFT, REPORT_ID) is None

        middle_confirmation = custody.receive(station, SHIFT, *saved_middle)
        assert middle_confirmation[0] == 3

        report = publish(station, SHIFT, REPORT_ID)
        assert report is not None
        assert [item.delivery_no for item in report.alerts.readings] == [1, 2, 3]
        assert [item.reading.source_seq for item in report.alerts.readings] == [1, 2, 3]
        assert report.alerts.count == 3
        assert report.alerts.peak_mc == 9300

        # The now-complete prefix authorizes removal of 2 and 3 too.
        released_remaining = custody.release(device, SHIFT, middle_confirmation)
        assert released_remaining == (2, 3)
        assert custody.pending(device, SHIFT) == ()

        device_seal_after_release = custody.finish(device, SHIFT)
        assert device_seal_after_release[0] == seal

        report_before_reopen = report

    # Stop-source restart at the station: retrieve the same signed report by ID.
    with closing(connect(station_path)) as reopened:
        reopened_report = get_report(reopened, REPORT_ID)
        assert reopened_report == report_before_reopen

    event = {
        "case_id": "north-bay-dual-db-custody",
        "point": POINT,
        "threshold_mc": 8000,
        "submitted_before_loss": [1, 3],
        "lost_confirmation_for_delivery": lost_reply,
        "replayed_prefix_coverage": replayed_third_confirmation[0],
        "released_on_prefix_1": list(released_prefix_one),
        "device_buffer_after_release": list(pending_numbers),
        "seal": {"cutoff": seal.cutoff, "last_delivery": seal.last_delivery},
        "report_while_delivery_2_missing": None,
        "supplemented_delivery": 2,
        "delivery_numbers": [item.delivery_no for item in report.alerts.readings],
        "source_sequences": [item.reading.source_seq for item in report.alerts.readings],
        "origins": [
            {
                "delivery_no": item.delivery_no,
                "recording": item.lane.recording,
                "generation": item.lane.generation,
                "slot": item.lane.slot,
                "rule_id": item.lane.rule.rule_id,
                "revision": item.lane.rule.revision,
                "point": item.reading.point,
            }
            for item in report.alerts.readings
        ],
        "count": report.alerts.count,
        "peak_mc": report.alerts.peak_mc,
        "first_receipt": asdict(report.receipt),
        "receipt": asdict(report.receipt),
        "released_on_complete_prefix": list(released_remaining),
        "station_reopen_same_report": reopened_report == report_before_reopen,
        "device_reopen_same_seal": device_seal_after_release[0] == seal,
    }
    if verbose:
        print("POINTWISE_SCENARIO " + json.dumps(event, ensure_ascii=False))
        print(json.dumps(asdict(reopened_report), ensure_ascii=False, indent=2))
    return event, reopened_report


def main():
    parser = argparse.ArgumentParser(description="北湾冷库双库托管续接演示")
    parser.add_argument("--temporary", action="store_true", help="use two temporary SQLite databases")
    parser.add_argument("--directory", type=Path, help="directory for device.sqlite3 and station.sqlite3")
    args = parser.parse_args()

    if args.directory is not None:
        args.directory.mkdir(parents=True, exist_ok=True)
        run(args.directory / "device.sqlite3", args.directory / "station.sqlite3")
    else:
        with TemporaryDirectory(prefix="coldchain-custody-demo-") as directory:
            run(Path(directory) / "device.sqlite3", Path(directory) / "station.sqlite3")


if __name__ == "__main__":
    main()
