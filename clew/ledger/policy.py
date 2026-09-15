"""
The remediation policy: a versioned table, not an if-ladder.

A rule is a match dict, first match wins, an omitted dimension is a
wildcard, and there is no other syntax, so an auditor can read the whole
policy:

    {"id": "R3", "when": {"exclusive": True, "storage": "WRITABLE"},
     "action": "DESTROY", "because": "..."}

The table carries a version and a content hash, every decision names its
rule, and every plan carries the policy it ran under, so a plan from March
replays under March's table. This table defines what the classes mean, so
Clew owns it. Mapping a site's events onto classes is the provider's job and
lives elsewhere.

A dimension passed as None is unverified, not a value: decide() runs once
per possible value and returns no action when they disagree, naming the
candidates instead. Unknown classes normalise to IRREDUCIBLE first, and
falling off the end of the rules yields QUARANTINE. Neither guard fixes the
verdict. A policy mapping IRREDUCIBLE to PURGE loads and runs, and is wrong
in the open with a rule id on it, which is why the table is data.
"""

import hashlib
import json
from itertools import product

from clew.graph import contribution

# The dimensions a rule may test. A rule naming anything else is rejected at
# load time rather than silently never matching.
DIMENSIONS = ("contribution", "storage", "exclusive", "terminal", "mode")
REMOVE, TRACE = "remove", "trace"
MODES = (REMOVE, TRACE)

VALID = {
    "contribution": set(contribution.CLASSES),
    "storage": set(contribution.STORAGE),
    "exclusive": {True, False},
    "terminal": {True, False},
    "mode": set(MODES),
}

TYPES = {"storage": str, "exclusive": bool, "terminal": bool, "mode": str}
VERIFIABLE = ("storage", "exclusive", "terminal", "mode")

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
# back ALREADY_GONE, "nothing to do", which is wrong. Deleting your copy of
# something does not un-publish it. The disclosure obligation survives the
# bytes, and the same holds for material that has left under an agreement:
# our copy being gone does not reach the partner's.
#
# The practical consequence is sharper than it first looks. Because R1 is the
# only rule that can yield ALREADY_GONE, putting it first made EVERY verdict
# depend on the storage state, so under v1 nothing at all is decidable
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
# a different position, ids identify rules, not positions, so two plans on
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

# --------------------------------------------------------------------- v3
#
# WHY v3 EXISTS: v2 gave a corrected subject the same verdict as a removed
# one, purge, which takes the old part out and never puts the new one back.
# R9 adds the mode. Every other cell decides as under v2.

V3 = {
    "version": "v3",
    "description": ("Clew's remediation table. A corrected subject's separable "
                    "part is recomputed, not merely removed."),
    "rules": [r for r in V2["rules"] if r["id"] not in ("R5", "R6", "R7", "R8")] + [
        rule("R9", contribution.REGENERATE,
             "The subject changed rather than left. Its part can be isolated, "
             "so recompute that part and put it back; the rest stands.",
             contribution=contribution.SEPARABLE, mode=TRACE),
    ] + [r for r in V2["rules"] if r["id"] in ("R5", "R6", "R7", "R8")],
}

DEFAULT = V3

# Every policy ever shipped, so a plan citing an old version can be replayed
# under the table that was actually in force when it was computed. Entries
# here are immutable: a version is a historical record, not a place to fix
# things.
REGISTRY = {policy["version"]: policy for policy in (V1, V2, V3)}


# ------------------------------------------------------------------ hashing

def canonical(policy):
    return json.dumps(policy, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def fingerprint(policy):
    """
    SHA-256 of the whole policy, rationales included. Two policies that
    decide alike but justify differently are not the same policy: the
    rationale is what an assessor reads.
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
    Reject anything that could decide by accident; returns the policy. Every
    failure refuses to load. A rule with a mistyped dimension would
    otherwise never match, and a rule that never matches looks in force
    while being absent.
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


POLICY_GROUP = "clew.policies"


def available():
    """
    {version: policy}: the shipped tables, then every table a provider
    registered under the entry-point group. An entry loads to a table dict
    or the path of a JSON file, is validated, and overrides a shipped
    version of the same name.
    """
    from pathlib import Path as _Path
    from clew.contracts.registry import entry_points

    tables = dict(REGISTRY)
    for name, dist, entry in entry_points(POLICY_GROUP):
        try:
            loaded = entry.load()
            table = loaded if isinstance(loaded, dict) else json.loads(_Path(loaded).read_text())
            table = validate(table)
        except (InvalidPolicy, OSError, ValueError, TypeError) as bad:
            raise InvalidPolicy(f"policy {name!r} from {dist}: {bad}")
        if table["version"] != name:
            raise InvalidPolicy(f"policy {name!r} from {dist} calls itself "
                                f"{table['version']!r}; the entry point name and the "
                                "table's version must agree")
        tables[name] = table
    return tables


def resolve_or_load(name_or_path):
    """A version name, shipped or registered, or a path. Names win over files."""
    from pathlib import Path as _Path

    tables = available()
    if name_or_path in tables:
        return tables[name_or_path]
    if not _Path(name_or_path).exists():
        raise InvalidPolicy(
            f"{name_or_path!r} is neither a known version "
            f"({', '.join(sorted(tables))}) nor a readable file")
    return load(name_or_path)


def resolve(version):
    """The policy for a version string, for replaying an old plan."""
    tables = available()
    if version not in tables:
        raise InvalidPolicy(
            f"unknown policy version {version!r}; known versions are "
            f"{', '.join(sorted(tables))}. A plan citing a version this "
            "build does not have cannot be replayed here, say so rather "
            "than recomputing it under a different table.")
    return tables[version]


# ----------------------------------------------------------------- deciding

def matches(when, facts):
    """Every named dimension equals the fact. Omitted dimensions are wildcards."""
    return all(facts[field] == value for field, value in when.items())


def _english(items):
    """'a, b or c', a list a person reads, not a join artefact."""
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
           terminal=False, policy=None, mode=None):
    """
    One action for one artifact, with the rule that chose it: {action, rule,
    because}. None on storage, exclusive, terminal or mode means unverified:
    the policy runs once per possible value, and if they disagree action is
    None with a `possible` map of the candidates. A value outside a
    dimension's set is an error, not a wildcard; only the contribution class
    normalises, to IRREDUCIBLE.
    """
    policy = policy or DEFAULT

    # Guard 1, outside the rules: unknown class becomes IRREDUCIBLE before
    # anything gets to look at it.
    facts = {
        "contribution": contribution.normalise(contribution_class),
        "storage": storage,
        "exclusive": exclusive,
        "terminal": terminal,
        "mode": mode,
    }
    for field in VERIFIABLE:
        value = facts[field]
        # The type check is not pedantry: bool is a subclass of int, so 1
        # would otherwise pass as True.
        if value is not None and (type(value) is not TYPES[field]
                                  or value not in VALID[field]):
            raise ValueError(
                f"{field}={value!r} is not a possible value "
                f"({sorted(VALID[field], key=str)}); pass None if it was "
                "not verified")

    unverified = [f for f in VERIFIABLE if facts[f] is None]
    if not unverified:
        return _decide_known(facts, policy)

    # Unverified. Ask the policy what it would say under each possibility.
    candidates = {}
    first = None
    for combination in product(*(sorted(VALID[f], key=str)
                                 for f in unverified)):
        assumed = dict(facts, **dict(zip(unverified, combination)))
        outcome = _decide_known(assumed, policy)
        candidates.setdefault(outcome["action"], outcome["rule"])
        first = first or outcome

    label = _english(unverified)
    if len(candidates) == 1:
        # The unverified dimensions turn out not to matter here. This is a
        # real answer, not a guess: it holds whatever the facts are.
        action, rule = next(iter(candidates.items()))
        return {"action": action, "rule": rule,
                "because": first["because"]
                + f" ({label} unverified, but every possible state gives "
                  "this same answer)"}

    return {
        "action": None,
        "rule": None,
        "possible": dict(sorted(candidates.items())),
        "because": f"{label} not verified, and the verdict depends on it. "
                   "Verifying would decide between "
                   + _english(sorted(candidates))
                   + ". Refusing to guess: assuming the artifact survives "
                     "over-claims work, and assuming it is gone reports an "
                     "obligation as already discharged.",
    }


def remediate(contribution_class, storage=contribution.WRITABLE,
              exclusive=False, terminal=False, policy=None, mode=None):
    """The action alone, for callers that do not need the citation.

    None when the verdict is undetermined. Callers that treat a falsy action
    as "nothing to do" are the exact failure this guards against, so anything
    acting on this must handle None explicitly.
    """
    return decide(contribution_class, storage=storage, exclusive=exclusive,
                  terminal=terminal, policy=policy, mode=mode)["action"]
