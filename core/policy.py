"""
Clew core — the remediation policy, versioned.

THIS FILE KNOWS NOTHING ABOUT BIOLOGY. It maps four opaque dimensions onto one
action and records which rule did it.

WHY THIS EXISTS
---------------
Clew's second claim is that the computation is deterministic and reproducible.
A decision table written as an if-ladder cannot support that claim, for one
reason: editing it silently re-interprets every plan ever produced. A plan
from March says QUARANTINE; the code says QUARANTINE today; nobody can tell
whether it said QUARANTINE in March. The history is unfalsifiable, which is
the same as worthless.

So the table becomes DATA with a version and a content hash, every decision
names the rule that made it, and every plan carries the policy it was
computed under. "Policy v1, rule R5, these hashes, re-run and get the same
answer" is then a checkable sentence rather than a slogan.

WHAT IS AND IS NOT VERSIONED HERE
---------------------------------
This is the CORE decision table: given a contribution class, a storage state,
whether the artifact is exclusively owned, and whether it is terminal, what
must happen. It defines what the classes MEAN, so it is ours, not the
customer's. Changing it changes the semantics of every historical plan, which
is exactly why it needs a version.

The customer's policy is a different object: which of their events map to
which contribution class, what counts as published, what a given tier of
withdrawal is allowed to reach. That lives in domains/ and is not this file.
CLAUDE.md's "the customer authors the policy, we ship templates" is about
that layer. Conflating the two would let a customer redefine SEPARABLE, and
then no two Clew deployments would mean the same thing by the same word.

RULES ARE MATCH DICTS, FIRST MATCH WINS
---------------------------------------
    {"id": "R3", "when": {"exclusive": True, "storage": "WRITABLE"},
     "action": "DESTROY", "because": "..."}

An omitted dimension is a wildcard. This is not a rule engine and must not
grow into one: no negation, no arithmetic, no expressions. The entire
semantics is "does every named field equal this value", and the reason is
that an auditor has to be able to read the policy. A condition language rich
enough to be interesting is rich enough to be argued about.

AN UNVERIFIED DIMENSION IS NOT A VALUE
--------------------------------------
Storage state is not lineage. Lineage says what was derived from what and is
permanently true; storage says whether the bytes are still there and is true
only at the instant you look. Whoever asks Clew a question may or may not be
standing somewhere that can look.

When a dimension is unverified, decide() is given None for it and evaluates
the policy once per possible value:

  - every value yields the same action  ->  that action is certain anyway,
                                            and is returned normally
  - the values disagree                 ->  no action is returned at all

The second case is the whole point of the design. Guessing WRITABLE
over-claims an obligation and wastes work; guessing DESTROYED yields
ALREADY_GONE, which reads as "you have nothing to do" and is the one error
this project exists not to make. Returning neither is the only honest answer,
and it names which verdicts are still in play so the reader knows exactly
what verifying the storage would settle.

No new action is invented for this. The action enum is closed — a new verdict
would change what remediation means — so an undetermined item simply HAS no
action, and carries the candidates instead.

TWO GUARDS SIT OUTSIDE THE RULE LIST
------------------------------------
  1. The contribution class is normalised before matching. Anything
     unrecognised becomes IRREDUCIBLE first, and no rule can test for an
     unrecognised class because validate() rejects such a rule.
  2. Falling off the end of the rules yields QUARANTINE — not an error, and
     not a pass. An incomplete policy is cautious rather than permissive.

BE PRECISE ABOUT WHAT THAT GUARANTEES, THOUGH. It fixes the FACTS, not the
VERDICT. A policy that maps IRREDUCIBLE to PURGE is expressible, and would be
wrong, and Clew will run it. That is not a hole — it is the reason the table
is data. Wrong logic in an if-ladder is invisible in a code review nobody
does; wrong logic in a hashed, versioned, rationale-carrying policy file is
sitting in the open with a rule id on it. There is a test named for this so
that nobody later mistakes it for an oversight and "fixes" it by hardcoding
verdicts back into core.
"""

import hashlib
import json

from core import contribution

# The dimensions a rule may test. A rule naming anything else is rejected at
# load time rather than silently never matching.
DIMENSIONS = ("contribution", "storage", "exclusive", "terminal")

VALID = {
    "contribution": set(contribution.CLASSES),
    "storage": set(contribution.STORAGE),
    "exclusive": {True, False},
    "terminal": {True, False},
}

ACTIONS = {
    contribution.PURGE, contribution.REGENERATE, contribution.QUARANTINE,
    contribution.DESTROY, contribution.NOTIFY_ONLY, contribution.ALREADY_GONE,
}

# The label for an item with no verdict. Deliberately NOT a member of
# ACTIONS: it is the absence of an action, not a seventh one, and code that
# iterates the action set must not find it there and start treating it as a
# remediation someone could carry out.
UNDETERMINED = "UNDETERMINED"

# What a decision falls back to when no rule matches. See the module docstring:
# this is deliberately not expressible as a rule.
FALLTHROUGH_ACTION = contribution.QUARANTINE
FALLTHROUGH_RULE = "fallthrough"


def rule(rule_id, action, because, **when):
    return {"id": rule_id, "when": when, "action": action, "because": because}


# --------------------------------------------------------------- the policy

V1 = {
    "version": "v1",
    "description": "Clew's built-in remediation table.",
    "rules": [
        rule("R1", contribution.ALREADY_GONE,
             "Nothing survives to remediate. Asked first because every later "
             "question presumes an artifact still exists.",
             storage=contribution.DESTROYED),

        rule("R2", contribution.NOTIFY_ONLY,
             "Immutable history — published, or already past a trust "
             "boundary. Terminates remediation, not notification: you cannot "
             "unpublish, so the answer is disclosure.",
             terminal=True),

        rule("R3", contribution.DESTROY,
             "Exists only because of this subject and the bytes can be "
             "changed. Nothing else needs it, so it goes entirely.",
             exclusive=True, storage=contribution.WRITABLE),

        rule("R4", contribution.QUARANTINE,
             "Exists only because of this subject, but the storage cannot be "
             "written. Removal is correct and unavailable, so block use.",
             exclusive=True),

        rule("R5", contribution.PURGE,
             "The contribution can be isolated and the bytes can be changed. "
             "Subtract it in place; the artifact survives for everyone else.",
             contribution=contribution.SEPARABLE,
             storage=contribution.WRITABLE),

        rule("R6", contribution.REGENERATE,
             "Separable in principle but the artifact is unwritable. Rewriting "
             "in place is not required — produce a fresh one without it.",
             contribution=contribution.SEPARABLE),

        rule("R7", contribution.REGENERATE,
             "Cannot be isolated, but the derivation can be re-executed from "
             "the remaining sources.",
             contribution=contribution.REGENERABLE),

        rule("R8", contribution.QUARANTINE,
             "IRREDUCIBLE: neither separable nor re-executable. Nothing can be "
             "removed and nothing can be rebuilt, so block further use. Also "
             "where every unrecognised class lands, by normalisation.",
             contribution=contribution.IRREDUCIBLE),
    ],
}

# --------------------------------------------------------------------- v2
#
# WHY v2 EXISTS: in v1, "does it still exist?" is asked before "was it
# published?". A published artifact whose working copy had been deleted came
# back ALREADY_GONE — "nothing to do" — which is wrong. Deleting your copy of
# something does not un-publish it. The disclosure obligation survives the
# bytes, and the same holds for material that has left under an agreement:
# our copy being gone does not reach the partner's.
#
# The practical consequence is sharper than it first looks. Because R1 is the
# only rule that can yield ALREADY_GONE, putting it first made EVERY verdict
# depend on the storage state — so under v1 nothing at all is decidable
# without a disk check. Under v2 a published artifact resolves to NOTIFY_ONLY
# whatever the disk says, because the answer genuinely does not depend on it.
#
# V1 IS LEFT EXACTLY AS IT WAS, byte for byte. Plans computed in January cite
# it and must stay replayable; editing it in place would make the version
# label a lie and the hash meaningless. A semantic change is a new version,
# never an edit. There is a test pinning v1's hash to a literal so that this
# cannot happen by accident.
#
# RULE IDS ARE STABLE ACROSS VERSIONS. R2 is the same rule here as in v1, in
# a different position — ids identify rules, not positions, so two plans on
# different versions remain comparable line by line.

V2 = {
    "version": "v2",
    "description": ("Clew's remediation table. Publication is asked before "
                    "existence: a deleted working copy does not discharge a "
                    "disclosure obligation."),
    "rules": [
        rule("R2", contribution.NOTIFY_ONLY,
             "Immutable history — published, or already past a trust "
             "boundary. Asked first, before existence: destroying our copy "
             "does not reach the published or transferred one, so the "
             "obligation to disclose survives the bytes.",
             terminal=True),

        rule("R1", contribution.ALREADY_GONE,
             "Nothing survives to remediate, and nothing left our hands. "
             "Every later question presumes an artifact still exists, so this "
             "is asked early — but after publication, which outlives it.",
             storage=contribution.DESTROYED),

        rule("R3", contribution.DESTROY,
             "Exists only because of this subject and the bytes can be "
             "changed. Nothing else needs it, so it goes entirely.",
             exclusive=True, storage=contribution.WRITABLE),

        rule("R4", contribution.QUARANTINE,
             "Exists only because of this subject, but the storage cannot be "
             "written. Removal is correct and unavailable, so block use.",
             exclusive=True),

        rule("R5", contribution.PURGE,
             "The contribution can be isolated and the bytes can be changed. "
             "Subtract it in place; the artifact survives for everyone else.",
             contribution=contribution.SEPARABLE,
             storage=contribution.WRITABLE),

        rule("R6", contribution.REGENERATE,
             "Separable in principle but the artifact is unwritable. Rewriting "
             "in place is not required — produce a fresh one without it.",
             contribution=contribution.SEPARABLE),

        rule("R7", contribution.REGENERATE,
             "Cannot be isolated, but the derivation can be re-executed from "
             "the remaining sources.",
             contribution=contribution.REGENERABLE),

        rule("R8", contribution.QUARANTINE,
             "IRREDUCIBLE: neither separable nor re-executable. Nothing can be "
             "removed and nothing can be rebuilt, so block further use. Also "
             "where every unrecognised class lands, by normalisation.",
             contribution=contribution.IRREDUCIBLE),
    ],
}

DEFAULT = V2

# Every policy ever shipped, so a plan citing an old version can be replayed
# under the table that was actually in force when it was computed. Entries
# here are immutable: a version is a historical record, not a place to fix
# things.
REGISTRY = {policy["version"]: policy for policy in (V1, V2)}


# ------------------------------------------------------------------ hashing

def canonical(policy):
    return json.dumps(policy, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def fingerprint(policy):
    """
    SHA-256 of the whole policy, description and rationales included.

    Hashing the prose as well as the logic is intentional. Two policies that
    decide identically but justify differently are not the same policy: the
    rationale is what an assessor reads, and a quiet edit to it changes what
    the organisation is on record as having meant.
    """
    return hashlib.sha256(canonical(policy).encode("utf-8")).hexdigest()


def identify(policy=None):
    """Version and hash, for the header of a plan or an evidence bundle."""
    policy = policy or DEFAULT
    return {"policy_version": policy["version"],
            "policy_hash": fingerprint(policy)}


# --------------------------------------------------------------- validation

class InvalidPolicy(ValueError):
    """A policy that cannot be trusted to decide anything."""


def validate(policy):
    """
    Reject anything that could decide by accident. Returns the policy.

    Every failure here is a refusal to load, never a warning. A policy with a
    typo'd dimension name would otherwise load cleanly and silently never
    match, and a rule that never matches is indistinguishable from a rule that
    was deleted — except that the file still shows it, so everyone believes it
    is in force.
    """
    if not isinstance(policy, dict):
        raise InvalidPolicy("policy must be an object")

    version = policy.get("version")
    if not isinstance(version, str) or not version.strip():
        raise InvalidPolicy("policy needs a non-empty version string")

    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        raise InvalidPolicy("policy needs a non-empty list of rules")

    seen = set()
    for index, item in enumerate(rules):
        where = f"rule {index}"
        if not isinstance(item, dict):
            raise InvalidPolicy(f"{where} is not an object")

        rule_id = item.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise InvalidPolicy(f"{where} needs a non-empty id")
        if rule_id == FALLTHROUGH_RULE:
            raise InvalidPolicy(
                f"{where} may not be called {FALLTHROUGH_RULE!r}; that name is "
                "reserved for the guard outside the rule list")
        if rule_id in seen:
            raise InvalidPolicy(f"duplicate rule id {rule_id!r}")
        seen.add(rule_id)

        action = item.get("action")
        if action not in ACTIONS:
            # The action set is closed. A new action would change what
            # remediation means, which is a design event, not a config change.
            raise InvalidPolicy(
                f"rule {rule_id!r} has unknown action {action!r}; "
                f"known actions are {', '.join(sorted(ACTIONS))}")

        if not isinstance(item.get("because"), str) or not item["because"].strip():
            raise InvalidPolicy(
                f"rule {rule_id!r} needs a rationale; a rule nobody can "
                "explain cannot be defended when it is questioned")

        when = item.get("when")
        if not isinstance(when, dict):
            raise InvalidPolicy(f"rule {rule_id!r} needs a 'when' object")
        for field, value in when.items():
            if field not in DIMENSIONS:
                raise InvalidPolicy(
                    f"rule {rule_id!r} tests unknown dimension {field!r}; "
                    f"known dimensions are {', '.join(DIMENSIONS)}")
            if value not in VALID[field]:
                raise InvalidPolicy(
                    f"rule {rule_id!r} tests {field}={value!r}, which is not a "
                    f"possible value ({sorted(VALID[field], key=str)})")

    return policy


def load(path):
    """Read and validate a policy from a JSON file."""
    from pathlib import Path
    return validate(json.loads(Path(path).read_text()))


def resolve_or_load(name_or_path):
    """
    A shipped version name, or a path to a policy file.

    Version names win when both could apply: `v1` should mean the v1 everyone
    else means, not a file that happens to sit in the working directory under
    that name.
    """
    from pathlib import Path as _Path

    if name_or_path in REGISTRY:
        return resolve(name_or_path)
    if not _Path(name_or_path).exists():
        raise InvalidPolicy(
            f"{name_or_path!r} is neither a shipped version "
            f"({', '.join(sorted(REGISTRY))}) nor a readable file")
    return load(name_or_path)


def resolve(version):
    """The shipped policy for a version string, for replaying an old plan."""
    if version not in REGISTRY:
        raise InvalidPolicy(
            f"unknown policy version {version!r}; shipped versions are "
            f"{', '.join(sorted(REGISTRY))}. A plan citing a version this "
            "build does not have cannot be replayed here — say so rather "
            "than recomputing it under a different table.")
    return REGISTRY[version]


# ----------------------------------------------------------------- deciding

def matches(when, facts):
    """Every named dimension equals the fact. Omitted dimensions are wildcards."""
    return all(facts[field] == value for field, value in when.items())


def _english(items):
    """'a, b or c' — a list a person reads, not a join artefact."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " or " + items[-1]


def _decide_known(facts, policy):
    """One pass over the rules with every dimension known."""
    for item in policy["rules"]:
        if matches(item["when"], facts):
            return {"action": item["action"], "rule": item["id"],
                    "because": item["because"]}

    # Guard 2, outside the rules: falling off the end is not an error and not
    # a pass. An incomplete policy is cautious, never permissive.
    return {"action": FALLTHROUGH_ACTION, "rule": FALLTHROUGH_RULE,
            "because": "no rule matched; failing closed rather than deciding "
                       "by omission"}


def decide(contribution_class, storage=contribution.WRITABLE, exclusive=False,
           terminal=False, policy=None):
    """
    Resolve one affected artifact to exactly one action, and name the rule.

    Returns {action, rule, because}. The policy's version and hash are not
    repeated per decision — they belong once in the header of whatever
    collects these, and hashing the policy 81 times to say the same thing
    would be waste dressed up as rigour.

    `storage=None` means NOT VERIFIED, which is different from any of the
    three storage values. See the module docstring: the policy is evaluated
    against each possible value, and if they disagree no action is returned.
    An undetermined result has action None and a `possible` map of the
    candidate actions to the rules that would produce them.
    """
    policy = policy or DEFAULT

    # Guard 1, outside the rules: unknown class becomes IRREDUCIBLE before
    # anything gets to look at it.
    facts = {
        "contribution": contribution.normalise(contribution_class),
        "storage": storage,
        "exclusive": exclusive,
        "terminal": terminal,
    }

    if storage is not None:
        return _decide_known(facts, policy)

    # Unverified. Ask the policy what it would say under each possibility.
    candidates = {}
    for possible_storage in contribution.STORAGE:
        outcome = _decide_known(dict(facts, storage=possible_storage), policy)
        candidates.setdefault(outcome["action"], outcome["rule"])

    if len(candidates) == 1:
        # The storage state turns out not to matter here. This is a real
        # answer, not a guess: it holds whatever the bytes are doing.
        action, rule = next(iter(candidates.items()))
        return {"action": action, "rule": rule,
                "because": _decide_known(
                    dict(facts, storage=contribution.WRITABLE),
                    policy)["because"]
                + " (storage unverified, but every possible state gives this "
                  "same answer)"}

    return {
        "action": None,
        "rule": None,
        "possible": dict(sorted(candidates.items())),
        "because": "storage state not verified, and the verdict depends on "
                   "it. Verifying would decide between "
                   + _english(sorted(candidates))
                   + ". Refusing to guess: assuming the artifact survives "
                     "over-claims work, and assuming it is gone reports an "
                     "obligation as already discharged.",
    }


def remediate(contribution_class, storage=contribution.WRITABLE,
              exclusive=False, terminal=False, policy=None):
    """The action alone, for callers that do not need the citation.

    None when the verdict is undetermined. Callers that treat a falsy action
    as "nothing to do" are the exact failure this guards against, so anything
    acting on this must handle None explicitly.
    """
    return decide(contribution_class, storage=storage, exclusive=exclusive,
                  terminal=terminal, policy=policy)["action"]
