from recorder.emissions import ReadingDelivery, SourceReading, read_delivery
from recorder.seals import LaneSeal, read_seal
from recorder.slots import LaneRef, load_binding


_MAX_SEQUENCE = (1 << 63) - 1


def _sequence(value, minimum):
    return type(value) is int and minimum <= value <= _MAX_SEQUENCE


def resolve_lane(db, lane):
    if (not isinstance(lane, LaneRef) or type(lane.recording) is not str
        or not _sequence(lane.generation, 0) or type(lane.slot) is not int or lane.slot not in (0, 1)):
        raise ValueError('unknown lane')
    try:
        binding = load_binding(db, lane.recording, lane.generation)
    except KeyError as error:
        raise ValueError('unknown lane') from error
    if binding.lane != lane:
        raise ValueError('lane attributes disagree with the original binding')
    return binding


def validate_envelope(item, binding):
    if isinstance(item, ReadingDelivery):
        reading = item.reading
        if (not isinstance(reading, SourceReading) or not _sequence(item.delivery_no, 1)
            or not _sequence(reading.source_seq, 1) or type(reading.temperature_mc) is not int
            or type(reading.point) is not str or reading.recording != binding.lane.recording):
            raise ValueError('invalid delivery fields')
        if reading.source_seq < binding.active_after + item.delivery_no:
            raise ValueError('delivery rank exceeds the lane source interval')
        if binding.retired_through is not None and reading.source_seq > binding.retired_through:
            raise ValueError('delivery lies beyond the retired lane')
    elif (not _sequence(item.cutoff, 0) or not _sequence(item.last_delivery, 0)
          or item.cutoff < binding.active_after + item.last_delivery
          or (binding.retired_through is not None and item.cutoff > binding.retired_through)):
        raise ValueError('invalid seal fields or cutoff')


def verify_origin(db, item):
    if isinstance(item, ReadingDelivery):
        original = read_delivery(db, item.lane, item.delivery_no)
    elif isinstance(item, LaneSeal):
        original = read_seal(db, item.lane, item.cutoff)
    else:
        raise ValueError('unsupported receipt type')
    if original is None or original != item:
        raise ValueError('item was not emitted as stated by its device lane')
