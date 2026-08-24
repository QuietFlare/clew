"""
The log's storage behaviour, against a real Postgres.

These tests are the reason the log moved off a local file. They assert the
three layers separately, because each stops something the next cannot:

  1. GRANTS stop the application. The writer role holds SELECT and INSERT and
     was never granted UPDATE, DELETE or TRUNCATE. The tests below check that
     the writer is refused with a PRIVILEGE error, not a trigger error — if
     the trigger fired first, the grants would be untested and a future
     "let's simplify the DDL" would remove the real protection unnoticed.

  2. TRIGGERS stop the owner's mistake. Grants do not constrain the owner, who
     is a person with a psql prompt. The owner gets the trigger.

  3. THE HASH CHAIN catches whoever defeats both.

Skipped, loudly, when no server is configured. Set CLEW_TEST_DSN to a
connection string for a database whose owner you are:

    CLEW_TEST_DSN=postgresql://postgres:pw@localhost:5432/clew \
        python3 -m unittest discover -s tests
"""

import os
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import eventlog as el

ADMIN_DSN = os.environ.get("CLEW_TEST_DSN")
T0 = "2026-01-01T00:00:00+00:00"

try:
    import psycopg
    from psycopg import errors
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    DRIVER = True
except ImportError:
    DRIVER = False

needs_postgres = unittest.skipUnless(
    DRIVER and ADMIN_DSN,
    "needs psycopg and CLEW_TEST_DSN (see this module's docstring)")


def as_role(dsn, user, password):
    """The same server and database, reached as a different identity."""
    return make_conninfo(**dict(conninfo_to_dict(dsn),
                                user=user, password=password))


@needs_postgres
class LogTestCase(unittest.TestCase):
    """A fresh table and fresh grants for every test."""

    WRITER_PW = "test-writer-pw"
    AUDITOR_PW = "test-auditor-pw"

    # Roles are cluster-global, not per-database. Using the real role names
    # here would mean running the test suite RESETS the passwords of the
    # production writer and auditor — on any cluster that happens to host
    # both. Test roles get test names.
    WRITER_ROLE = "clew_test_writer"
    AUDITOR_ROLE = "clew_test_auditor"

    @classmethod
    def setUpClass(cls):
        cls.owner = el.connect(ADMIN_DSN)
        cls.writer_dsn = as_role(ADMIN_DSN, cls.WRITER_ROLE, cls.WRITER_PW)
        cls.auditor_dsn = as_role(ADMIN_DSN, cls.AUDITOR_ROLE, cls.AUDITOR_PW)

    @classmethod
    def tearDownClass(cls):
        cls.owner.close()

    def setUp(self):
        with self.owner.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS events")
        el.init(self.owner,
                writer=self.WRITER_ROLE, auditor=self.AUDITOR_ROLE,
                writer_password=self.WRITER_PW,
                auditor_password=self.AUDITOR_PW)
        self.writer = el.connect(self.writer_dsn)
        self.addCleanup(self.writer.close)

    def seed(self, n=3, conn=None):
        conn = conn or self.writer
        for i in range(1, n + 1):
            el.append(conn, event_type="Thing", subject=f"s{i}",
                      body={"i": i}, actor="tester",
                      effective_from=T0, recorded_at=T0)


class TestRolePrivileges(LogTestCase):
    """Layer 1. The application cannot edit, and cannot grant itself the right."""

    def test_writer_can_append_and_read(self):
        self.seed(2)
        self.assertEqual(len(el.read(self.writer)), 2)

    def test_writer_is_refused_update_by_privilege_not_by_trigger(self):
        self.seed(1)
        with self.assertRaises(errors.InsufficientPrivilege):
            with self.writer.cursor() as cur:
                cur.execute("UPDATE events SET actor = 'x' WHERE seq = 1")

    def test_writer_is_refused_delete_by_privilege(self):
        self.seed(1)
        with self.assertRaises(errors.InsufficientPrivilege):
            with self.writer.cursor() as cur:
                cur.execute("DELETE FROM events WHERE seq = 1")

    def test_writer_is_refused_truncate_by_privilege(self):
        # TRUNCATE fires no row triggers. If the grant were missing, the log
        # would empty in one statement and layer 2 would never run.
        self.seed(1)
        with self.assertRaises(errors.InsufficientPrivilege):
            with self.writer.cursor() as cur:
                cur.execute("TRUNCATE events")

    def test_writer_cannot_grant_itself_more(self):
        # Postgres does not ERROR on a grant by a role without grant option —
        # it warns and does nothing. So assert the outcome, not the exception:
        # after trying, the writer still cannot update. Testing for a raised
        # error here would have passed for the wrong reason on some versions
        # and silently stopped testing anything on others.
        self.seed(1)
        with self.writer.cursor() as cur:
            cur.execute(f"GRANT UPDATE ON events TO {self.WRITER_ROLE}")
        with self.assertRaises(errors.InsufficientPrivilege):
            with self.writer.cursor() as cur:
                cur.execute("UPDATE events SET actor = 'x' WHERE seq = 1")

    def test_writer_cannot_drop_the_triggers(self):
        with self.assertRaises(psycopg.Error):
            with self.writer.cursor() as cur:
                cur.execute("DROP TRIGGER events_no_update ON events")

    def test_auditor_can_read_but_not_write(self):
        self.seed(2)
        auditor = el.connect(self.auditor_dsn)
        self.addCleanup(auditor.close)
        self.assertTrue(el.verify(auditor)["ok"])
        with self.assertRaises(errors.InsufficientPrivilege):
            with auditor.cursor() as cur:
                cur.execute("INSERT INTO events (seq, effective_from,"
                            " recorded_at, actor, event_type, subject, body,"
                            " prev_hash, hash) VALUES (99, 'x', 'x', 'x',"
                            " 'x', 'x', '{}', 'x', 'x')")


class TestOwnerGuards(LogTestCase):
    """Layer 2. Grants do not constrain the owner; the triggers do."""

    def assertBlockedByTrigger(self, statement):
        with self.assertRaises(errors.RaiseException) as caught:
            with self.owner.cursor() as cur:
                cur.execute(statement)
        self.assertIn("append-only", str(caught.exception))

    def test_owner_update_hits_the_trigger(self):
        self.seed(1)
        self.assertBlockedByTrigger(
            "UPDATE events SET actor = 'x' WHERE seq = 1")

    def test_owner_delete_hits_the_trigger(self):
        self.seed(1)
        self.assertBlockedByTrigger("DELETE FROM events WHERE seq = 1")

    def test_owner_truncate_hits_the_statement_trigger(self):
        self.seed(1)
        self.assertBlockedByTrigger("TRUNCATE events")


class TestTamperDetection(LogTestCase):
    """Layer 3. The owner drops the guards; the chain still tells."""

    def unlock(self):
        with self.owner.cursor() as cur:
            cur.execute("DROP TRIGGER events_no_update ON events")
            cur.execute("DROP TRIGGER events_no_delete ON events")
            cur.execute("DROP TRIGGER events_no_truncate ON events")

    def test_edit_after_dropping_the_triggers_is_caught(self):
        self.seed(3)
        self.unlock()
        with self.owner.cursor() as cur:
            cur.execute("UPDATE events SET actor = 'someone-else' WHERE seq = 2")
        result = el.verify(self.owner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], 2)
        self.assertIn("edited", result["reason"])

    def test_deletion_after_dropping_the_triggers_is_caught(self):
        self.seed(4)
        self.unlock()
        with self.owner.cursor() as cur:
            cur.execute("DELETE FROM events WHERE seq = 2")
        result = el.verify(self.owner)
        self.assertEqual(result["broken_at"], 3)
        self.assertIn("removed", result["reason"])

    def test_truncated_tail_is_not_caught_by_the_chain_alone(self):
        # Honest limit, asserted so nobody reads ok=True as "nothing was lost".
        self.seed(4)
        remembered = el.verify(self.owner)["head"]
        self.unlock()
        with self.owner.cursor() as cur:
            cur.execute("DELETE FROM events WHERE seq = 4")
        result = el.verify(self.owner)
        self.assertTrue(result["ok"])
        self.assertNotEqual(result["head"], remembered)


class TestAppend(LogTestCase):
    def test_first_entry_links_to_genesis(self):
        self.seed(1)
        self.assertEqual(el.read(self.writer)[0]["prev_hash"], el.GENESIS)

    def test_entries_link_in_sequence(self):
        self.seed(4)
        entries = el.read(self.writer)
        for previous, current in zip(entries, entries[1:]):
            self.assertEqual(current["prev_hash"], previous["hash"])
            self.assertEqual(current["seq"], previous["seq"] + 1)

    def test_head_of_empty_log_is_genesis(self):
        self.assertEqual(el.head(self.writer), {"seq": 0, "hash": el.GENESIS})

    def test_effective_from_defaults_to_recorded_at(self):
        e = el.append(self.writer, "T", "s", actor="t", recorded_at=T0)
        self.assertEqual(e["effective_from"], T0)

    def test_a_fact_can_be_effective_before_it_was_recorded(self):
        e = el.append(self.writer, "T", "s", actor="t",
                      effective_from="2025-06-01T00:00:00+00:00",
                      recorded_at=T0)
        self.assertLess(e["effective_from"], e["recorded_at"])
        self.assertTrue(el.verify(self.writer)["ok"])

    def test_timestamps_survive_the_round_trip_byte_for_byte(self):
        # Why the columns are text. A timestamptz would come back in the
        # server's own formatting and every later hash would fail to
        # recompute — verification broken by a display convention.
        odd = "2026-01-01T00:00:00+00:00"
        el.append(self.writer, "T", "s", actor="t", recorded_at=odd)
        self.assertEqual(el.read(self.writer)[0]["recorded_at"], odd)
        self.assertTrue(el.verify(self.writer)["ok"])

    def test_body_round_trips_as_a_structure(self):
        el.append(self.writer, "T", "s", {"nested": {"list": [1, 2]}},
                  actor="t", recorded_at=T0)
        self.assertEqual(el.read(self.writer)[0]["body"],
                         {"nested": {"list": [1, 2]}})


class TestRead(LogTestCase):
    def test_since_is_exclusive_and_until_inclusive(self):
        self.seed(5)
        window = el.read(self.writer, since=2, until=4)
        self.assertEqual([e["seq"] for e in window], [3, 4])

    def test_filters_by_type_and_subject(self):
        for event_type, subject in (("A", "s1"), ("B", "s1"), ("A", "s2")):
            el.append(self.writer, event_type, subject, actor="t", recorded_at=T0)
        self.assertEqual(len(el.read(self.writer, event_type="A")), 2)
        self.assertEqual(len(el.read(self.writer, subject="s1")), 2)
        self.assertEqual(
            len(el.read(self.writer, event_type="A", subject="s2")), 1)


class TestWindowVerification(LogTestCase):
    def test_window_verifies_against_the_stored_anchor(self):
        self.seed(6)
        result = el.verify(self.writer, since=3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["entries"], 3)

    def test_missing_anchor_raises_rather_than_falling_back(self):
        # Verifying against a history that is not there must be an error,
        # not a quiet pass as if the window were the start of the log.
        self.seed(2)
        with self.assertRaises(LookupError):
            el.verify(self.writer, since=99)


class TestConcurrentAppend(LogTestCase):
    """
    The reason for the advisory lock. Without it two appenders read the same
    head and the chain forks — which the PRIMARY KEY would turn into a crash,
    and a weaker schema would turn into silent corruption.
    """

    def test_parallel_writers_produce_one_unbroken_chain(self):
        errors_seen = []

        def worker(index):
            try:
                conn = el.connect(self.writer_dsn)
                for i in range(10):
                    el.append(conn, "Thing", f"w{index}", {"i": i},
                              actor="tester", recorded_at=T0)
                conn.close()
            except Exception as exc:            # pragma: no cover
                errors_seen.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors_seen, [])
        result = el.verify(self.owner)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["entries"], 40)


if __name__ == "__main__":
    unittest.main()
