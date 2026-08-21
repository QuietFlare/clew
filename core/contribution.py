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

FAIL CLOSED
-----------
Unknown class becomes IRREDUCIBLE. The two error directions are not symmetric:
over-claiming remediation wastes work, under-claiming tells someone their data
is gone when it is not. Only the second one ends up in front of a regulator.
"""

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


def remediate(contribution, storage=WRITABLE, exclusive=False, terminal=False):
    """
    Resolve one affected artifact to exactly one action.

    Every combination must produce a single action. If a combination feels
    ambiguous, one of the two dimensions is modelled wrong.

    `exclusive`  the artifact exists only because of the withdrawn subject.
                 Nothing else needs it, so it goes entirely.
    `terminal`   immutable history - published, or already outside the trust
                 boundary. Terminates remediation, not notification.
    """
    contribution = normalise(contribution)

    # Order matters. Each guard answers a question that makes the later ones
    # irrelevant, so they read top-down as "is there anything left to do?"
    if storage == DESTROYED:
        return ALREADY_GONE

    if terminal:
        return NOTIFY_ONLY

    if exclusive:
        # No other subject depends on it. Nothing to preserve.
        return DESTROY if storage == WRITABLE else QUARANTINE

    if contribution == SEPARABLE:
        # Logically removable - but only if the bytes can actually be changed.
        return PURGE if storage == WRITABLE else REGENERATE

    if contribution == REGENERABLE:
        # Rewriting in place is not required; a fresh artifact is produced.
        return REGENERATE

    return QUARANTINE  # IRREDUCIBLE


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
