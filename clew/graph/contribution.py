"""
Contribution classes, storage states and actions: the vocabulary.

Given B = f(A1 ... An), removing Ai is SEPARABLE when some g gives B' = g(B,
Ai), REGENERABLE when f can be re-run without Ai, and IRREDUCIBLE otherwise.
The axis is invertibility with respect to one input. The enum is closed: a
provider maps its events onto these classes and cannot add one.

Storage mutability is a second, independent dimension. Whether a remediation
is possible and whether it is executable are different questions.

An unknown class becomes IRREDUCIBLE. Over-claiming remediation wastes work;
under-claiming tells someone their data is gone when it is not.

Which combination yields which action belongs to the policy, versioned in
policy.py. The words here must stay stable; the table must be versioned.
"""

from clew.graph.graph import local_workdir

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
REGENERATE = "REGENERATE"          # recompute without the removed source
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
# looks at task["script"] and task["container"], `storage_state` looks for
# the task directory under a root the caller gives. A domain maps its
# events onto the classes above; it does not own the reading of them.
# ---------------------------------------------------------------------------


def storage_state(task, work_root=None):
    """
    Whether the task's artifacts are on disk, or None for not checked. None
    is the absence of an answer: DESTROYED is returned only after looking
    under `work_root` and not finding, never because a path was unrecorded,
    unmounted or from another machine. Those used to read ALREADY_GONE, the
    one error this project must not make. See graph.local_workdir.
    """
    return storage_at(local_workdir(task, work_root))


def storage_at(local):
    """Storage for a resolved task directory, None when there is none to look at."""
    if local is None:
        return None
    return WRITABLE if local.is_dir() else DESTROYED


def classify(graph, task_hash, exclusive, published=None, work_root=None,
             resolved=None):
    """
    Contribution class and storage for one affected task, from pipeline
    evidence: a recorded script and container mean REGENERABLE, otherwise
    IRREDUCIBLE. Publication arrives as an assertion and sets terminal.
    storage is None unless work_root says where to look; pass `resolved`
    from graph.resolve_workdirs to keep its refusals.
    """
    task = graph["tasks"].get(task_hash, {})
    if resolved is not None:
        storage = storage_at(resolved.get(task_hash))
    else:
        storage = storage_state(task, work_root)

    # Name what is missing. A reader of an IRREDUCIBLE verdict needs to know
    # which record to go and find, not that one of two is absent.
    missing = [field for field in ("script", "container")
               if not task.get(field)]
    reproducible = not missing
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
        reason = (f"no {' or '.join(missing)} recorded; "
                  "task cannot be reproduced")

    if storage is None:
        reason += ("; storage not checked (task directory not placed under "
                   "--work-root)" if work_root
                   else "; storage not checked (no --work-root given)")

    return {
        "contribution": klass,
        "storage": storage,
        "exclusive": exclusive,
        "terminal": assertion is not None,
        "reason": reason,
    }
