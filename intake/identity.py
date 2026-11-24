from recorder.emissions import ReadingDelivery, SourceReading
from recorder.seals import LaneSeal


def source_key(item: SourceReading):
    return item.recording, item.source_seq


def delivery_key(item: ReadingDelivery):
    return item.lane.recording, item.lane.generation, item.delivery_no


def seal_key(item: LaneSeal):
    return item.lane.recording, item.lane.generation, item.cutoff
