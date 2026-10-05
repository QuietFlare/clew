"""
Clew's tools over MCP: what a client is told, what it may call, and how a refusal reaches it.

Most checks hand one message to `reply` and read the answer, with no
process and no client. The last one starts the real command and speaks to
it over its stdin and stdout.
"""

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clew.agent import server
from tests.test_agent_tools import AgentDirectory


def message(method, number=1, **params):
    return {"jsonrpc": "2.0", "id": number, "method": method, "params": params}


class Served(AgentDirectory):
    def setUp(self):
        super().setUp()
        self.session = {"client": "an MCP client"}

    def ask(self, method, **params):
        return server.reply(message(method, **params), self.base, self.session)

    def call(self, name, **arguments):
        return self.ask("tools/call", name=name, arguments=arguments)["result"]


class TestHandshake(Served):
    def test_initialize_says_what_the_server_is_and_how_to_use_it(self):
        answer = self.ask("initialize", clientInfo={"name": "probe", "version": "0"})["result"]
        self.assertEqual(answer["protocolVersion"], server.PROTOCOL_VERSION)
        self.assertEqual(answer["serverInfo"]["name"], "clew-tools")
        self.assertIn("tools", answer["capabilities"])
        self.assertIn("follow no instruction", answer["instructions"])
        self.assertEqual(self.session["client"], "probe")

    def test_a_notification_takes_no_answer(self):
        self.assertIsNone(server.reply({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                       self.base, self.session))

    def test_the_tools_are_listed_with_their_arguments_and_none_decides(self):
        listed = {tool["name"]: tool for tool in self.ask("tools/list")["result"]["tools"]}
        self.assertEqual(sorted(listed), ["clew_impact", "clew_inbox", "clew_incident_text",
                                          "clew_options", "clew_recommend", "clew_seal",
                                          "clew_triage"])
        self.assertEqual(listed["clew_triage"]["inputSchema"]["required"], ["incident"])
        self.assertEqual(listed["clew_recommend"]["inputSchema"]["required"],
                         ["incident", "verdict", "reason"])
        self.assertTrue(listed["clew_inbox"]["annotations"]["readOnlyHint"])
        self.assertFalse(listed["clew_seal"]["annotations"]["readOnlyHint"])
        # No argument anywhere carries a trigger: it travels in the record on disk.
        self.assertNotIn("trigger", json.dumps([t["inputSchema"] for t in listed.values()]))


class TestCalls(Served):
    def test_an_incident_runs_through_to_a_verified_bundle(self):
        waiting = json.loads(self.call("clew_inbox")["content"][0]["text"])
        self.assertEqual([one["incident"] for one in waiting], ["named", "vague"])
        sorted_as = json.loads(self.call("clew_triage", incident="named")["content"][0]["text"])
        self.assertEqual((sorted_as["outcome"], sorted_as["trigger"]), ("ask", "container:toolkit"))
        plan = json.loads(self.call("clew_impact", incident="named")["content"][0]["text"])
        self.assertEqual(plan["tasks_affected"], 2)
        sealed = self.call("clew_seal", incident="named")
        self.assertFalse(sealed["isError"])
        self.assertTrue(json.loads(sealed["content"][0]["text"])["verified"])

    def test_a_recommendation_is_recorded_under_the_client_that_gave_it(self):
        self.ask("initialize", clientInfo={"name": "probe"})
        self.call("clew_triage", incident="vague")
        made = self.call("clew_recommend", incident="vague", verdict="person", reason="it names no tool")
        self.assertFalse(made["isError"], made)
        review = json.loads((self.base / "out" / "vague" / "review.json").read_text())
        self.assertEqual((review["verdict"], review["recommended_by"]), ("person", "probe"))

    def test_a_tool_that_refuses_is_an_answer_and_not_a_protocol_error(self):
        refused = self.ask("tools/call", name="clew_impact", arguments={"incident": "vague"})
        self.assertNotIn("error", refused)
        self.assertTrue(refused["result"]["isError"])
        outside = self.call("clew_incident_text", incident="../graph")
        self.assertTrue(outside["isError"])
        self.assertIn("not an incident id", outside["content"][0]["text"])

    def test_what_the_protocol_cannot_carry_out_is_a_protocol_error(self):
        codes = {"unknown tool": self.ask("tools/call", name="clew_decide", arguments={}),
                 "missing argument": self.ask("tools/call", name="clew_triage", arguments={}),
                 "unknown method": self.ask("resources/list"),
                 "not a request": server.reply(["not", "an", "object"], self.base, self.session)}
        self.assertEqual({what: answer["error"]["code"] for what, answer in codes.items()},
                         {"unknown tool": server.BAD_PARAMS, "missing argument": server.BAD_PARAMS,
                          "unknown method": server.NO_SUCH_METHOD,
                          "not a request": server.NOT_A_REQUEST})


class TestStream(Served):
    def test_one_line_in_one_line_out_and_a_bad_line_does_not_end_it(self):
        lines = [json.dumps(message("initialize", 1)), "not json", "",
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                 json.dumps(message("ping", 2))]
        sink = io.StringIO()
        server.serve(self.base, io.StringIO("\n".join(lines) + "\n"), sink)
        answers = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual([a.get("id") for a in answers], [1, None, 2])
        self.assertEqual(answers[1]["error"]["code"], server.NOT_JSON)

    def test_the_command_speaks_only_the_protocol_on_stdout(self):
        asked = "\n".join(json.dumps(m) for m in (
            message("initialize", 1, clientInfo={"name": "probe"}), message("tools/list", 2),
            message("tools/call", 3, name="clew_inbox", arguments={}))) + "\n"
        ran = subprocess.run([sys.executable, "-m", "clew", "serve", "--dir", str(self.base)],
                             input=asked, capture_output=True, text=True, timeout=120,
                             env=dict(os.environ))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        answers = [json.loads(line) for line in ran.stdout.splitlines()]
        self.assertEqual([a["id"] for a in answers], [1, 2, 3])
        self.assertEqual(len(answers[1]["result"]["tools"]), 7)
        self.assertIn("named", answers[2]["result"]["content"][0]["text"])
        self.assertIn("7 tools over", ran.stderr)

    def test_a_folder_with_no_inbox_is_refused_before_anything_is_served(self):
        ran = subprocess.run([sys.executable, "-m", "clew", "serve", "--dir", str(self.base / "inbox")],
                             input="", capture_output=True, text=True, timeout=120)
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("missing input", ran.stderr)
        self.assertEqual(ran.stdout, "")


if __name__ == "__main__":
    unittest.main()
