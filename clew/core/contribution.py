"""
Clew core — contribution class and remediation.

THIS FILE KNOWS NOTHING ABOUT BIOLOGY. It defines what removability means and
what follows from it. Domain adapters decide which of their events map to
which class; they do not get to invent new ones.

THE CLASSES
-----------
Given B = f(A1 ... An), remove Ai:

    SEPARABLE     There is an efficient g where B' = g(B, Ai).
                  Invertible IN THE OUTPUT - subtract the contribution
                  without re-running f.

    REGENERABLE   No such g, but f is available and re-runnable, so
                  B' = f(A1 ... without Ai).
                  Invertible VIA RE-EXECUTION.

    IRREDUCIBLE   Neither.

The axis is invertibility of a derivation with respect to one input. It is not
ring theory - do not claim an algebraic pedigree for it.

The enum is CLOSED. If a domain could add a fourth class, core would not know
how to traverse it and determinism would be lost.

STORAGE MUTABILITY is a second, orthogonal dimension. A contribution can be
SEPARABLE while the artifact is physically unwritable. Whether a remediation
is POSSIBLE and whether it is EXECUTABLE are different questions, and the
answer is the join of the two.

WHAT LIVES HERE, AND WHAT DOES NOT
----------------------------------
This file owns the VOCABULARY: the classes, the storage states, the actions,
and the fail-closed normalisation. It does not decide anything.

Deciding — which combination of dimensions yields which action — lives in
core/policy.py, as a versioned table with a content hash. The split is not
tidiness. The words have to be stable for Clew to mean anything, while the
table has to be versioned so a plan from March can be replayed under the
table that was in force in March. Stable and versioned are different
requirements, so they are different files.

FAIL CLOSED
-----------
Unknown class becomes IRREDUCIBLE. The two error directions are not symmetric:
over-claiming remediation wastes work, under-claiming tells someone their data
is gone when it is not. Only the second one ends up in front of a regulator.
"""

from pathlib import Path

# --- contribution class -----------------------------------------------------

SEPARABLE = "SEPARABLE"
REGENERABLE = "REGENERABLE"
IRREDUCIBLE = "IRREDUCIBLE"

CLASSES = (SEPARABLE, REGENERABLE, IRREDUCIBLE)

# --- storage mutability -----------------------------------------------------

WRITABLE = "WRITABLE"
WORM = "WORM"
DESTROYED = "DESTROYED"

STORAGE = (WRITABLE, WORM, DESTROYED)

# --- remediation actions ----------------------------------------------------

PURGE = "PURGE"                    # remove the contribution, artifact survives
REGENERATE = "REGENERATE"          # recompute without the withdrawn source
QUARANTINE = "QUARANTINE"          # cannot remediate; block further use
DESTROY = "DESTROY"                # the artifact exists only because of this
                                   # subject; remove it entirely
NOTIFY_ONLY = "NOTIFY_ONLY"        # immutable history; record, do not act
ALREADY_GONE = "ALREADY_GONE"      # nothing left to remediate


def normalise(contribution):
    """Unknown or unrecognised class fails closed to IRREDUCIBLE."""
    return contribution if contribution in CLASSES else IRREDUCIBLE


def explain(action):
    """One line per action, for humans reading a remediation plan."""
    return {
        PURGE: "remove this subject's contribution; artifact survives",
        REGENERATE: "recompute from the remaining sources; replaces the artifact",
        QUARANTINE: "cannot be remediated; block further use",
        DESTROY: "exists only because of this subject; remove entirely",
        NOTIFY_ONLY: "immutable history; record the fact, take no action",
        ALREADY_GONE: "no longer exists; nothing to do",
    }.get(action, "unknown action")


# ---------------------------------------------------------------------------
# Reading a class off a graph. Both read schema fields only: `classify`
# looks at task["script"] and task["container"], `storage_state` joins the
# last two path components onto a root the caller gives. A domain maps its
# events onto the classes above; it does not own the reading of them.
# ---------------------------------------------------------------------------


def storage_state(workdir, work_root=None):
    """
    Whether the task's artifacts are still on disk — or None for "not checked".

    NONE IS NOT A THIRD OUTCOME, IT IS THE ABSENCE OF ONE. Storage is a live
    property of the world, and the person asking Clew a question is often not
    standing where the pipeline ran: a different host, a CI runner, a laptop
    reading a graph someone emailed them. Guessing there is not conservative
    in either direction, so this refuses.

    In particular DESTROYED is now only ever returned after actually looking
    and not finding. It used to be returned whenever `is_dir()` was false,
    which fired identically when the path was never recorded, when the volume
    was not mounted, when the graph came from another machine, and when the
    fixtures were anonymised for publication. All of those became
    ALREADY_GONE — "no longer exists; nothing to do" — which is the one error
    direction this project exists not to make. A false negative that silences
    an obligation is worth more care than a false positive that wastes work.

    `work_root` is the caller saying where to look. The recorded path is from
    whichever machine ran the pipeline, so only its last two components — the
    two-character prefix and the full task hash, which is how the engine lays
    out a work directory — are joined onto the root given here. That makes a
    graph portable between hosts without pretending the recorded absolute
    path means anything locally.
    """
    if not workdir or not work_root:
        return None
    parts = Path(workdir).parts
    if len(parts) < 2:
        return None
    local = Path(work_root, *parts[-2:])
    return "WRITABLE" if local.is_dir() else "DESTROYED"


def classify(graph, task_hash, exclusive, published=None, work_root=None):
    """
    Contribution class and storage for one affected task, from pipeline
    evidence alone: a task whose script and container were recorded can be
    re-executed (REGENERABLE); one without fails closed to IRREDUCIBLE.
    Publication arrives as an external assertion and sets `terminal`.

    `storage` is None unless `work_root` says where to look. See storage_state.
    """
    task = graph["tasks"].get(task_hash, {})
    storage = storage_state(task.get("workdir", ""), work_root)

    reproducible = bool(task.get("script")) and bool(task.get("container"))
    klass = "REGENERABLE" if reproducible else "IRREDUCIBLE"

    assertion = (published or {}).get(task_hash)
    if assertion:
        reason = (
            f"published ({assertion.get('what', 'unspecified')}), asserted by "
            f"{assertion.get('asserted_by', '?')} on {assertion.get('date', '?')}"
        )
    elif reproducible:
        reason = "script and container recorded; task can be re-executed"
    else:
        reason = "script or container missing; task cannot be reproduced"

    if storage is None:
        reason += "; storage not checked (no --work-root given)"

    return {
        "contribution": klass,
        "storage": storage,
        "exclusive": exclusive,
        "terminal": assertion is not None,
        "reason": reason,
    }
