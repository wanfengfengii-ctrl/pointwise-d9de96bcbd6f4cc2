from rules.revisions import RuleRef, RuleRevision, load_revision, store_revision
from rules.units import parse_millicelsius
from storage import atomic, require_name


def register_rule(db, rule_id, revision, point, threshold_mc, unit='mC'):
    ref = RuleRef(require_name(rule_id, 'rule_id'), require_name(revision, 'revision'))
    rule = RuleRevision(ref, require_name(point, 'point'), parse_millicelsius(threshold_mc, unit))
    with atomic(db):
        return store_revision(db, rule)


def get_rule(db, ref):
    if not isinstance(ref, RuleRef):
        raise ValueError('a fixed rule reference is required')
    return load_revision(db, ref)
