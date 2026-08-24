"""
The MCP server: protocol, and the properties that make it safe to point a
language model at a compliance record.

Two things are tested here that are not really about MCP at all. That the
server is read-only — nothing it exposes can write a fact, and an auditor's
chat session is the last place a new fact should be able to enter the record.
And that bundles sealed from different logs are detected rather than
silently interleaved into one plausible-looking history.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server
from core import evidence
from core import eventlog
from core import policy as policy_module

ROOT = Path(__file__).resolve().parent.parent
T = "2026-01-01T00:00:00+00:00"


def log_entry(seq, prev_hash, subject="s1", event_type="Withdrawn",
              effective_from="2026-03-01", body=None):
    fields = {
        "seq": seq, "effective_from": effective_from, "recorded_at": T,
        "actor": "tester", "event_type": event_type, "subject": subject,
        "body": eventlog.canonical(body or {}), "prev_hash": prev_hash,
    }
    fields["hash"] = eventlog.event_hash(fields)
    return fields


def a_plan():
    decision = policy_module.decide("REGENERABLE", storage="WRITABLE")
    return {
        "clew_plan_version": 1, "trigger": "withdrawal of s1",
        **policy_module.identify(policy_module.DEFAULT),
        "tasks_total": 10, "tasks_affected": 1, "entry_tasks": ["t0"],
        "plan": [{
            "task": "t1", "process": "P", "name": "t1",
            "action": decision["action"], "rule": decision["rule"],
            "because": decision["because"], "contribution": "REGENERABLE",
            "storage": "WRITABLE", "exclusive": False, "terminal": False,
            "reason": "test", "evidence_path": ["t0", "t1"],
        }],
        "caveats": [],
    }


def seal(directory, events, name="b1"):
    target = Path(directory) / name
    head = ({"seq": events[-1]["seq"], "hash": events[-1]["hash"]}
            if events else {"seq": 0, "hash": eventlog.GENESIS})
    evidence.build(target, {"plan.json": a_plan(),
                            "policy.json": policy_module.DEFAULT,
                            "events.json": events, "inputs.json": {}},
                   log_head=head)
    return target


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = holder.name

    def chain(self, n, subject="s1"):
        entries, prev = [], eventlog.GENESIS
        for i in range(1, n + 1):
            entries.append(log_entry(i, prev, subject=subject))
            prev = entries[-1]["hash"]
        return entries


class TestProtocol(ServerTestCase):
    """Driven as a client drives it, over a real pipe."""

    def converse(self, messages, bundles):
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "mcp_server.py"), "--bundles", bundles],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        payload = "".join(json.dumps(m) + "\n" for m in messages)
        out, _ = process.communicate(payload, timeout=30)
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def setUp(self):
        super().setUp()
        seal(self.tmp, self.chain(3))

    def test_initialize_returns_a_version_and_the_guardrail(self):
        replies = self.converse(
            [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": mcp_server.PROTOCOL_VERSION}}],
            self.tmp)
        result = replies[0]["result"]
        self.assertEqual(result["protocolVersion"], mcp_server.PROTOCOL_VERSION)
        self.assertEqual(result["serverInfo"]["name"], "clew")
        # The instructions are the guardrail. If they ever go missing the
        # model loses every constraint on how it reports this record.
        for demand in ("QUOTE THE CITATIONS", "NEVER CONCLUDE COMPLIANCE",
                       "coverage"):
            self.assertIn(demand, result["instructions"])

    def test_notifications_get_no_reply(self):
        replies = self.converse(
            [{"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "ping"}],
            self.tmp)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["id"], 2)

    def test_tools_list_declares_a_schema_for_every_tool(self):
        replies = self.converse(
            [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], self.tmp)
        tools = replies[0]["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, set(mcp_server.BY_NAME))
        for tool in tools:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_an_unknown_tool_is_a_protocol_error(self):
        replies = self.converse(
            [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "delete_everything", "arguments": {}}}],
            self.tmp)
        self.assertIn("error", replies[0])

    def test_a_missing_argument_is_reported_not_guessed(self):
        replies = self.converse(
            [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "subject_history", "arguments": {}}}],
            self.tmp)
        self.assertIn("error", replies[0])
        self.assertIn("missing required argument", replies[0]["error"]["message"])

    def converse_raw(self, lines, bundles):
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "mcp_server.py"), "--bundles", bundles],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        out, _ = process.communicate("".join(lines), timeout=30)
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def test_malformed_input_does_not_kill_the_session(self):
        # Both shapes of bad line: unparseable, and valid JSON that is not an
        # object. The second one is the dangerous one — it parses, and then
        # has no .get() — and it must not end a session an auditor is in the
        # middle of.
        replies = self.converse_raw(
            ["{ not json at all\n",
             '"a bare string"\n',
             "[1, 2, 3]\n",
             json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n"],
            self.tmp)
        self.assertEqual(len(replies), 4)
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["error"]["code"], -32600)
        self.assertEqual(replies[2]["error"]["code"], -32600)
        self.assertEqual(replies[3]["id"], 9)


class TestReadOnly(ServerTestCase):
    """
    Nothing here can change the record. Asserted structurally rather than by
    inspection, so a future tool that writes has to break a test to land.
    """

    def test_no_tool_name_suggests_a_write(self):
        forbidden = ("append", "write", "record", "seal", "delete", "update",
                     "set_", "create", "sign", "adopt")
        for name in mcp_server.BY_NAME:
            for word in forbidden:
                self.assertNotIn(word, name, f"tool {name!r} looks like a write")

    def test_the_server_never_opens_a_database_connection(self):
        # The auditor surface reads sealed bundles. A connection is a
        # credential, and this process should not hold one.
        source = (ROOT / "mcp_server.py").read_text()
        self.assertNotIn("eventlog.connect", source)
        self.assertNotIn("--dsn", source)

    def test_tools_leave_the_bundle_untouched(self):
        target = seal(self.tmp, self.chain(3))
        before = {p.name: p.read_bytes() for p in target.iterdir()}
        store = mcp_server.load_store(self.tmp)
        for tool in mcp_server.TOOLS:
            args = {}
            schema = tool["inputSchema"].get("properties", {})
            if "bundle" in schema:
                args["bundle"] = target.name
            if "task" in schema:
                args["task"] = "t1"
            if "subject" in schema:
                args["subject"] = "s1"
            if "as_of" in schema:
                args["as_of"] = "2026-06-01"
            tool["handler"](store, args)
        after = {p.name: p.read_bytes() for p in target.iterdir()}
        self.assertEqual(before, after)


class TestCrossLogDetection(ServerTestCase):
    """
    A log has no identity. Two logs both number their entries from 1, and
    merging them would interleave two unrelated histories into one plausible
    timeline. Detected by the thing that must agree if they are the same log.
    """

    def test_bundles_from_one_log_merge_cleanly(self):
        events = self.chain(3)
        seal(self.tmp, events[:2], name="early")
        seal(self.tmp, events, name="later")
        bundles, entries, conflicts = mcp_server.load_store(self.tmp)
        self.assertEqual(len(bundles), 2)
        self.assertEqual(len(entries), 3)
        self.assertEqual(conflicts, [])

    def test_bundles_from_different_logs_are_flagged(self):
        seal(self.tmp, self.chain(2, subject="s1"), name="log-a")
        seal(self.tmp, self.chain(2, subject="s2"), name="log-b")
        _, _, conflicts = mcp_server.load_store(self.tmp)
        self.assertTrue(conflicts)
        self.assertEqual({c["seq"] for c in conflicts}, {1, 2})

    def test_the_warning_reaches_every_answer_from_merged_entries(self):
        seal(self.tmp, self.chain(2, subject="s1"), name="log-a")
        seal(self.tmp, self.chain(2, subject="s2"), name="log-b")
        store = mcp_server.load_store(self.tmp)
        for handler, args in (
            (mcp_server.tool_subject_history, {"subject": "s1"}),
            (mcp_server.tool_policy_history, {}),
            (mcp_server.tool_policy_in_force, {"as_of": "2026-06-01"}),
            (mcp_server.tool_list_bundles, {}),
        ):
            joined = " ".join(handler(store, args)["coverage"])
            self.assertIn("DIFFERENT LOGS", joined)


class TestLoading(ServerTestCase):
    def test_an_unreadable_item_does_not_deny_the_rest(self):
        seal(self.tmp, self.chain(2), name="good")
        broken = Path(self.tmp) / "broken"
        broken.mkdir()
        (broken / evidence.MANIFEST).write_text("{ not json")
        bundles, _, _ = mcp_server.load_store(self.tmp)
        self.assertEqual([b["name"] for b in bundles], ["good"])

    def test_a_directory_that_is_itself_a_bundle_is_accepted(self):
        target = seal(self.tmp, self.chain(2), name="solo")
        bundles, _, _ = mcp_server.load_store(target)
        self.assertEqual(len(bundles), 1)

    def test_an_empty_directory_says_it_found_nothing(self):
        empty = Path(self.tmp) / "empty"
        empty.mkdir()
        store = mcp_server.load_store(empty)
        result = mcp_server.tool_list_bundles(store, {})
        self.assertEqual(result["result"]["bundles"], [])
        self.assertIn("no readable bundle", " ".join(result["coverage"]))


if __name__ == "__main__":
    unittest.main()
