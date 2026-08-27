"""
Clew core — the append-only event log, on Postgres.

THIS FILE KNOWS NOTHING ABOUT BIOLOGY. It stores opaque events: a type, a
subject, a body, an actor, two timestamps. Core never interprets any of them.
Domain adapters define what the types mean; if core ever needed to know, the
boundary would be broken.

WHAT THIS IS FOR
----------------
Clew claims three things and only three. This file is the first of them:

    the log is append-only and unmodified.

Everything downstream — a blast radius, a remediation plan, an evidence
bundle — is a computation over facts. If the facts can be edited after the
fact, none of the rest is worth anything. So the facts get their own store
whose only job is to make revision impossible for the application and
detectable for everyone else.

TWO CLOCKS, DELIBERATELY
------------------------
    effective_from   when the fact became true in the world
    recorded_at      when we learned it

They are usually different and the gap is the interesting part. A withdrawal
signed on the 1st and entered on the 5th was true from the 1st; a release
made on the 3rd was made in good faith and is still a release that must be
disclosed. One timestamp cannot express that, and retrofitting the second one
later means re-interpreting every historical row. So both, from the start.

Note what this does NOT do: it does not put a clock in the computation.
The log timestamps facts. A plan computed from those facts is a pure function
of them, and stays byte-identical on replay. Learning is dated; deciding is not.

THREE LAYERS OF PROTECTION, EACH WITH A DIFFERENT JOB
-----------------------------------------------------
They are not redundant. Each one stops something the next cannot.

1. ROLE GRANTS stop the application.
   The writer role holds SELECT and INSERT. It was never granted UPDATE,
   DELETE or TRUNCATE, and it cannot grant them to itself. This is the reason
   this log is on a server rather than in a local file: enforcement lives
   outside the process that writes, in a database the application does not
   administer. A file-backed store cannot do this — whoever holds the file
   holds everything.

2. TRIGGERS stop the owner's mistake.
   Grants do not constrain the table owner, and the owner is a real person
   with a psql prompt at 6pm. The triggers refuse UPDATE, DELETE and TRUNCATE
   from anyone, owner included. TRUNCATE gets its own statement-level trigger
   because it does not fire row triggers at all — it would otherwise empty the
   whole log silently.

3. THE HASH CHAIN catches whoever defeats both.
   A superuser can disable a trigger and rewrite a row. Every entry hashes its
   own content plus its predecessor's hash, so any such edit fails to
   recompute, and verify() finds it without trusting the table, the triggers,
   or us.

WHAT IS STILL NOT PROVED
------------------------
The chain detects EDITING. It does not detect TRUNCATION OF THE TAIL — a
shorter chain is still self-consistent — nor a FULL REWRITE by someone who
rebuilds every hash from the altered point.

No hash chain solves that alone. What closes it is anchoring the head hash
somewhere the log's owner does not control: a countersigned evidence bundle, a
build log, a timestamping service. Slice 3 does that. Until then the claim is
exactly "the application cannot edit, and anyone else's edit is detectable",
which is what this says rather than something more comfortable.

The tests assert both limits rather than leaving them implied, so nobody
later reads verify()'s ok=True as "nothing was lost".
"""

import hashlib
import json
from datetime import datetime, timezone

# The predecessor of the first entry. Same width as a real hash so the chain
# is uniform and nothing has to special-case "is this the beginning".
GENESIS = "0" * 64

FIELDS = ("seq", "effective_from", "recorded_at", "actor",
          "event_type", "subject", "body", "prev_hash", "hash")

# Appending is serialised on this one advisory lock. Reading the head and
# writing its successor must be atomic or two concurrent appenders both read
# seq N and fork the chain. An advisory lock says that plainly; SERIALIZABLE
# plus a retry loop would achieve the same thing while reading as if
# contention were an accident rather than the normal case.
APPEND_LOCK = 0x0C1E0010


# --------------------------------------------------------------------- pure
# Everything above the driver line. An auditor verifying an exported bundle
# has entries but no database, so the verification logic must not need one.

def canonical(obj):
    """
    The one serialisation used for hashing, everywhere.

    Sorted keys and no incidental whitespace, so two processes that agree on
    the content agree on the bytes. ensure_ascii keeps the preimage pure ASCII,
    which removes any question of what encoding produced a given digest.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def event_hash(entry):
    """
    SHA-256 over every field of the entry, including its predecessor's hash.

    Hashing the whole entry rather than a chosen subset is the point: there is
    no field an editor can change quietly, and no argument later about which
    ones were covered.
    """
    fields = {k: entry[k] for k in FIELDS if k != "hash"}
    # The body is hashed as its canonical TEXT, which is what the database
    # stores. Accepting a structure here and canonicalising it means an
    # exported bundle verifies whether its bodies arrive parsed or raw —
    # a trap worth closing once rather than in every consumer.
    if not isinstance(fields["body"], str):
        fields["body"] = canonical(fields["body"])
    preimage = canonical(fields)
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def verify_entries(entries, start_seq=1, start_prev=GENESIS):
    """
    Re-walk a chain in sequence order, recomputing every hash.

    Takes plain dicts, not rows, so the same function verifies a live log and
    an exported evidence bundle. Trusts nothing but the raw field values:
    not the stored hash, not the triggers, not that the file opened cleanly.
    Anyone can run this, which is the property that matters — an auditor who
    does not trust us can verify without us.

    `start_seq`/`start_prev` anchor a WINDOW of the chain. A bundle covering
    entries 40-60 is not verifiable on its own — it is verifiable against the
    hash the previous bundle ended on. Defaulting them to the genesis pair
    means an unanchored window silently verifies as if it were the whole log,
    so a caller checking a window must supply the anchor it is claiming.

    Returns the FIRST failure, not a list. A chain is broken from its first
    bad link onwards; reporting every later entry as "also wrong" would be
    noise that buries where the edit actually happened.
    """
    expected_prev = start_prev
    expected_seq = start_seq
    count = 0
    last_hash = start_prev

    for entry in entries:
        if entry["seq"] != expected_seq:
            return _broken(entry["seq"], count,
                           f"sequence jumps to {entry['seq']}, expected "
                           f"{expected_seq}: an entry was removed or inserted")

        if entry["prev_hash"] != expected_prev:
            return _broken(entry["seq"], count,
                           "prev_hash does not match the previous entry's "
                           "hash: the chain was cut or re-ordered here")

        if event_hash(entry) != entry["hash"]:
            return _broken(entry["seq"], count,
                           "content does not match its own hash: this entry "
                           "was edited after it was written")

        expected_prev = last_hash = entry["hash"]
        expected_seq += 1
        count += 1

    return {"ok": True, "entries": count, "head": last_hash,
            "broken_at": None, "reason": None}


def _broken(seq, verified, reason):
    return {"ok": False, "entries": verified, "head": None,
            "broken_at": seq, "reason": reason}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- driver
# psycopg is imported inside connect(), not at module scope, so that
# canonical(), event_hash() and verify_entries() stay importable with nothing
# installed. An auditor checking an exported bundle has a JSON file and a
# Python interpreter; requiring them to install a database driver to check our
# arithmetic would undercut the whole "verify without us" claim.

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS events (
    seq            bigint PRIMARY KEY,   -- 1-based, monotonic, no gaps
    effective_from text NOT NULL,        -- when it became true
    recorded_at    text NOT NULL,        -- when we learned it
    actor          text NOT NULL,        -- who asserted it
    event_type     text NOT NULL,        -- opaque to core
    subject        text NOT NULL,        -- opaque to core
    body           text NOT NULL,        -- canonical JSON, opaque to core
    prev_hash      text NOT NULL,
    hash           text NOT NULL UNIQUE
);

-- Timestamps are text, not timestamptz, and that is deliberate. The hash
-- covers bytes. A timestamptz round-trips through the server's own
-- formatting, so '+00:00' could come back as 'Z' and every hash after it
-- would fail to recompute — verification broken by a display convention.
-- ISO-8601 UTC sorts correctly as text, which is the whole reason the format
-- exists, so nothing is lost for querying.

CREATE INDEX IF NOT EXISTS events_subject_idx ON events (subject);
CREATE INDEX IF NOT EXISTS events_type_idx ON events (event_type);
CREATE INDEX IF NOT EXISTS events_effective_idx ON events (effective_from);

CREATE OR REPLACE FUNCTION clew_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'clew: the event log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_no_update ON events;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION clew_append_only();

DROP TRIGGER IF EXISTS events_no_delete ON events;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION clew_append_only();

-- TRUNCATE fires no row triggers at all. Without this one, a single statement
-- empties the log and the two above never run.
DROP TRIGGER IF EXISTS events_no_truncate ON events;
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION clew_append_only();
"""

WRITER_ROLE = "clew_writer"
AUDITOR_ROLE = "clew_auditor"


def connect(dsn, autocommit=True):
    """Open a connection. Rows come back as dicts keyed by column name."""
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=autocommit)


def init(conn, writer=WRITER_ROLE, auditor=AUDITOR_ROLE,
         writer_password=None, auditor_password=None):
    """
    Create the table, the guards, and the two roles. Run as the owner.

    THE GRANTS ARE THE POINT. The writer is given SELECT and INSERT and
    nothing else — not UPDATE, not DELETE, not TRUNCATE — and a role cannot
    grant itself a privilege it does not hold. The application therefore
    cannot edit the log even if its code is wrong, its credentials leak, or
    someone is in a hurry. That is prevention, and it is the one thing a
    file-backed store cannot offer at any price: whoever holds the file holds
    every privilege over it.

    What this does NOT constrain is the owner, who can drop a trigger and
    re-grant anything. Owner and application must therefore be different
    identities, and the owner's credentials should not live in the pipeline.
    Clew cannot enforce that from inside; it is an operational control, and
    saying so is more use than implying we solved it.
    """
    from psycopg import sql

    with conn.cursor() as cur:
        cur.execute(TABLE_DDL)

        cur.execute("SELECT current_schema() AS s, current_database() AS d")
        row = cur.fetchone()
        schema, database = row["s"], row["d"]

        for role, password in ((writer, writer_password),
                               (auditor, auditor_password)):
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(
                    sql.Identifier(role)))
            if password is not None:
                cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)))
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(role)))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)))

        # Start from nothing, then hand back exactly what each role needs.
        cur.execute("REVOKE ALL ON events FROM PUBLIC")
        cur.execute(sql.SQL("REVOKE ALL ON events FROM {}, {}").format(
            sql.Identifier(writer), sql.Identifier(auditor)))
        cur.execute(sql.SQL("GRANT SELECT, INSERT ON events TO {}").format(
            sql.Identifier(writer)))
        cur.execute(sql.SQL("GRANT SELECT ON events TO {}").format(
            sql.Identifier(auditor)))

    return {"schema": schema, "database": database,
            "writer": writer, "auditor": auditor}


def head(conn):
    """The last entry's seq and hash, or the genesis pair on an empty log."""
    with conn.cursor() as cur:
        cur.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1")
        row = cur.fetchone()
    if row is None:
        return {"seq": 0, "hash": GENESIS}
    return {"seq": row["seq"], "hash": row["hash"]}


def append(conn, event_type, subject, body=None, actor="unknown",
           effective_from=None, recorded_at=None):
    """
    Add one event and return it, hash included.

    `effective_from` defaults to `recorded_at` — a fact with no stated
    effective date is treated as effective when we heard it. That default
    never back-dates anything on its own, which is the safe direction, but it
    is still a default: a domain that knows the real date should pass it.
    """
    recorded_at = recorded_at or now()
    entry = {
        "effective_from": effective_from or recorded_at,
        "recorded_at": recorded_at,
        "actor": actor,
        "event_type": event_type,
        "subject": subject,
        "body": canonical(body if body is not None else {}),
    }

    with conn.transaction():
        with conn.cursor() as cur:
            # Serialise appenders. Reading the head and writing its successor
            # is one indivisible step; without this two writers on two hosts
            # both read seq N and the chain forks. The advisory lock is held
            # for the transaction and released with it.
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (APPEND_LOCK,))

            previous = head(conn)
            entry["seq"] = previous["seq"] + 1
            entry["prev_hash"] = previous["hash"]
            entry["hash"] = event_hash(entry)

            cur.execute(
                "INSERT INTO events (seq, effective_from, recorded_at, actor,"
                " event_type, subject, body, prev_hash, hash)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                tuple(entry[k] for k in FIELDS))

    entry["body"] = json.loads(entry["body"])
    return entry


def read(conn, since=0, until=None, event_type=None, subject=None):
    """
    Entries in sequence order, optionally windowed or filtered.

    `since` is exclusive and `until` inclusive, so (since, until] names a
    range the way an evidence bundle wants to: "everything after the last
    bundle, up to this head". Bodies come back parsed.
    """
    sql_text = "SELECT * FROM events WHERE seq > %s"
    params = [since]
    if until is not None:
        sql_text += " AND seq <= %s"
        params.append(until)
    if event_type is not None:
        sql_text += " AND event_type = %s"
        params.append(event_type)
    if subject is not None:
        sql_text += " AND subject = %s"
        params.append(subject)
    sql_text += " ORDER BY seq"

    entries = []
    with conn.cursor() as cur:
        cur.execute(sql_text, params)
        for row in cur:
            entry = {k: row[k] for k in FIELDS}
            entry["body"] = json.loads(entry["body"])
            entries.append(entry)
    return entries


def raw(conn, since=0, until=None):
    """
    Entries with bodies left as stored text — the form that was hashed.

    verify() and the evidence bundle both want this. read() is for humans and
    for code that wants structures; raw() is for arithmetic.
    """
    sql_text = "SELECT * FROM events WHERE seq > %s"
    params = [since]
    if until is not None:
        sql_text += " AND seq <= %s"
        params.append(until)
    sql_text += " ORDER BY seq"

    with conn.cursor() as cur:
        cur.execute(sql_text, params)
        return [{k: row[k] for k in FIELDS} for row in cur]


def anchor(conn, seq):
    """
    The hash an entry range beginning after `seq` must chain back to.

    Genesis when seq is 0. Raises when the named entry does not exist, rather
    than falling back to genesis: a missing anchor means the caller is
    verifying against a history that is not there, and quietly treating that
    as "start of log" would turn a real problem into a pass.
    """
    if seq == 0:
        return GENESIS
    with conn.cursor() as cur:
        cur.execute("SELECT hash FROM events WHERE seq = %s", (seq,))
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"no entry at seq {seq} to anchor against")
    return row["hash"]


def verify(conn, since=0, until=None):
    """Re-walk the stored chain, recomputing every hash. See verify_entries."""
    return verify_entries(raw(conn, since=since, until=until),
                          start_seq=since + 1, start_prev=anchor(conn, since))
