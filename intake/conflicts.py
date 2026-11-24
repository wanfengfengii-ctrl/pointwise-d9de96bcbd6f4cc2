from intake.identity import delivery_key, seal_key


def _seal_agrees_with_delivery(seal, delivery):
    rank = delivery.delivery_no
    source = delivery.reading.source_seq
    if rank <= seal.last_delivery:
        return source + seal.last_delivery - rank <= seal.cutoff
    return source - (rank - seal.last_delivery) >= seal.cutoff


def classify_delivery(item, deliveries, seals):
    key = delivery_key(item)
    for prior in deliveries:
        if delivery_key(prior) == key:
            if prior != item:
                raise ValueError('conflicting delivery at an occupied sequence')
            return 'duplicate'
        rank_delta = item.delivery_no - prior.delivery_no
        source_delta = item.reading.source_seq - prior.reading.source_seq
        if (rank_delta > 0 and source_delta < rank_delta) or (rank_delta < 0 and source_delta > rank_delta):
            raise ValueError('delivery sequence contradicts source order')
    if any(not _seal_agrees_with_delivery(seal, item) for seal in seals):
        raise ValueError('delivery contradicts a received seal')
    return 'accepted'


def classify_seal(item, deliveries, seals):
    key = seal_key(item)
    for prior in seals:
        if seal_key(prior) == key:
            if prior != item:
                raise ValueError('conflicting seal at an occupied cutoff')
            return 'duplicate'
        if prior.cutoff < item.cutoff:
            earlier, later = prior, item
        else:
            earlier, later = item, prior
        if not 0 <= later.last_delivery - earlier.last_delivery <= later.cutoff - earlier.cutoff:
            raise ValueError('received seals imply contradictory delivery counts')
    if any(not _seal_agrees_with_delivery(item, delivery) for delivery in deliveries):
        raise ValueError('seal contradicts a received delivery')
    return 'accepted'
