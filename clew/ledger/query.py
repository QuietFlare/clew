"""
The read surface an auditor's questions land on. Subjects, triggers and fact
types are opaque strings.

The dashboard and the MCP server both answer through here, so they cannot
drift apart. Every answer carries its citations: log sequence numbers, entry
hashes, rule ids and bundle hashes. answer() requires them.

No query resolves to "compliant" or "no further action". Each is a statement
about what the log holds and what the core computed from it. Every result
also says what it does not cover, because silence reads as completeness.
"""

import json

from clew.graph import contribution as contribution_module
from clew.ledger import policy as policy_module
from clew.ledger.eventlog import in_effect, instant

# Fact types core does recognise, because Clew itself writes them. Everything
# else in a log is the customer's vocabulary and stays opaque.
POLICY_ADOPTED = "PolicyAdopted"
BUNDLE_SEALED = "BundleSealed"
GATE_CHECKED = "GateChecked"


def body_of(entry):
    """
    An entry's body as a structure, whether it arrived parsed (a live log)
    or as canonical text (a bundle, where the text is what was hashed).
    """
    body = entry.get("body")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return body or {}


def answer(question, result, citations, coverage=None):
    """
    The only way to return anything from this module.

    Citations are required and must be non-empty for any answer that asserts
    something. An answer with nothing to check is an opinion, and this module
    does not deal in those.
    """
    if not citations:
        raise ValueError(
            f"refusing to answer {question!r} with no citations: an answer "
            "nobody can check independently is worse than no answer, because "
            "it looks the same as one they can")
    return {
        "question": question,
        "result": result,
        "citations": citations,
        "coverage": coverage or [],
    }


def cite_entry(entry):
    """A log entry, in the form a reader uses to find it themselves."""
    return {
        "kind": "log_entry",
        "seq": entry["seq"],
        "hash": entry["hash"],
        "event_type": entry["event_type"],
        "subject": entry["subject"],
        "effective_from": entry["effective_from"],
        "recorded_at": entry["recorded_at"],
        "actor": entry["actor"],
    }


def cite_rule(policy_document, rule_id):
    for rule in policy_module.upgrade(policy_document)["rules"]:
        if rule["id"] == rule_id:
            return {"kind": "policy_rule", "rule": rule_id,
                    "policy_version": policy_document["version"],
                    "policy_hash": policy_module.fingerprint(policy_document),
                    "action": rule["action"], "reason": rule["reason"]}
    return {"kind": "policy_rule", "rule": rule_id,
            "policy_version": policy_document["version"],
            "policy_hash": policy_module.fingerprint(policy_document),
            "note": "no rule with this id is in the cited policy"}


# --------------------------------------------------------------- the queries

def subject_history(entries, subject):
    """
    Everything the log holds about one subject, ordered by effective_from:
    the question is what happened and when, not what was typed and when.
    Both timestamps travel with each entry.
    """
    matching = sorted((e for e in entries if e["subject"] == subject),
                      key=lambda e: (instant(e["effective_from"]), e["seq"]))
    if not matching:
        # An empty history is a real answer and must not look like a clean
        # one. It cites the log head so a reader can see what was searched.
        return {
            "question": f"what does the log record about {subject!r}?",
            "result": {"subject": subject, "facts": [], "known": False},
            "citations": [],
            "coverage": [
                f"the log holds no fact naming {subject!r}. That is not the "
                "same as nothing having happened: it may be recorded under a "
                "different identifier, or never recorded at all.",
            ],
        }

    facts = [{
        "seq": e["seq"], "event_type": e["event_type"],
        "effective_from": e["effective_from"], "recorded_at": e["recorded_at"],
        "actor": e["actor"], "body": body_of(e), "hash": e["hash"],
    } for e in matching]

    return answer(
        f"what does the log record about {subject!r}?",
        {"subject": subject, "facts": facts, "known": True},
        [cite_entry(e) for e in matching],
        coverage=[
            "these are the facts someone recorded. Facts never recorded "
            "cannot appear here, and their absence is not evidence.",
            "effective_from is when a decision was made; recorded_at is when "
            "it reached the log. Where they differ, both matter.",
        ])


def policy_history(entries):
    """Which remediation table was adopted, when, and by whom."""
    adoptions = sorted(
        (e for e in entries if e["event_type"] == POLICY_ADOPTED),
        key=lambda e: (instant(e["effective_from"]), e["seq"]))
    if not adoptions:
        return {
            "question": "which policy versions were adopted, and when?",
            "result": {"adoptions": []},
            "citations": [],
            "coverage": [
                "no policy adoption was ever recorded in this log. Plans may "
                "still cite a version and hash, but nothing here says who "
                "adopted it or from when it was meant to apply.",
            ],
        }

    return answer(
        "which policy versions were adopted, and when?",
        {"adoptions": [{
            "version": e["subject"],
            "policy_hash": body_of(e).get("policy_hash"),
            "effective_from": e["effective_from"],
            "recorded_at": e["recorded_at"],
            "actor": e["actor"],
        } for e in adoptions]},
        [cite_entry(e) for e in adoptions],
        coverage=[
            "adoption is an assertion by the named actor, like any other "
            "fact in this log. Clew records that it was claimed, not that it "
            "was authorised.",
        ])


def policy_in_force(entries, as_of):
    """
    The table in force on a date as the log records it, which is what an
    assessor asks. A plan's own header stays authoritative for that plan.
    """
    adoptions = [e for e in entries
                 if e["event_type"] == POLICY_ADOPTED
                 and in_effect(e["effective_from"], as_of)]
    if not adoptions:
        return {
            "question": f"which policy was in force on {as_of}?",
            "result": {"as_of": as_of, "version": None},
            "citations": [],
            "coverage": [
                f"no policy adoption effective on or before {as_of} is "
                "recorded. The log does not say what was in force then.",
            ],
        }

    latest = max(adoptions,
                 key=lambda e: (instant(e["effective_from"]), e["seq"]))
    return answer(
        f"which policy was in force on {as_of}?",
        {"as_of": as_of, "version": latest["subject"],
         "policy_hash": body_of(latest).get("policy_hash"),
         "adopted_effective_from": latest["effective_from"],
         "adopted_by": latest["actor"],
         "policy": body_of(latest).get("policy")},
        [cite_entry(latest)],
        coverage=[
            "the table itself is carried in the adoption event, so this "
            "answer does not depend on the running build still shipping that "
            "version.",
        ])


def verdict(plan, policy_document, task):
    """
    One line of a remediation plan, with everything behind it.

    This is the "why was this flagged?" question, and the whole architecture
    exists so the answer is a rule id, a rationale and a derivation chain
    rather than a summary.
    """
    item = next((i for i in plan.get("plan", []) if i["task"] == task), None)
    if item is None:
        return {
            "question": f"what did the plan decide about {task!r}?",
            "result": {"task": task, "in_plan": False},
            "citations": [],
            "coverage": [
                f"{task!r} is not in this plan. That means it was not in the "
                "blast radius of this trigger, not that it is unaffected by "
                "anything.",
            ],
        }

    citations = [{
        "kind": "plan_item",
        "task": task,
        "trigger": plan.get("trigger"),
        "policy_version": plan.get("policy_version"),
        "policy_hash": plan.get("policy_hash"),
    }]
    if item.get("rule"):
        citations.append(cite_rule(policy_document, item["rule"]))

    coverage = [
        "the verdict follows from the four facts below under the cited "
        "policy. Whether those facts were true is not something Clew checked.",
    ]
    if not item.get("action"):
        coverage.append(
            "this item has NO verdict: storage was not verified and the "
            "answer depends on it. It is unanswered, not clean.")

    return answer(
        f"what did the plan decide about {task!r}?",
        {
            "task": task,
            "in_plan": True,
            "process": item.get("process"),
            "action": item.get("action"),
            "possible": item.get("possible"),
            "rule": item.get("rule"),
            "reason": item.get("reason", item.get("because")),
            "facts": {"contribution": item.get("contribution"),
                      **policy_module.plan_item_facts(item)},
            "evidence_path": item.get("evidence_path"),
            "published_copies": item.get("published_copies"),
            "explanation": contribution_module.explain(item["action"])
                           if item.get("action") else None,
        },
        citations, coverage)


def plan_summary(plan):
    """Counts by action, and what is unanswered."""
    items = plan.get("plan", [])
    counts = {}
    for item in items:
        counts[item.get("action") or policy_module.UNDETERMINED] = counts.get(
            item.get("action") or policy_module.UNDETERMINED, 0) + 1

    undetermined = counts.get(policy_module.UNDETERMINED, 0)
    coverage = list(plan.get("caveats", []))
    if undetermined:
        coverage.append(
            f"{undetermined} of {len(items)} items have no verdict. They are "
            "unanswered, not clean.")

    return answer(
        f"what does the plan for {plan.get('trigger')!r} say?",
        {"trigger": plan.get("trigger"),
         "tasks_total": plan.get("tasks_total"),
         "tasks_affected": plan.get("tasks_affected"),
         "entry_tasks": plan.get("entry_tasks"),
         "actions": dict(sorted(counts.items()))},
        [{"kind": "plan",
          "trigger": plan.get("trigger"),
          "policy_version": plan.get("policy_version"),
          "policy_hash": plan.get("policy_hash")}],
        coverage)


def unaffected(plan, task):
    """
    The negative question: was this task touched. A task absent from a plan
    is outside that trigger's radius over that graph, and nothing more; the
    answer says what was searched.
    """
    listed = any(i["task"] == task for i in plan.get("plan", []))
    return answer(
        f"was {task!r} affected by {plan.get('trigger')!r}?",
        {"task": task, "affected": listed,
         "trigger": plan.get("trigger"),
         "tasks_total": plan.get("tasks_total"),
         "tasks_affected": plan.get("tasks_affected")},
        [{"kind": "plan",
          "trigger": plan.get("trigger"),
          "policy_version": plan.get("policy_version"),
          "policy_hash": plan.get("policy_hash"),
          "entry_tasks": plan.get("entry_tasks")}],
        coverage=[
            "scoped to this trigger and the graph it was computed over. "
            "A task outside this blast radius may still be reached by a "
            "different trigger.",
            "the graph covers instrumented work only. Anything the lineage "
            "extractor never saw cannot appear in either answer.",
        ])
