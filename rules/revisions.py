from dataclasses import dataclass


@dataclass(frozen=True)
class RuleRef:
    rule_id: str
    revision: str


@dataclass(frozen=True)
class RuleRevision:
    ref: RuleRef
    point: str
    threshold_mc: int


def store_revision(db, revision):
    ref = revision.ref
    db.execute(
        '''INSERT INTO rule_revisions (rule_id, revision, point, threshold_hex)
           VALUES (?, ?, ?, ?) ON CONFLICT(rule_id, revision) DO NOTHING''',
        (ref.rule_id, ref.revision, revision.point, format(revision.threshold_mc, 'x')),
    )
    current = load_revision(db, ref)
    if current != revision:
        raise ValueError('rule revision already has different parameters')
    return current.ref


def load_revision(db, ref):
    row = db.execute(
        'SELECT point, threshold_hex FROM rule_revisions WHERE rule_id = ? AND revision = ?',
        (ref.rule_id, ref.revision),
    ).fetchone()
    if row is None:
        raise KeyError(ref)
    return RuleRevision(ref, row['point'], int(row['threshold_hex'], 16))
