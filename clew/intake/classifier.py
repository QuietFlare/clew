"""
The classifier behind triage: one closed question in, one answer out.

An answer is {choice, confidence, probabilities, model}. `jev` asks
TypeSafe's System One endpoint over the standard library. `by_name` needs
no key and no network, and picks an option only when the notice names it.
An answer made elsewhere, by a pipeline plugin for example, goes through
`checked` like a live one.

Nothing here decides what happens to a notice. That is the settings' job,
in triage, so swapping the classifier changes no rule.
"""

import json
import re
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
KEY_VARIABLE = "TYPESAFE_API_KEY"

# The one question a triage request carries, and the option that means
# the notice concerns nothing in this graph.
QUESTION = "trigger"
NONE = "none"

# Names that follow the newest build. A bar adopted against one build says
# nothing about the next, so neither a request nor an answer may carry one.
ALIASES = ("jev-latest", "jev-preview")

# The two statuses the service documents as worth a retry.
RETRYABLE = (429, 529)


class Refused(ValueError):
    """An answer that cannot be recorded as given."""


def jev(request, key, endpoint=ENDPOINT, timeout=30, attempts=3):
    """The service's reply to one request, as parsed JSON."""
    call = urllib.request.Request(
        endpoint, data=json.dumps(request).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    failure = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.25 * 2 ** (attempt - 1))
        try:
            with urllib.request.urlopen(call, timeout=timeout) as reply:
                return json.load(reply)
        except urllib.error.HTTPError as bad:
            detail = bad.read().decode("utf-8", "replace")
            if bad.code not in RETRYABLE:
                raise Refused(f"the classifier answered HTTP {bad.code}: {detail}")
            failure = f"HTTP {bad.code}"
        except (urllib.error.URLError, TimeoutError) as bad:
            failure = str(getattr(bad, "reason", bad))
    raise Refused(f"no answer from the classifier after {attempts} attempts: {failure}")


def checked(document, criteria, model):
    """
    The answer inside a reply, or Refused. Takes the service's whole reply
    or the answer alone with a `model` beside it. The choice must be one of
    the options that were offered and the model must be the pinned one the
    settings name, whoever asked.
    """
    if not isinstance(document, dict):
        raise Refused("the answer is not a JSON object")
    answer = document.get("answers", {}).get(QUESTION) if "answers" in document else document
    if not isinstance(answer, dict):
        raise Refused(f"the reply carries no answer to {QUESTION!r}")

    answered_by = document.get("model") or answer.get("model")
    if not answered_by:
        raise Refused("the answer names no model, so it cannot be told "
                      "which build gave it")
    if answered_by in ALIASES:
        raise Refused(f"the answer names the alias {answered_by!r}; "
                      "it must name the build that answered")
    if answered_by != model:
        raise Refused(f"answered by {answered_by}, and the settings name {model}")

    choice = answer.get("choice")
    if choice not in criteria:
        raise Refused(f"the choice {choice!r} is not one of the options offered")

    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) \
            or not 0 <= confidence <= 1:
        raise Refused(f"confidence {confidence!r} is not a number from 0 to 1")

    probabilities = answer.get("probabilities") or {}
    strangers = sorted(set(probabilities) - set(criteria))
    if strangers:
        raise Refused("probabilities name options that were not offered: "
                      + ", ".join(strangers))

    return {"choice": choice, "confidence": confidence,
            "probabilities": probabilities, "model": answered_by}


def names(notice, word, whole=False):
    """
    Whether the notice contains the word standing alone, in any case. With
    `whole`, a longer hyphenated name does not count, so bwa is not found
    in bwa-mem2.
    """
    before = r"(?<![A-Za-z0-9])" + (r"(?<![A-Za-z0-9]-)" if whole else "")
    after = r"(?!-?[A-Za-z0-9])" if whole else r"(?![A-Za-z0-9])"
    return re.search(before + re.escape(word) + after, notice, re.IGNORECASE) is not None


def by_name(notice, words):
    """
    The option whose word the notice contains, standing alone, when exactly
    one does. `words` is {option: word}. No confidence comes back: a name
    is there or it is not, and its absence shows nothing.
    """
    named = sorted(option for option, word in words.items() if names(notice, word, whole=True))
    return {"choice": named[0] if len(named) == 1 else NONE,
            "confidence": None, "probabilities": None, "model": None,
            "named": named}
