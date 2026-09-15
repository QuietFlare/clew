"""
The append-only event log, on Postgres. It stores opaque events: type,
subject, body, actor and two timestamps. Providers define what the types
mean.

Two clocks. effective_from is when the fact became true; recorded_at is when
the log heard it. The gap is often the interesting part, and a plan computed
from the facts carries no clock of its own.

Three protections with different jobs. Role grants stop the application: the
writer holds SELECT and INSERT and cannot grant itself more, which no file
can offer. Triggers stop the owner's mistake by refusing UPDATE, DELETE and
TRUNCATE from anyone. The hash chain catches whoever defeats both: each
entry hashes its content and its predecessor, so an edit fails to recompute
and verify() finds it without trusting the table.

Not proved here: truncation of the tail, or a rewrite from a point onward. A
witness outside the owner's control closes that, and the evidence bundle is
that witness.
"""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

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
    # exported bundle verifies whether its bodies arrive parsed or raw , 
    # a trap worth closing once rather than in every consumer.
    if not isinstance(fields["body"], str):
        fields["body"] = canonical(fields["body"])
    preimage = canonical(fields)
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def verify_entries(entries, start_seq=1, start_prev=GENESIS):
    """
    Re-walk a chain in sequence order, recomputing every hash from the raw
    field values. Plain dicts, so a live log and a bundle verify the same
    way. `start_seq` and `start_prev` anchor a window; an unanchored window
    would pass as if it were the whole log. Returns the first failure only,
    which is where the edit happened.
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


def stamp(moment):
    """One ISO-8601 spelling for every timestamp the log writes."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def now():
    return stamp(datetime.now(timezone.utc))


def instant(text):
    """
    An ISO-8601 string as an aware UTC datetime. Timestamps are stored as
    text because the hash covers bytes, but text order is not time order
    across offsets, so every comparison goes through here. A bare date is
    midnight UTC, a missing offset is UTC, and an unparseable value raises.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"not a timestamp: {text!r}")
    spelled = text.strip()
    if spelled.endswith("Z"):
        spelled = spelled[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(spelled)
    except ValueError:
        raise ValueError(f"not an ISO-8601 timestamp: {text!r}") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _date_only(text):
    try:
        date.fromisoformat(text.strip())
    except ValueError:
        return False
    return True


def in_effect(effective_from, as_of):
    """
    Whether a fact effective at `effective_from` counts when asking at `as_of`.

    A date-only as_of names the whole day: "as of 1 May" includes a fact
    stamped 09:00 on 1 May, because the person asking means the day, not
    the stroke of midnight that began it.
    """
    moment = instant(effective_from)
    cutoff = instant(as_of)
    if _date_only(as_of):
        return moment < cutoff + timedelta(days=1)
    return moment <= cutoff


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
-- would fail to recompute, verification broken by a display convention.
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
    Create the table, the guards and the two roles, as the owner. The writer
    gets SELECT and INSERT only and cannot grant itself more, so the
    application cannot edit the log whatever its code or credentials do. The
    owner can still drop a trigger, so owner and application must be
    different identities. That is an operational control, not something this
    code enforces.
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
           effective_from=None):
    """
    Add one event and return it, hash included. `recorded_at` is the server
    clock read in the same transaction; the caller is the wrong party to say
    when the log heard it. `effective_from` defaults to recorded_at, which
    never back-dates, and must be ISO-8601 or it cannot be ordered against
    anything.
    """
    if effective_from is not None:
        instant(effective_from)

    entry = {
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

            cur.execute("SELECT now() AS t")
            entry["recorded_at"] = stamp(cur.fetchone()["t"])
            entry["effective_from"] = effective_from or entry["recorded_at"]

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
    Entries with bodies left as stored text, the form that was hashed.

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
    The hash an entry range beginning after `seq` must chain to: genesis at
    0. A missing entry raises rather than falling back to genesis, which
    would turn a check against absent history into a pass.
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
