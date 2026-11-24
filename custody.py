"""Persistent custody handoff between independent device and station databases.

The proofs in this module are opaque HMAC values.  They bind the message type,
the complete delivery/seal/confirmation value, the paired endpoint identities,
the shift, recording, lane and fixed rule revision.  Both databases therefore
have to contain the same pairing key and rule reference before a handoff can be
verified.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from control.shifts import get_shift
from intake.conflicts import classify_delivery, classify_seal
from intake.receipts import read_lane_receipts, read_lane_seals
from intake.provenance import resolve_lane, validate_envelope
from recorder.emissions import ReadingDelivery, SourceReading
from recorder.recording import stop_recording
from recorder.seals import LaneSeal
from recorder.slots import LaneRef
from rules.catalog import get_rule
from storage import atomic, require_name


_DOMAIN = "north-bay-coldchain/custody/v1"
_MAX_SEQUENCE = (1 << 63) - 1

__all__ = ("pair", "pending", "finish", "receive", "release")


def _outside_transaction(db):
    if db.in_transaction:
        raise ValueError("custody calls require a connection without an outer transaction")


def _require_key(key):
    if type(key) is not bytes or not key:
        raise ValueError("key must be nonempty bytes")
    return key


def pair(db, shift_id, *, role, device_id, station_id, key):
    """Persist this endpoint's side of a shift pairing."""
    _outside_transaction(db)
    require_name(shift_id, "shift_id")
    if role not in ("device", "station"):
        raise ValueError("role must be 'device' or 'station'")
    require_name(device_id, "device_id")
    require_name(station_id, "station_id")
    _require_key(key)

    shift = get_shift(db, shift_id)
    lane = shift.initial_lane
    with atomic(db):
        row = db.execute(
            "SELECT * FROM custody_pair WHERE shift_id = ?", (shift_id,)
        ).fetchone()
        if row is not None:
            same = (
                row["role"] == role
                and row["device_id"] == device_id
                and row["station_id"] == station_id
                and row["key"] == key
                and row["recording"] == shift.recording
                and row["generation"] == lane.generation
            )
            if not same:
                raise ValueError("shift is already paired with different custody data")
            return None

        db.execute(
            """INSERT INTO custody_pair
               (shift_id, role, device_id, station_id, key, recording, generation)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (shift_id, role, device_id, station_id, key,
             shift.recording, lane.generation),
        )
    return None


def _paired_context(db, shift_id, role):
    row = db.execute(
        "SELECT * FROM custody_pair WHERE shift_id = ?", (shift_id,)
    ).fetchone()
    if row is None:
        raise ValueError("custody pairing is required before this operation")
    shift = get_shift(db, shift_id)
    lane = shift.initial_lane
    if row["recording"] != shift.recording or row["generation"] != lane.generation:
        raise ValueError("custody pairing does not belong to this shift lane")
    if row["role"] != role:
        raise ValueError(f"custody endpoint must have role {role!r}")
    return shift, row, lane


def _lane_identity(lane: LaneRef):
    return {
        "recording": lane.recording,
        "generation": lane.generation,
        "slot": lane.slot,
        "rule_id": lane.rule.rule_id,
        "revision": lane.rule.revision,
    }


def _lane_context(db, lane: LaneRef):
    rule = get_rule(db, lane.rule)
    result = _lane_identity(lane)
    result.update(point=rule.point, threshold_mc=rule.threshold_mc)
    return result


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sign(row, db, lane, purpose, message):
    body = {
        "domain": _DOMAIN,
        "purpose": purpose,
        "context": {
            "shift_id": row["shift_id"],
            "device_id": row["device_id"],
            "station_id": row["station_id"],
            "lane": _lane_context(db, lane),
        },
        "message": message,
    }
    return hmac.new(bytes(row["key"]), _canonical(body), hashlib.sha256).digest()


def _assert_proof(row, db, lane, purpose, message, proof):
    if type(proof) is not bytes or not proof:
        raise ValueError("custody proof must be nonempty bytes")
    expected = _sign(row, db, lane, purpose, message)
    if not hmac.compare_digest(expected, proof):
        raise ValueError("custody proof does not authenticate the complete item")


def _delivery_message(item: ReadingDelivery):
    reading = item.reading
    return {
        "kind": "ReadingDelivery",
        "lane": _lane_identity(item.lane),
        "delivery_no": item.delivery_no,
        "source_seq": reading.source_seq,
        "reading_recording": reading.recording,
        "point": reading.point,
        "temperature_mc": reading.temperature_mc,
    }


def _seal_message(item: LaneSeal):
    return {
        "kind": "LaneSeal",
        "lane": _lane_identity(item.lane),
        "cutoff": item.cutoff,
        "last_delivery": item.last_delivery,
    }


def pending(db, shift_id):
    """Return all originals still held by the device, each with its proof."""
    _outside_transaction(db)
    _shift, row, lane = _paired_context(db, shift_id, "device")
    rows = db.execute(
        """SELECT delivery_no, source_seq, point, temperature_hex
           FROM emissions
           WHERE recording = ? AND generation = ?
             AND delivery_no NOT IN (
                 SELECT delivery_no FROM custody_delivery_history
                 WHERE recording = ? AND generation = ?
             )
           ORDER BY delivery_no""",
        (lane.recording, lane.generation, lane.recording, lane.generation),
    ).fetchall()

    result = []
    for data in rows:
        reading = SourceReading(
            lane.recording,
            data["source_seq"],
            data["point"],
            int(data["temperature_hex"], 16),
        )
        item = ReadingDelivery(lane, data["delivery_no"], reading)
        proof = _sign(row, db, lane, "delivery", _delivery_message(item))
        result.append((item, proof))
    return tuple(result)


def finish(db, shift_id):
    """Stop the fixed lane and return its authentic final seal."""
    _outside_transaction(db)
    _shift, row, lane = _paired_context(db, shift_id, "device")
    seals = stop_recording(db, lane.recording)
    seal = next((candidate for candidate in seals if candidate.lane == lane), None)
    if seal is None:
        raise ValueError("the shift's fixed lane has no final seal")
    proof = _sign(row, db, lane, "final-seal", _seal_message(seal))
    return seal, proof


def _prefix_coverage(db, lane):
    rows = db.execute(
        """SELECT delivery_no FROM received_deliveries
           WHERE recording = ? AND generation = ?""",
        (lane.recording, lane.generation),
    ).fetchall()
    received = {row["delivery_no"] for row in rows}
    coverage = 0
    while coverage + 1 in received:
        coverage += 1
    return coverage


def receive(db, shift_id, item, proof):
    """Independently verify and persist a delivery or final seal.

    Deliveries return ``(prefix_coverage, confirmation_proof)``.  Final seals
    return ``None``.
    """
    _outside_transaction(db)
    if not isinstance(item, (ReadingDelivery, LaneSeal)):
        raise ValueError("unsupported custody item type")

    _shift, row, lane = _paired_context(db, shift_id, "station")
    binding = resolve_lane(db, item.lane)
    if binding.lane != lane:
        raise ValueError("custody item belongs to a different shift route")
    validate_envelope(item, binding)

    if isinstance(item, ReadingDelivery):
        message = _delivery_message(item)
        purpose = "delivery"
    else:
        message = _seal_message(item)
        purpose = "final-seal"
    _assert_proof(row, db, lane, purpose, message, proof)

    coverage = None
    with atomic(db):
        # Re-resolve inside the write transaction so classification and insert
        # share the same committed local facts.
        binding = resolve_lane(db, item.lane)
        validate_envelope(item, binding)
        deliveries = read_lane_receipts(db, lane)
        seals = read_lane_seals(db, lane)

        if isinstance(item, ReadingDelivery):
            status = classify_delivery(item, deliveries, seals)
            if status == "accepted":
                reading = item.reading
                db.execute(
                    """INSERT INTO received_deliveries
                       (recording, generation, delivery_no, source_seq,
                        point, temperature_hex)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (item.lane.recording, item.lane.generation, item.delivery_no,
                     reading.source_seq, reading.point,
                     format(reading.temperature_mc, "x")),
                )
            coverage = _prefix_coverage(db, lane)
        else:
            status = classify_seal(item, deliveries, seals)
            if status == "accepted":
                db.execute(
                    """INSERT INTO received_seals
                       (recording, generation, cutoff, last_delivery)
                       VALUES (?, ?, ?, ?)""",
                    (item.lane.recording, item.lane.generation,
                     item.cutoff, item.last_delivery),
                )
            previous = db.execute(
                "SELECT end_seq FROM shift_closures WHERE shift_id = ?",
                (shift_id,),
            ).fetchone()
            if previous is None:
                db.execute(
                    "INSERT INTO shift_closures (shift_id, end_seq) VALUES (?, ?)",
                    (shift_id, item.cutoff),
                )
            elif previous["end_seq"] != item.cutoff:
                raise ValueError("final seal contradicts the registered shift end")

    if isinstance(item, LaneSeal):
        return None

    confirmation_message = {"mode": "prefix", "n": coverage}
    confirmation_proof = _sign(
        row, db, lane, "confirmation", confirmation_message
    )
    return coverage, confirmation_proof


def _confirmation_spec(confirmation):
    if not isinstance(confirmation, tuple) or len(confirmation) != 2:
        raise ValueError("confirmation must be a (coverage, proof) tuple")
    coverage, proof = confirmation

    if type(coverage) is int:
        if not 0 <= coverage <= _MAX_SEQUENCE:
            raise ValueError("prefix coverage is outside the valid range")
        message = {"mode": "prefix", "n": coverage}
        return coverage, None, message, proof

    if type(coverage) is tuple:
        previous = 0
        for number in coverage:
            if (type(number) is not int or not 1 <= number <= _MAX_SEQUENCE
                    or number <= previous):
                raise ValueError("item coverage must be strictly increasing positive integers")
            previous = number
        message = {"mode": "items", "numbers": list(coverage)}
        return None, coverage, message, proof

    raise ValueError("confirmation coverage must be an int or a tuple of ints")


def release(db, shift_id, confirmation):
    """Remove originals explicitly covered by a valid confirmation."""
    _outside_transaction(db)
    prefix, numbers, message, proof = _confirmation_spec(confirmation)
    _shift, row, lane = _paired_context(db, shift_id, "device")
    _assert_proof(row, db, lane, "confirmation", message, proof)

    released = []
    with atomic(db):
        if prefix is not None:
            rows = db.execute(
                """SELECT delivery_no, source_seq
                   FROM emissions
                   WHERE recording = ? AND generation = ?
                     AND delivery_no >= 1 AND delivery_no <= ?
                   ORDER BY delivery_no""",
                (lane.recording, lane.generation, prefix),
            ).fetchall()
            candidates = tuple(
                (row["delivery_no"], row["source_seq"]) for row in rows
            )
        else:
            candidates = []
            for number in numbers:
                row = db.execute(
                    """SELECT delivery_no, source_seq
                       FROM emissions
                       WHERE recording = ? AND generation = ? AND delivery_no = ?""",
                    (lane.recording, lane.generation, number),
                ).fetchone()
                if row is not None:
                    candidates.append((row["delivery_no"], row["source_seq"]))
            candidates = tuple(candidates)

        for delivery_no, source_seq in candidates:
            # A bodyless identity history keeps delivery numbers and seal
            # accounting monotonic after the full original row is deleted.
            db.execute(
                """INSERT INTO custody_delivery_history
                   (shift_id, recording, generation, delivery_no, source_seq)
                   VALUES (?, ?, ?, ?, ?)""",
                (shift_id, lane.recording, lane.generation,
                 delivery_no, source_seq),
            )
            db.execute(
                """DELETE FROM emissions
                   WHERE recording = ? AND generation = ? AND delivery_no = ?""",
                (lane.recording, lane.generation, delivery_no),
            )
            released.append(delivery_no)

    return tuple(released)
