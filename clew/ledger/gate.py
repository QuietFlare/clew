"""
The admission decision: opaque subject ids, opaque facts, and which fact
types block or clear.

A blast radius traverses work that happened. A gate looks at a list of
inputs before anything runs. Three outcomes: BLOCKED, CLEARED and UNKNOWN,
where the log has nothing to say. UNKNOWN is its own outcome because a gate
that treats it as clear goes green while checking nothing, the day an
identifier is mistyped or the wrong log is named.

For each subject the latest decisive fact in effect decides, ordered by
effective_from with log order as the tiebreak. Facts effective after the
question's date do not count, so a historical answer stays reproducible.
"""

from clew.ledger.eventlog import in_effect, instant, now

BLOCKED = "BLOCKED"
CLEARED = "CLEARED"
UNKNOWN = "UNKNOWN"


def decisive_facts(entries, blocking, clearing, as_of=None):
    """
    The facts that can decide anything, in effective order. `blocking` and
    `clearing` are opaque type names the provider chose. Timestamps compare
    as instants, never as text.
    """
    decisive = set(blocking) | set(clearing)
    relevant = [e for e in entries if e["event_type"] in decisive]
    if as_of is not None:
        relevant = [e for e in relevant
                    if in_effect(e["effective_from"], as_of)]
    # effective_from first, log order as the tiebreak. See the module
    # docstring: seq is the only ordering nobody can back-date.
    return sorted(relevant,
                  key=lambda e: (instant(e["effective_from"]), e["seq"]))


def status_by_subject(subjects, entries, blocking, clearing, as_of=None):
    """
    One outcome per subject, with the fact that produced it.

    Subjects the log has never heard of come back UNKNOWN with no fact, which
    is different from CLEARED in the way that matters: nobody has said this is
    fine, we simply have no record either way.
    """
    blocking, clearing = set(blocking), set(clearing)
    latest = {}
    for entry in decisive_facts(entries, blocking, clearing, as_of):
        latest[entry["subject"]] = entry

    result = {}
    for subject in subjects:
        fact = latest.get(subject)
        if fact is None:
            result[subject] = {"status": UNKNOWN, "fact": None,
                               "reason": "the log holds no decisive fact about "
                                         "this subject"}
            continue
        status = BLOCKED if fact["event_type"] in blocking else CLEARED
        result[subject] = {
            "status": status,
            "fact": {"seq": fact["seq"], "event_type": fact["event_type"],
                     "effective_from": fact["effective_from"],
                     "recorded_at": fact["recorded_at"],
                     "actor": fact["actor"], "hash": fact["hash"]},
            "reason": (f"{fact['event_type']} effective "
                       f"{fact['effective_from']}, asserted by {fact['actor']}"),
        }
    return result


def decide(subjects, entries, blocking, clearing, as_of=None,
           unknown_blocks=True):
    """
    The verdict with what is needed to defend it. `unknown_blocks` defaults
    True: a caller who cannot say a subject is permitted has not established
    it, and turning that off must be a choice someone made. `as_of` defaults
    to now and is recorded, so the result can be re-derived after the log
    grows.
    """
    if as_of is None:
        as_of = now()
    else:
        instant(as_of)
    statuses = status_by_subject(subjects, entries, blocking, clearing, as_of)
    counts = {BLOCKED: 0, CLEARED: 0, UNKNOWN: 0}
    for detail in statuses.values():
        counts[detail["status"]] += 1

    stopped = counts[BLOCKED] > 0 or (unknown_blocks and counts[UNKNOWN] > 0)
    return {
        "clew_gate_version": 1,
        "as_of": as_of,
        "blocking_types": sorted(set(blocking)),
        "clearing_types": sorted(set(clearing)),
        "unknown_blocks": unknown_blocks,
        "subjects": dict(sorted(statuses.items())),
        "counts": counts,
        "passed": not stopped,
    }
