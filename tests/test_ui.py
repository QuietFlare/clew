"""
The local page and its server, with no agent and no model.

The agent launch is replaced by a function that writes the files an agent
run leaves, so what is tested is the server's own work: who may call it,
what it reads from a folder, and that a step counts as done only when its
file exists.
"""

import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clew.ui import app as ui
from clew.ui.page import PAGE

RUNS = ROOT / "tests" / "fixtures" / "horus_run"

RECORD = {"outcome": "ask", "choice": "shell", "confidence": 0.9, "trigger": "container:shell",
          "reason": "0.90, at or above the asking bar 0.6", "backend": "jev",
          "model": "jev-1.13.0", "options": {"shell": "container:shell"},
          "incident": {"sha256": "0" * 64, "text": "n"}}
PLAN = {"trigger": "container:shell", "policy_version": "v1", "tasks_affected": 1,
        "tasks_total": 4, "actions": {"REGENERATE": 1},
        "plan": [{"process": "analyse", "action": "REGENERATE", "rule": "R8"}]}


def leaves(outcome="ask", plan=True, review=None):
    """A stand-in for the agent: it writes what a run with this outcome leaves behind."""
    def launch(job, home, roots):
        out = job.folder / "out" / ui.INCIDENT
        out.mkdir(parents=True)
        (out / "triage.json").write_text(json.dumps(dict(RECORD, outcome=outcome)))
        if plan and outcome == "ask":
            (out / "plan.json").write_text(json.dumps(PLAN))
        if review:
            (out / "review.json").write_text(json.dumps(review))
        job.turns, job.cost = 12, 0.03
    return launch


class Served(unittest.TestCase):
    launch = staticmethod(leaves())
    hosts = ()

    def setUp(self):
        if not hasattr(self, "tmp"):
            self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server, self.link = ui.serve(self.tmp.name, port=0, token="t0ken", hosts=self.hosts)
        self.server.app.launch = self.launch
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None, token="t0ken", host=None, method="POST"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method=method,
            data=None if method == "GET" else json.dumps(body or {}).encode(),
            headers={"X-Clew-Token": token, "Content-Type": "application/json"})
        if host:
            request.add_header("Host", host)
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                raw = reply.read()
                return reply.status, raw if method == "GET" else json.loads(raw)
        except urllib.error.HTTPError as bad:
            return bad.code, json.loads(bad.read())

    def finished(self, name):
        for _ in range(200):
            status, job = self.call("/api/job", {"job": name})
            if job["state"] != "running":
                return job
            threading.Event().wait(0.02)
        self.fail("the job never finished")

    def start(self, **over):
        body = dict({"path": str(RUNS), "run": "horus_run", "incident": "shell is broken"}, **over)
        return self.call("/api/run", body)


class TestWhoMayCall(Served):
    def test_the_page_is_served_and_fetches_nothing_from_outside(self):
        status, page = self.call("/", method="GET")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Clew</title>", page)
        for outside in ("http://", "https://", "//cdn", "@import"):
            self.assertNotIn(outside, PAGE)

    def test_a_call_without_the_token_is_refused(self):
        self.assertEqual(self.call("/api/state", token="")[0], 403)
        self.assertEqual(self.call("/api/state", token="wrong")[0], 403)

    def test_a_call_addressed_to_another_host_is_refused(self):
        self.assertEqual(self.call("/api/state", host="evil.example:80")[0], 403)

    def test_the_link_carries_the_token(self):
        self.assertTrue(self.link.endswith("/?t=t0ken"))

    def test_state_says_when_the_agent_and_triage_go_through_a_gateway(self):
        import os
        from unittest import mock
        gateway = {"ANTHROPIC_BASE_URL": "https://proxy.example", "ANTHROPIC_AUTH_TOKEN": "t",
                   "TYPESAFE_API_KEY": "t", "CLEW_JEV_ENDPOINT": "https://proxy.example/typesafe/v1/systemone"}
        with mock.patch.dict(os.environ, gateway):
            state = self.call("/api/state")[1]
        self.assertEqual((state["api_key"], state["proxy"], state["jev_proxy"]), (True, "proxy.example", True))
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://proxy.example"}, clear=True):
            state = self.call("/api/state")[1]
        # A base URL alone is not a credential, so the agent is not through a gateway.
        self.assertEqual((state["api_key"], state["proxy"], state["jev_proxy"]), (False, None, False))

    def test_a_forwarded_host_is_refused_unless_asked_for(self):
        self.assertEqual(self.call("/api/state", host="me-8770.app.github.dev")[0], 403)

    def test_the_loopback_names_are_answered_on_any_port(self):
        # A container publishes the server's port under whatever port the person chose.
        self.assertEqual(self.call("/api/state", host="localhost:9000")[0], 200)
        self.assertEqual(self.call("/api/state", host="127.0.0.1:9000")[0], 200)
        self.assertEqual(self.call("/api/state", host="localhost.evil.example:9000")[0], 403)

    def test_bound_to_every_interface_the_link_says_localhost(self):
        server, link = ui.serve(self.tmp.name, port=0, token="t0ken", bind="0.0.0.0")
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "0.0.0.0")
        self.assertTrue(link.startswith("http://localhost:"), link)

    def test_a_further_host_is_added_by_flag(self):
        from unittest import mock
        fake = mock.Mock()
        fake.app.token, fake.app.home, fake.app.providers = "tok", "/h", "/p"
        fake.serve_forever.side_effect = KeyboardInterrupt
        with mock.patch.dict(ui.os.environ, {}, clear=True), \
                mock.patch.object(ui, "serve", return_value=(fake, "http://localhost:8770/?t=tok")) as serve, \
                mock.patch.object(ui.webbrowser, "open"):
            ui.main(["--no-browser", "--bind", "0.0.0.0", "--host", "clew.example:8770", "--home", self.tmp.name])
        self.assertEqual(serve.call_args.kwargs, {"hosts": ["clew.example:8770"], "bind": "0.0.0.0"})


def two_run_store(root, on_disk=False):
    """
    A Nextflow lineage store with three runs: `first` and `second` are
    separate sessions of one workflow whose ALIGN output differs; `resumed`
    is a resume of `first` and so shares its graph. Checksums are deep-mode
    from a Nextflow that computes them as labelled, so they are believed.
    With `on_disk`, the first run's work directory and a published copy of
    its output exist under `root`, with the copy recorded in the store.
    """
    root = Path(root)
    store = root / ".lineage"
    (store / ".history").mkdir(parents=True)
    runs = {"first": ("a" * 32, "s-one", "2026-08-01 10:00:00 CEST"),
            "second": ("b" * 32, "s-two", "2026-08-02 10:00:00 CEST"),
            "resumed": ("c" * 32, "s-one", "2026-08-03 10:00:00 CEST")}
    for name, (run_hash, session, stamp) in runs.items():
        (store / ".history" / run_hash).write_text(f"{stamp}\t{name}\t{session}\tlid://{run_hash}\n")
        (store / run_hash).mkdir()
        (store / run_hash / ".data.json").write_text(json.dumps({
            "version": "lineage/v1beta1", "kind": "WorkflowRun",
            "spec": {"sessionId": session, "name": name,
                     "metadata": {"nextflow": {"version": "26.09.2-edge"}}}}))
    work = root / "work" if on_disk else Path("/work")
    for task_hash, run_hash, session, digest in (("1" * 32, "a" * 32, "s-one", "d1"),
                                                  ("2" * 32, "b" * 32, "s-two", "d2")):
        (store / task_hash / "out.bam").mkdir(parents=True)
        (store / task_hash / ".data.json").write_text(json.dumps({
            "version": "lineage/v1beta1", "kind": "TaskRun",
            "spec": {"sessionId": session, "workflowRun": f"lid://{run_hash}", "name": "PIPE:ALIGN",
                     "container": "img:1", "script": "align", "input": []}}))
        task_dir = work / task_hash[:2] / task_hash[2:]
        (store / task_hash / "out.bam" / ".data.json").write_text(json.dumps({
            "version": "lineage/v1beta1", "kind": "FileOutput",
            "spec": {"path": f"{task_dir}/out.bam", "size": 3,
                     "checksum": {"value": digest, "algorithm": "nextflow", "mode": "deep"},
                     "workflowRun": f"lid://{run_hash}", "taskRun": f"lid://{task_hash}"}}))
        if on_disk and session == "s-one":
            task_dir.mkdir(parents=True)
            (task_dir / "out.bam").write_bytes(b"abc")
            (root / "results" / "out").mkdir(parents=True)
            (root / "results" / "out" / "out.bam").write_bytes(b"abc")
            (store / run_hash / "out" / "out.bam").mkdir(parents=True)
            (store / run_hash / "out" / "out.bam" / ".data.json").write_text(json.dumps({
                "version": "lineage/v1beta1", "kind": "FileOutput",
                "spec": {"path": f"{root}/results/out/out.bam", "size": 3,
                         "checksum": {"value": digest, "algorithm": "nextflow", "mode": "deep"},
                         "source": f"lid://{task_hash}/out.bam", "workflowRun": f"lid://{run_hash}"}}))
    return str(root)


class TestReclaim(Served):
    """The run's work directories weighed on the page, from the record and the disk here."""

    def setUp(self):
        super().setUp()
        self.record = two_run_store(Path(self.tmp.name) / "runs", on_disk=True)

    def plan(self, **over):
        body = dict({"path": self.record, "run": "first", "work_root": f"{self.record}/work",
                     "results": f"{self.record}/results"}, **over)
        return self.call("/api/reclaim", body)

    def test_a_published_copy_in_the_record_proves_a_directory_redundant(self):
        status, found = self.plan()
        self.assertEqual(status, 200)
        self.assertEqual(found["verdicts"], {"REDUNDANT": 1})
        self.assertEqual((found["reclaimable_dirs"], found["reclaimable_bytes"], found["tasks_total"]), (1, 3, 1))
        self.assertEqual(found["groups"][0]["processes"], [{"process": "ALIGN", "count": 1, "bytes": 3}])
        self.assertEqual(found["withheld"], [])
        self.assertIn("plan", found)

    def test_without_the_results_folder_nothing_can_be_redundant(self):
        status, found = self.plan(results="")
        self.assertEqual(found["verdicts"], {"KEEP": 1})
        self.assertEqual(found["withheld"][0]["count"], 1)
        self.assertTrue(any("REDUNDANT" in line for line in found["caveats"]))

    def test_the_work_folder_must_be_here(self):
        self.assertEqual(self.plan(work_root="")[0], 400)
        status, found = self.plan(work_root="/no/such/folder")
        self.assertEqual(status, 400)
        self.assertIn("not a folder", found["error"])


class TestDrift(Served):
    """Two runs of the record compared on the page, with no agent and no job."""

    def setUp(self):
        super().setUp()
        self.record = two_run_store(Path(self.tmp.name) / "runs")

    def compare(self, before, after, **over):
        return self.call("/api/drift", dict({"path": self.record, "run": after, "before": before}, **over))

    def test_the_root_and_its_cause_are_read_from_the_record(self):
        status, found = self.compare("first", "second")
        self.assertEqual(status, 200)
        self.assertEqual((found["before"], found["after"], found["tasks_total"]), ("first", "second", 1))
        self.assertEqual(found["verdicts"], {"DRIFTED": 1})
        self.assertEqual([r["process"] for r in found["roots"]], ["ALIGN"])
        self.assertEqual(found["roots"][0]["cause"], "same inputs and recipe, different outputs")
        self.assertEqual(found["roots"][0]["files"], ["out.bam"])
        self.assertEqual(found["ignored"], ["versions.yml"])
        self.assertTrue(any("paired by name" in line for line in found["caveats"]))

    def test_an_ignored_output_is_not_compared(self):
        status, found = self.compare("first", "second", ignore="out.bam, versions.yml")
        self.assertEqual(found["verdicts"], {"REPRODUCED": 1})
        self.assertEqual(found["roots"], [])
        self.assertEqual(found["groups"][0]["verdict"], "REPRODUCED")

    def test_two_runs_of_one_resume_chain_are_refused(self):
        status, found = self.compare("first", "resumed")
        self.assertEqual(status, 400)
        self.assertIn("resume chain", found["error"])

    def test_the_same_run_twice_and_an_unknown_run_are_refused(self):
        self.assertEqual(self.compare("second", "second")[0], 400)
        self.assertEqual(self.compare("nowhere", "second")[0], 400)

    def test_a_reading_is_the_models_words_on_the_plan_shown_and_is_given_once(self):
        """
        The model sees the plan the page laid out, nothing else, and a
        second click costs no second call. The verdicts are not touched.
        """
        from unittest import mock
        plan = self.compare("first", "second")[1]
        asked = []

        def reads(kind, answer):
            asked.append((kind, answer))
            return {"text": "One task, ALIGN, drifted; its inputs and recipe match.", "model": "m"}
        with mock.patch.object(ui.summary, "explain", reads):
            status, reading = self.call("/api/explain", {"plan": plan["plan"]})
            self.assertEqual((status, reading["model"]), (200, "m"))
            self.assertIn("ALIGN", reading["text"])
            self.assertEqual(self.call("/api/explain", {"plan": plan["plan"]})[1], reading)
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0][0], "drift")
        self.assertEqual(asked[0][1]["roots"], plan["roots"])
        self.assertEqual(self.call("/api/explain", {"plan": "nothing"})[0], 404)

    def test_no_credential_is_said_rather_than_tried(self):
        import os
        from unittest import mock
        plan = self.compare("first", "second")[1]
        with mock.patch.dict(os.environ, {}, clear=True):
            status, found = self.call("/api/explain", {"plan": plan["plan"]})
        self.assertEqual(status, 400)
        self.assertIn("credential", found["error"])


class TestSummary(unittest.TestCase):
    """The model client: what it sends, and what it does with the answer."""

    def test_the_request_carries_the_plan_the_rules_and_the_credential(self):
        from unittest import mock
        from clew.agent import summary
        seen = {}

        def opened(call, timeout):
            seen["url"], seen["headers"] = call.full_url, dict(call.headers)
            seen["body"] = json.loads(call.data)
            return io.BytesIO(json.dumps({"model": "m-1", "content": [
                {"type": "text", "text": "Two roots."}]}).encode())
        env = {"ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "https://proxy.example/"}
        with mock.patch.object(summary.urllib.request, "urlopen", opened):
            reading = summary.explain("drift", {"roots": [{"task": "ALIGN"}]}, env=env)
        self.assertEqual(reading, {"text": "Two roots.", "model": "m-1"})
        self.assertEqual(seen["url"], "https://proxy.example/v1/messages")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer t")
        self.assertEqual(seen["headers"]["User-agent"], "clew-lineage")
        self.assertIn("ALIGN", seen["body"]["messages"][0]["content"])
        self.assertIn("do not judge", seen["body"]["system"])

    def test_a_refusal_names_the_models_error(self):
        from unittest import mock
        from urllib.error import HTTPError
        from clew.agent import summary
        body = io.BytesIO(json.dumps({"error": {"message": "allowance used up"}}).encode())

        def opened(call, timeout):
            raise HTTPError(call.full_url, 402, "no", {}, body)
        with mock.patch.object(summary.urllib.request, "urlopen", opened), \
                self.assertRaises(summary.Refused) as refused:
            summary.explain("drift", {}, env={"ANTHROPIC_API_KEY": "k"})
        self.assertIn("allowance used up", str(refused.exception))


class TestForwarded(Served):
    """
    A Codespace reaches the server through one forwarded name. Only that
    name is added, the token still guards every request, and nothing else
    about who may call changes.
    """
    hosts = ("me-8770.app.github.dev",)

    def test_the_forwarded_host_is_answered_with_or_without_its_port(self):
        self.assertEqual(self.call("/api/state", host="me-8770.app.github.dev")[0], 200)
        self.assertEqual(self.call("/api/state", host="me-8770.app.github.dev:443")[0], 200)

    def test_the_forwarded_host_still_needs_the_token(self):
        self.assertEqual(self.call("/api/state", host="me-8770.app.github.dev", token="")[0], 403)

    def test_another_host_is_still_refused(self):
        self.assertEqual(self.call("/api/state", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("/api/state", host="me-8771.app.github.dev")[0], 403)

    def test_the_host_is_read_from_what_a_codespace_sets(self):
        env = {"CODESPACE_NAME": "me", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev"}
        self.assertEqual(ui.forwarded_host(8770, env), "me-8770.app.github.dev")
        self.assertIsNone(ui.forwarded_host(8770, {}))

    def test_a_codespace_forwards_and_prints_the_link_unasked(self):
        import io
        import os
        from contextlib import redirect_stdout
        from unittest import mock
        fake = mock.Mock()
        fake.app.token, fake.app.home, fake.app.providers = "tok", "/h", "/p"
        fake.serve_forever.side_effect = KeyboardInterrupt
        env = {"CODESPACE_NAME": "me", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(ui, "serve", return_value=(fake, "http://127.0.0.1:8770/?t=tok")) as serve, \
                mock.patch.object(ui.webbrowser, "open") as opened, redirect_stdout(out):
            ui.main(["--port", "8770", "--home", self.tmp.name])
        self.assertEqual(serve.call_args.kwargs["hosts"], ["me-8770.app.github.dev"])
        self.assertIn("Clew UI: https://me-8770.app.github.dev/?t=tok", out.getvalue())
        opened.assert_not_called()

    def test_the_flag_refuses_to_run_outside_a_codespace(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as refused:
            ui.main(["--forwarded", "--no-browser", "--port", "8770", "--home", self.tmp.name])
        self.assertIn("CODESPACE_NAME", str(refused.exception))


def multipart(files, boundary="clew-test", paths=True):
    """What the page sends: a "path" field, then the file under its bare name, per file.
    With paths=False, only the file, named by its path, as an older page would send."""
    body = b""
    for name, data in files:
        if paths:
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="path"\r\n\r\n{name}\r\n').encode()
        sent = name.rsplit("/", 1)[-1] if paths else name
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{sent}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


class TestAddRecord(Served):
    def upload(self, files, token="t0ken", paths=True):
        body, kind = multipart(files, paths=paths)
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/upload", method="POST", data=body,
                                         headers={"X-Clew-Token": token, "Content-Type": kind})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as bad:
            return bad.code, json.loads(bad.read())

    def archive(self, files, name, token="t0ken"):
        """Upload one archive, built here the way Finder or the page would build it."""
        import io
        import tarfile
        import zipfile
        buf = io.BytesIO()
        if name.endswith(".zip"):
            with zipfile.ZipFile(buf, "w") as z:
                for path, data in files:
                    z.writestr(path, data)
        else:
            with tarfile.open(fileobj=buf, mode="w:gz" if name.endswith((".tgz", ".gz")) else "w") as t:
                for path, data in files:
                    info = tarfile.TarInfo(path)
                    info.size = len(data)
                    t.addfile(info, io.BytesIO(data))
        boundary = "clew-arch"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="archive"; filename="{name}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + buf.getvalue() + \
            f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/upload", method="POST", data=body,
                                         headers={"X-Clew-Token": token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as bad:
            return bad.code, json.loads(bad.read())

    def test_the_path_of_an_archive_on_this_machine_opens_it(self):
        import io
        import zipfile
        where = Path(self.tmp.name) / "store.zip"
        with zipfile.ZipFile(where, "w") as z:
            for p in RUNS.rglob("*"):
                if p.is_file():
                    z.write(p, f"demo/{p.relative_to(RUNS)}")
        status, found = self.call("/api/browse", {"path": str(where)})
        self.assertEqual(status, 200, found)
        self.assertEqual(found["engine"], "horus")
        self.assertEqual(found["path"], str(Path(self.tmp.name).resolve() / "records" / "demo"))

    def test_a_tar_gz_of_a_store_unpacks_and_opens(self):
        files = [(f"demo/{p.relative_to(RUNS)}", p.read_bytes()) for p in RUNS.rglob("*") if p.is_file()]
        status, got = self.archive(files, "record.tar.gz")
        self.assertEqual(status, 200, got)
        self.assertEqual(got["engine"], "horus")
        self.assertEqual(got["runs"], 1)
        self.assertEqual(self.call("/api/browse", {"path": got["path"]})[1]["engine"], "horus")

    def test_a_zip_from_finder_unpacks_without_its_metadata(self):
        files = [(f"demo/{p.relative_to(RUNS)}", p.read_bytes()) for p in RUNS.rglob("*") if p.is_file()]
        files += [("__MACOSX/demo/._run.json", b"\x00\x05\x16\x07"), ("demo/.DS_Store", b"\x00Bud1")]
        status, got = self.archive(files, "demo.zip")
        self.assertEqual(status, 200, got)
        root = Path(got["path"])
        self.assertFalse((root / ".DS_Store").exists())
        self.assertFalse((Path(self.tmp.name) / "records" / "__MACOSX").exists())

    def test_a_zip_of_lineage_itself_keeps_its_dot(self):
        status, got = self.archive([(".lineage/.history/abc", b"x"), ("./.lineage/abc/.data.json", b"{}")], "lineage.zip")
        self.assertEqual(status, 200, got)
        root = Path(got["path"])
        self.assertTrue(root.name.startswith("record-"), root)
        self.assertTrue((root / ".lineage" / ".history" / "abc").is_file())
        self.assertFalse((root / "lineage").exists())

    def test_an_archive_that_climbs_out_is_refused(self):
        status, got = self.archive([("demo/../../escape", b"no")], "bad.tgz")
        self.assertEqual(status, 400)
        self.assertIn("refusing", got["error"])

    def test_an_archive_of_the_wrong_kind_is_refused(self):
        status, got = self.archive([("demo/x", b"x")], "record.rar")
        self.assertEqual(status, 400)
        self.assertIn("must be", got["error"])

    def test_an_upload_with_no_record_is_refused_and_not_kept(self):
        status, got = self.upload([("photos/holiday.txt", b"sun")])
        self.assertEqual(status, 400, got)
        self.assertIn("No run record", got["error"])
        self.assertFalse((Path(self.tmp.name) / "records" / "photos").exists())

    def test_a_launch_folder_keeps_its_name_and_only_its_record_is_written(self):
        status, got = self.upload([("Petri/.lineage/.history/abc", b"2026-01-01\tr\ts\tlid://abc"),
                                   ("Petri/.lineage/abc/.data.json", b"{}"),
                                   ("Petri/petri.config", b"lineage.enabled = true")])
        self.assertEqual(status, 200, got)
        root = Path(got["path"])
        self.assertEqual(root, Path(self.tmp.name).resolve() / "records" / "Petri")
        self.assertEqual((root / ".lineage" / ".history" / "abc").read_bytes(), b"2026-01-01\tr\ts\tlid://abc")
        self.assertEqual(got["files"], 3)

    def test_the_file_name_alone_still_places_a_file(self):
        status, got = self.upload([("Petri/.lineage/.history/abc", b"x")], paths=False)
        self.assertEqual(status, 200, got)
        self.assertTrue((Path(got["path"]) / ".lineage" / ".history" / "abc").is_file())

    def test_a_bare_lineage_folder_gets_a_dated_name_and_stays_lineage(self):
        status, got = self.upload([(".lineage/.history/abc", b"x"), (".lineage/abc/.data.json", b"{}")])
        self.assertEqual(status, 200, got)
        root = Path(got["path"])
        self.assertTrue(root.name.startswith("record-"), root)
        self.assertTrue((root / ".lineage" / ".history" / "abc").is_file())

    def test_paths_that_climb_out_are_refused(self):
        status, got = self.upload([("Petri/../../etc/passwd", b"no")])
        self.assertEqual(status, 400)
        self.assertIn("refusing", got["error"])
        self.assertFalse((Path(self.tmp.name) / "records").exists())

    def test_the_token_guards_uploads_too(self):
        status, _ = self.upload([("Petri/.lineage/x", b"x")], token="wrong")
        self.assertEqual(status, 403)

    def test_an_uploaded_store_opens_like_any_folder(self):
        files = [(f"demo/{p.relative_to(RUNS)}", p.read_bytes()) for p in RUNS.rglob("*") if p.is_file()]
        status, got = self.upload(files)
        self.assertEqual(status, 200, got)
        status, found = self.call("/api/browse", {"path": got["path"]})
        self.assertEqual(status, 200)
        self.assertTrue(found["engine"], found)
        self.assertTrue(found["runs"])


class TestFolders(Served):
    def test_a_plain_folder_lists_its_subfolders_and_no_runs(self):
        (Path(self.tmp.name) / "alpha").mkdir()
        (Path(self.tmp.name) / ".hidden").mkdir()
        status, found = self.call("/api/browse", {"path": self.tmp.name})
        self.assertEqual((status, found["engine"], found["runs"]), (200, None, []))
        self.assertIn("alpha", found["folders"])
        self.assertNotIn(".hidden", found["folders"])

    def test_a_run_folder_names_its_engine_and_runs(self):
        status, found = self.call("/api/browse", {"path": str(RUNS)})
        self.assertEqual(found["engine"], "horus")
        self.assertEqual([r["name"] for r in found["runs"]], ["horus_run"])

    def test_a_folder_that_cannot_be_read_is_said(self):
        status, found = self.call("/api/browse", {"path": "/no/such/folder/anywhere"})
        self.assertEqual(status, 400)
        self.assertIn("cannot read", found["error"])

    def test_state_says_which_engines_can_be_picked_by_folder(self):
        engines = {e["name"]: e["folder"] for e in self.call("/api/state")[1]["engines"]}
        self.assertTrue(engines["horus"] and engines["nextflow"])
        self.assertFalse(engines["cromwell"])


class TestRun(Served):
    def test_a_run_extracts_and_reports_each_step_from_its_file(self):
        status, started = self.start()
        self.assertEqual(status, 200)
        job = self.finished(started["job"])
        self.assertEqual(job["state"], "finished")
        self.assertEqual(job["extract"]["tasks"], 4)
        states = {s["key"]: s["state"] for s in job["steps"]}
        # No bundle was written, so evidence is not done whatever the agent said.
        self.assertEqual(states, {"extract": "done", "triage": "done", "impact": "done",
                                  "evidence": "skipped", "act": "skipped"})
        self.assertEqual(job["plan"]["actions"], {"REGENERATE": 1})
        self.assertEqual((job["turns"], job["cost"]), (12, 0.03))
        folder = Path(job["folder"])
        self.assertTrue((folder / "graph.json").is_file())
        self.assertEqual((folder / "inbox" / "incident.txt").read_text(), "shell is broken\n")

    def test_an_empty_incident_or_an_unknown_run_is_refused(self):
        self.assertEqual(self.start(incident="  ")[0], 400)
        self.assertEqual(self.start(run="absent")[0], 400)
        self.assertEqual(self.start(path=self.tmp.name)[0], 400)
        self.assertEqual(self.start(work_root="/no/such/folder")[0], 400)


class TestHeld(Served):
    launch = staticmethod(leaves("held", review={
        "verdict": "dismiss", "trigger": None, "reason": "another tool",
        "status": "recommendation, not a decision", "recommended_by": "clew"}))

    def test_a_held_incident_shows_a_recommendation_and_no_plan(self):
        job = self.finished(self.start()[1]["job"])
        states = {s["key"]: s["state"] for s in job["steps"]}
        self.assertEqual(states["impact"], "skipped")
        self.assertEqual(states["review"], "done")
        self.assertIsNone(job["plan"])
        self.assertEqual(job["review"]["verdict"], "dismiss")


class TestFailure(Served):
    @staticmethod
    def launch(job, home, roots):
        raise ui.Refused("Mainsheet is not installed in this environment")

    def test_a_failed_launch_ends_in_a_state_the_page_can_show(self):
        job = self.finished(self.start()[1]["job"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("Mainsheet is not installed", job["error"])
        self.assertEqual({s["key"]: s["state"] for s in job["steps"]}["triage"], "failed")


class TestOneAtATime(Served):
    gate = threading.Event()

    @classmethod
    def launch(cls, job, home, roots):
        cls.gate.wait(5)

    def test_a_second_run_waits_for_the_first(self):
        first = self.start()[1]["job"]
        self.assertEqual(self.start()[0], 409)
        self.gate.set()
        self.finished(first)


if __name__ == "__main__":
    unittest.main()


class TestPicking(unittest.TestCase):
    """The system dialog is never opened here: only which command would run, and what comes back."""

    def test_each_system_gets_its_own_dialog(self):
        from unittest import mock
        with mock.patch.object(ui.sys, "platform", "darwin"):
            command = ui.picker("/some/where")
            self.assertEqual(command[0], "osascript")
            # The folder is an argument after --, so no path can become script text.
            self.assertEqual(command[-2:], ["--", "/some/where"])
            self.assertNotIn("/some/where", " ".join(command[:-1]))
        with mock.patch.object(ui.sys, "platform", "win32"):
            self.assertEqual(ui.picker("C:/x")[0], "powershell")
        with mock.patch.object(ui.sys, "platform", "linux"), \
                mock.patch.dict(ui.os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(ui.shutil, "which", lambda name: name == "zenity"):
            self.assertEqual(ui.picker("/x")[0], "zenity")

    def test_headless_linux_has_no_dialog(self):
        # A Codespace: zenity or tkinter may be installed, but there is no
        # display to open them on, and the page must say so at once.
        from unittest import mock
        with mock.patch.object(ui.sys, "platform", "linux"), \
                mock.patch.dict(ui.os.environ, {}, clear=True), \
                mock.patch.object(ui.shutil, "which", lambda name: True):
            self.assertIsNone(ui.picker("/x"))

    def test_a_dialog_that_fails_to_open_is_not_available(self):
        from unittest import mock
        done = mock.Mock(stdout="", returncode=1)
        with mock.patch.object(ui, "picker", lambda start, file=False: ["x"]), \
                mock.patch.object(ui.subprocess, "run", return_value=done):
            self.assertEqual(ui.pick("/"), {"available": False, "path": None})

    def test_no_dialog_at_all_is_said(self):
        from unittest import mock
        with mock.patch.object(ui, "picker", lambda start, file=False: None):
            self.assertEqual(ui.pick("/"), {"available": False, "path": None})

    def test_a_cancelled_dialog_gives_no_path(self):
        from unittest import mock
        done = mock.Mock(stdout="\n", returncode=0)
        with mock.patch.object(ui, "picker", lambda start, file=False: ["x"]), \
                mock.patch.object(ui.subprocess, "run", return_value=done):
            self.assertEqual(ui.pick("/"), {"available": True, "path": None})

    def test_a_chosen_folder_comes_back(self):
        from unittest import mock
        done = mock.Mock(stdout=str(RUNS) + "/\n", returncode=0)
        with mock.patch.object(ui, "picker", lambda start, file=False: ["x"]), \
                mock.patch.object(ui.subprocess, "run", return_value=done):
            self.assertEqual(Path(ui.pick("/")["path"]), RUNS)


class TestNearby(unittest.TestCase):
    def test_a_folder_inside_a_run_folder_points_up_to_it(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            launch = Path(tmp) / "launch"
            shutil.copytree(RUNS, launch)
            (launch / "work").mkdir()
            found = ui.browse(str(launch / "work"))
        self.assertIsNone(found["engine"])
        self.assertEqual((found["nearby"]["engine"], found["nearby"]["name"]), ("horus", "launch"))


class TestAdapters(Served):
    def test_state_lists_each_adapter_with_the_files_its_kinds_need(self):
        listed = {a["name"]: a for a in self.call("/api/state")[1]["adapters"]}
        self.assertEqual(listed["vina-docking"]["kinds"], ["ligand"])
        self.assertEqual([f["flag"] for f in listed["vina-docking"]["flags"]], ["--ligands"])

    def test_a_run_passes_the_adapter_and_its_file_to_the_agent(self):
        seen = {}

        def launch(job, home, roots):
            seen.update(roots)
            leaves()(job, home, roots)
        self.server.app.launch = launch
        library = ROOT / "tests" / "fixtures" / "pantheon_vina" / "ligands.smi"
        started = self.start(pipeline="vina-docking", adapter_args={"--ligands": str(library)})
        self.finished(started[1]["job"])
        self.assertEqual(seen["CLEW_PIPELINE"], "vina-docking")
        self.assertEqual(seen["CLEW_ADAPTER_ARGS"], f"--ligands {library}")

    def test_an_unknown_adapter_or_setting_or_a_missing_file_is_refused(self):
        self.assertEqual(self.start(pipeline="absent")[0], 400)
        self.assertEqual(self.start(pipeline="vina-docking",
                                    adapter_args={"--other": __file__})[0], 400)
        self.assertEqual(self.start(pipeline="vina-docking",
                                    adapter_args={"--ligands": "/no/such/file"})[0], 400)


class TestDecision(Served):
    """A held incident waits for a person. Their choice is recorded and, when it asks, carried on."""
    launch = staticmethod(leaves("held"))

    def setUp(self):
        super().setUp()
        import os
        from unittest import mock
        clean = {k: v for k, v in os.environ.items() if k not in ("CLEW_DSN", "TYPESAFE_API_KEY")}
        patched = mock.patch.dict(os.environ, dict(clean, PYTHONPATH=str(ROOT)), clear=True)
        patched.start()
        self.addCleanup(patched.stop)
        self.job = self.finished(self.start()[1]["job"])

    def decide(self, **over):
        body = dict({"job": self.job["job"], "action": "ask", "actor": "qa.lead@example.org",
                     "trigger": "container:shell", "reason": "the shell step"}, **over)
        return self.call("/api/decide", body)

    def test_a_held_incident_is_open_and_offers_what_triage_offered(self):
        states = {s["key"]: s["state"] for s in self.job["steps"]}
        self.assertEqual(states["decision"], "open")
        self.assertEqual(states["impact"], "skipped")
        self.assertEqual(self.job["choices"]["triggers"], ["container:shell"])
        self.assertEqual(self.job["state"], "finished")

    def test_asking_a_trigger_records_the_person_and_carries_on_to_a_verified_bundle(self):
        status, job = self.decide()
        self.assertEqual(status, 200)
        job = self.finished(job["job"])
        self.assertEqual(job["state"], "finished", job["error"])
        self.assertEqual((job["decision"]["actor"], job["decision"]["trigger"]),
                         ("qa.lead@example.org", "container:shell"))
        states = {s["key"]: s["state"] for s in job["steps"]}
        self.assertEqual((states["decision"], states["impact"], states["evidence"]),
                         ("done", "done", "done"))
        self.assertTrue(job["verified"])
        self.assertIsNone(job["choices"])

    def test_dismissing_seals_the_dismissal_and_no_plan(self):
        status, job = self.decide(action="dismiss")
        self.assertEqual((status, job["decision"]["decision"]), (200, "dismiss"))
        job = self.finished(job["job"])
        self.assertEqual(job["state"], "finished", job["error"])
        states = {s["key"]: s for s in job["steps"]}
        self.assertEqual(states["impact"]["state"], "skipped")
        self.assertIn("a person dismissed", states["impact"]["detail"])
        self.assertEqual(states["evidence"]["state"], "done")
        self.assertTrue(job["verified"])
        self.assertIsNone(job["plan"])
        self.assertTrue((Path(job["bundle"]) / "decision.json").is_file())

    def test_a_decision_needs_a_name_and_an_offered_trigger(self):
        self.assertEqual(self.decide(actor="  ")[0], 400)
        self.assertEqual(self.decide(trigger="container:invented")[0], 400)
        self.assertEqual(self.decide(action="approve")[0], 400)

    def test_a_second_decision_is_refused(self):
        self.finished(self.decide()[1]["job"])
        self.assertEqual(self.decide(action="dismiss")[0], 400)


DOCKING = ROOT / "tests" / "fixtures" / "pantheon_vina"
ADAPTER = (ROOT / "tests" / "fixtures" / "builder" / "adapter.py").read_text()


def writes(adapter=ADAPTER, tests="import unittest\n", linked=None):
    """A stand-in for the building agent: it leaves these files in a new instance's work folder."""
    def launch(job, home, roots, definition):
        work = home / "mainsheet" / "instances" / "adapter-builder-0a1b2c3d" / "work"
        work.mkdir(parents=True)
        if linked:
            (work / "adapter.py").symlink_to(linked)
        elif adapter:
            (work / "adapter.py").write_text(adapter)
        if tests:
            (work / "test_adapter.py").write_text(tests)
        job.definition = definition
        job.turns, job.cost = 30, 1.2
    return launch


class TestBuild(Served):
    """An agent writes an adapter, a judge checks it, and a person's name installs it."""
    launch = staticmethod(writes())

    def setUp(self):
        import os
        from unittest import mock
        from clew.contracts.registry import LOCAL_VARIABLE, TRIAL_VARIABLE
        # The judge is a process of its own: it finds this checkout by path,
        # and the provider folder is this test's, never the machine's.
        clean = {k: v for k, v in os.environ.items() if k not in (LOCAL_VARIABLE, TRIAL_VARIABLE)}
        self.tmp = tempfile.TemporaryDirectory()
        self.providers = Path(self.tmp.name).resolve() / "providers"
        patched = mock.patch.dict(os.environ, dict(clean, PYTHONPATH=str(ROOT),
                                                   **{LOCAL_VARIABLE: str(self.providers)}), clear=True)
        patched.start()
        self.addCleanup(patched.stop)
        super().setUp()

    def build(self, **over):
        body = dict({"path": str(DOCKING), "run": "pantheon_vina", "name": "site-ligands",
                     "kind": "ligand", "removable": True, "notes": "second column",
                     "sheet": str(DOCKING / "ligands.smi")}, **over)
        return self.call("/api/build", body)

    def built(self, **over):
        status, started = self.build(**over)
        self.assertEqual(status, 200, started)
        return self.finished(started["job"])

    def states(self, job):
        return {s["key"]: s["state"] for s in job["steps"]}

    def test_a_build_ends_judged_and_waits_for_a_person(self):
        job = self.built()
        self.assertEqual((job["state"], job["error"], job["kind"]), ("finished", None, "build"))
        self.assertEqual(self.states(job), {"extract": "done", "brief": "done", "adapter": "done",
                                            "tests": "done", "judge": "done", "approval": "open"})
        self.assertTrue(job["verdict"]["passed"], job["verdict"]["checks"])
        self.assertEqual(job["verdict"]["kinds"][0]["reached"], 3)
        self.assertEqual(job["code"], ADAPTER)
        self.assertTrue(job["can_install"])
        self.assertEqual((job["turns"], job["cost"]), (30, 1.2))
        # Nothing is installed by a build.
        self.assertFalse(self.providers.exists())

    def test_the_agent_is_briefed_from_the_job_folder_and_nothing_else_of_the_site(self):
        job = self.built()
        folder = Path(job["folder"])
        brief = json.loads((folder / "agent.yaml").read_text())
        self.assertEqual((brief["name"], brief["tools"]["servers"]), ("adapter-builder", {}))
        for said in (str(folder / "graph.json"), str(folder / "sheet" / "ligands.smi"),
                     'name = "site-ligands"', "second column", "REMOVE"):
            self.assertIn(said, brief["task"])
        self.assertNotIn(str(DOCKING), json.dumps(brief))
        self.assertEqual((folder / "sheet" / "ligands.smi").read_text(),
                         (DOCKING / "ligands.smi").read_text())
        self.assertEqual(self.server.app.jobs[job["job"]].definition, folder / "agent.yaml")

    def test_an_approval_installs_the_file_the_judge_saw_under_the_approver(self):
        job = self.built()
        status, after = self.call("/api/install", {"job": job["job"], "actor": "qa.lead@example.org"})
        self.assertEqual(status, 200, after)
        self.assertEqual(self.states(after)["approval"], "done")
        self.assertFalse(after["can_install"])
        self.assertEqual((self.providers / "site_ligands.py").read_text(), ADAPTER)
        record = json.loads((self.providers / "site_ligands.approval.json").read_text())
        self.assertEqual((record["actor"], record["sha256"]),
                         ("qa.lead@example.org", job["verdict"]["sha256"]))
        local, = self.call("/api/state")[1]["local"]
        self.assertEqual((local["file"], local["name"], local["actor"], local["problem"]),
                         ("site_ligands.py", "site-ligands", "qa.lead@example.org", None))
        # Installed once: not again, and the name is taken for the next build.
        self.assertEqual(self.call("/api/install", {"job": job["job"], "actor": "x"})[0], 400)
        self.assertEqual(self.build()[0], 400)

    def test_an_approval_needs_a_name_and_the_file_as_judged(self):
        job = self.built()
        self.assertEqual(self.call("/api/install", {"job": job["job"], "actor": " "})[0], 400)
        self.assertEqual(self.call("/api/install", {"job": "absent", "actor": "x"})[0], 404)
        (Path(job["folder"]) / "work" / "adapter.py").write_text(ADAPTER + "\n# later\n")
        status, refused = self.call("/api/install", {"job": job["job"], "actor": "qa"})
        self.assertEqual(status, 400)
        self.assertIn("changed after the judge saw it", refused["error"])
        self.assertFalse(self.providers.exists())

    def test_a_changed_file_in_the_provider_folder_is_listed_as_refused(self):
        job = self.built()
        self.call("/api/install", {"job": job["job"], "actor": "qa.lead@example.org"})
        (self.providers / "site_ligands.py").write_text(ADAPTER + "\n# later\n")
        local, = self.call("/api/state")[1]["local"]
        self.assertIn("changed since qa.lead@example.org approved it", local["problem"])

    def test_an_adapter_the_judge_fails_cannot_be_installed(self):
        self.server.app.launch = writes(ADAPTER.replace("if value is not None and value not in ids:",
                                                        "if False:"))
        job = self.built()
        self.assertFalse(job["verdict"]["passed"])
        self.assertEqual((self.states(job)["judge"], self.states(job)["approval"]), ("failed", "skipped"))
        self.assertFalse(job["can_install"])
        status, refused = self.call("/api/install", {"job": job["job"], "actor": "qa"})
        self.assertEqual(status, 400)
        self.assertIn("did not pass", refused["error"])

    def test_an_agent_that_writes_no_adapter_is_a_failed_build(self):
        self.server.app.launch = writes(adapter=None)
        job = self.built()
        self.assertEqual((job["state"], self.states(job)["adapter"]), ("failed", "failed"))
        self.assertIn("wrote no adapter", job["error"])
        self.assertIsNone(job["verdict"])

    def test_a_link_left_as_the_adapter_is_not_followed(self):
        self.server.app.launch = writes(linked=DOCKING / "ligands.smi")
        job = self.built()
        self.assertEqual(job["state"], "failed")
        self.assertIsNone(job["code"])

    def test_a_brief_is_checked_before_any_job_starts(self):
        for bad in ({"name": "Site Ligands"}, {"name": "sarek"}, {"kind": "two words"},
                    {"sheet": "/no/such/file"}, {"run": "absent"}, {"path": self.tmp.name},
                    {"notes": "x" * 2001}):
            status, refused = self.build(**bad)
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.server.app.jobs, {})

    def test_a_build_and_a_incident_do_not_run_together(self):
        gate = threading.Event()
        self.server.app.launch = lambda job, home, roots, definition: gate.wait(5)
        first = self.build()[1]["job"]
        self.assertEqual(self.build(name="other")[0], 409)
        self.assertEqual(self.call("/api/run", {"path": str(RUNS), "run": "horus_run",
                                                "incident": "n"})[0], 409)
        gate.set()
        self.finished(first)

    def test_a_sheet_is_picked_as_a_file(self):
        from unittest import mock
        with mock.patch.object(ui.sys, "platform", "darwin"):
            self.assertIn("choose file", " ".join(ui.picker("/x", file=True)))
            self.assertIn("choose folder", " ".join(ui.picker("/x")))
        for chosen, found in ((DOCKING / "ligands.smi", str(DOCKING / "ligands.smi")), (DOCKING, None)):
            done = mock.Mock(stdout=f"{chosen}\n", returncode=0)
            with mock.patch.object(ui, "picker", lambda start, file=False: ["x"]), \
                    mock.patch.object(ui.subprocess, "run", return_value=done):
                self.assertEqual(ui.pick("/", file=True)["path"], found)
