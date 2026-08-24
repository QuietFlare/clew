"""
Clew core — the pre-flight gate.

THIS FILE KNOWS NOTHING ABOUT BIOLOGY. It takes a set of opaque subject ids,
a set of opaque facts about them, and a statement of which fact types block.

WHAT A GATE IS, AND WHY IT IS NOT A BLAST RADIUS
------------------------------------------------
Everything else in Clew answers a question after the fact: this went wrong,
what must happen now. A gate asks the opposite question, before anything runs:
is any of this material something we are not allowed to use?

The two are not variations on each other. A blast radius traverses a graph of
work that already happened. A gate looks at a list of inputs and a log of
facts, and there is no graph yet because nothing has run.

THREE OUTCOMES, NOT TWO
-----------------------
    BLOCKED   a fact in effect says do not use this
    CLEARED   a fact in effect says it is fine
    UNKNOWN   the log has nothing to say about this subject at all

The third one is the entire reason this file is careful. A gate that reports
UNKNOWN as "not blocked" passes everything it failed to check, and the day
someone mistypes an identifier — or points at the wrong log, or connects with
a role that cannot read — it goes green while checking nothing.

That is the same failure this project has now made twice: an absent answer
being reported as a clean one. So UNKNOWN is a distinct outcome, it is
counted, and the caller must decide explicitly what to do with it.

LATEST EFFECTIVE FACT WINS
--------------------------
Decisions get reversed. A subject withdrawn in March and reinstated in June
is usable in July, and a gate that only ever accumulated prohibitions would
be wrong about that in the direction of refusing legitimate work.

So for each subject, the LATEST DECISIVE FACT IN EFFECT decides — ordered by
effective_from, which is when the decision was made in the world, not by
recorded_at, which is merely when we heard. Two facts effective on the same
date are broken by log order, because the log is the only tiebreak that
cannot be back-dated by whoever entered them.

Facts effective in the FUTURE relative to the question do not count. Asking
"was this usable on 1 March" must not be answered with a decision taken in
June, or every historical gate result becomes unreproducible the moment
someone records something new.
"""

BLOCKED = "BLOCKED"
CLEARED = "CLEARED"
UNKNOWN = "UNKNOWN"


def decisive_facts(entries, blocking, clearing, as_of=None):
    """
    The facts that can decide anything, in the order they took effect.

    `blocking` and `clearing` are sets of opaque type names. Core does not
    know what any of them mean — a domain decides which of its vocabulary
    belongs in which set, and that mapping is the customer's to author.
    """
    decisive = set(blocking) | set(clearing)
    relevant = [e for e in entries if e["event_type"] in decisive]
    if as_of is not None:
        relevant = [e for e in relevant if e["effective_from"] <= as_of]
    # effective_from first, log order as the tiebreak. See the module
    # docstring: seq is the only ordering nobody can back-date.
    return sorted(relevant, key=lambda e: (e["effective_from"], e["seq"]))


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
    The gate's verdict, with everything needed to defend it.

    `unknown_blocks` defaults True. A gate exists to stop work that should
    not proceed, and the caller who cannot say whether a subject is permitted
    has not established that it is. Turning it off is a real and sometimes
    correct choice — a pilot run against a log that only covers part of an
    estate — but it must be a choice someone made, not a default they
    inherited without noticing.
    """
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
