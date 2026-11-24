from rules.revisions import RuleRevision


def matches(rule: RuleRevision, point: str, temperature_mc: int) -> bool:
    return point == rule.point and temperature_mc >= rule.threshold_mc
