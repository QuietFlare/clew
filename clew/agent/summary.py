"""
A model's reading of a plan Clew computed, in plain words.

The model is given the plan as data and asked to say what it reports and
what it cannot tell. It is told not to judge whether a difference matters
and not to add anything the plan does not contain. The verdicts stay what
the command computed; this is a reading of them, labelled as such.

Credentials and the endpoint come from the same variables the agent uses,
so a Codespace going through a proxy sends this call the same way.
"""

import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://api.anthropic.com"
MODEL_VARIABLE = "CLEW_EXPLAIN_MODEL"
MODEL = "claude-opus-5-5"
MOST_PLAN = 60000      # characters of plan the model is shown
MOST_ANSWER = 2000     # tokens for the answer and the thinking before it

INSTRUCTIONS = (
    "You are given a plan that Clew computed from a workflow's lineage record. "
    "Say in plain English what the plan reports: which tasks it names, the causes "
    "it read from the record, the counts, and the limits it states. Rules: describe "
    "only what the plan contains and add nothing; do not judge whether a difference "
    "matters, what caused it beyond what the plan says, or what anyone should do; "
    "say what the plan cannot tell when its limits say so. At most 150 words, no "
    "lists, no headings.")


class Refused(RuntimeError):
    """No reading could be given, and why."""


def credentials(env=None):
    """(base url, headers) from the environment, or Refused when no credential is set."""
    env = os.environ if env is None else env
    headers = {"anthropic-version": "2023-06-01", "content-type": "application/json",
               "user-agent": "clew-lineage"}
    if env.get("ANTHROPIC_API_KEY"):
        headers["x-api-key"] = env["ANTHROPIC_API_KEY"]
    elif env.get("ANTHROPIC_AUTH_TOKEN"):
        headers["authorization"] = f"Bearer {env['ANTHROPIC_AUTH_TOKEN']}"
    else:
        raise Refused("no model credential is set: ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN "
                      "with ANTHROPIC_BASE_URL")
    return (env.get("ANTHROPIC_BASE_URL") or ENDPOINT).rstrip("/"), headers


def model(env=None):
    env = os.environ if env is None else env
    return env.get(MODEL_VARIABLE) or MODEL


def explain(kind, plan, env=None, timeout=90):
    """The model's reading of `plan`, a dict the page laid out, as {text, model}."""
    base, headers = credentials(env)
    shown = json.dumps(plan, indent=1)[:MOST_PLAN]
    # Low effort: a reading, not a problem to solve, and the ceiling must
    # leave room for the words after whatever thinking the model spends.
    body = {"model": model(env), "max_tokens": MOST_ANSWER, "system": INSTRUCTIONS,
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": f"A {kind} plan:\n\n{shown}"}]}
    call = urllib.request.Request(f"{base}/v1/messages", data=json.dumps(body).encode("utf-8"),
                                  headers=headers, method="POST")
    try:
        with urllib.request.urlopen(call, timeout=timeout) as reply:
            answer = json.load(reply)
    except urllib.error.HTTPError as bad:
        detail = bad.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise Refused(f"the model answered HTTP {bad.code}: {detail[:300]}")
    except (urllib.error.URLError, TimeoutError, OSError) as bad:
        raise Refused(f"no answer from the model: {getattr(bad, 'reason', bad)}")
    text = " ".join(block.get("text", "") for block in answer.get("content", [])
                    if block.get("type") == "text").strip()
    if not text:
        raise Refused("the model answered with no text")
    return {"text": text, "model": answer.get("model") or model(env)}
