"""
Reclaim over a bucket. A fake S3 answers the four calls the client makes,
list, head, get and multi-delete, over plain HTTP on a local port, so the
same verdicts and the same receipt come out of a prefix as out of a
directory, and --apply removes objects, not paths.
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.graph import s3
from clew.graph.storage import S3Tree, open_tree
from clew.questions import reclaim


class FakeS3(BaseHTTPRequestHandler):
    """Path-style: /bucket/key. Auth is checked for shape only."""
    objects = {}
    log = []

    def log_message(self, *args):
        pass

    def _key(self):
        parts = urlsplit(self.path)
        _, bucket, *rest = parts.path.split("/", 2)
        return bucket, unquote(rest[0]) if rest else "", parse_qs(parts.query, keep_blank_values=True)

    def _send(self, code, body=b"", size=None):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body) if size is None else size))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        assert self.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=")
        bucket, key, query = self._key()
        FakeS3.log.append(("GET", key or query.get("prefix", [""])[0]))
        if "list-type" in query:
            prefix = query.get("prefix", [""])[0]
            found = sorted(k for k in self.objects if k.startswith(prefix))
            body = ("<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>"
                    + "".join(f"<Contents><Key>{escape(k)}</Key><Size>{len(self.objects[k])}</Size></Contents>"
                              for k in found)
                    + "<IsTruncated>false</IsTruncated></ListBucketResult>").encode()
            return self._send(200, body)
        if key in self.objects:
            return self._send(200, self.objects[key])
        self._send(404, b"<Error><Code>NoSuchKey</Code></Error>")

    def do_HEAD(self):
        _, key, _ = self._key()
        FakeS3.log.append(("HEAD", key))
        if key in self.objects:
            return self._send(200, size=len(self.objects[key]))
        self._send(404)

    def do_POST(self):
        _, key, query = self._key()
        assert "delete" in query and self.headers["Content-MD5"]
        body = self.rfile.read(int(self.headers["Content-Length"]))
        for entry in ElementTree.fromstring(body).findall("Object"):
            FakeS3.log.append(("DELETE", entry.findtext("Key")))
            self.objects.pop(entry.findtext("Key"), None)
        self._send(200, b"<DeleteResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'/>")


def sha(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


class Bucket(unittest.TestCase):
    """
    work/aa/000001: ALIGN, s.bam consumed downstream, not published.
    work/cc/000003: REPORT, report.html published at results/out/report.html.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.env = {"AWS_ENDPOINT_URL": f"http://127.0.0.1:{cls.server.server_port}",
                   "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
                   "AWS_REGION": "eu-central-1"}
        cls.saved = {k: os.environ.get(k) for k in cls.env}
        os.environ.update(cls.env)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for key, value in cls.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        FakeS3.objects = {
            "runs/work/aa/000001/s.bam": b"x" * 300,
            "runs/work/aa/000001/.command.sh": b"echo",
            "runs/work/cc/000003/report.html": b"z" * 70,
            "runs/work/cc/000003/.command.sh": b"echo",
            "runs/results/out/report.html": b"z" * 70,
        }
        FakeS3.log = []
        self.graph = {
            "tasks": {
                "aa/000001": {"hash": "aa/000001", "process": "ALIGN", "name": "ALIGN",
                              "workdir": "s3://lab/runs/work/aa/000001", "workpath": "aa/000001",
                              "status": "COMPLETED", "script": "echo", "container": "img"},
                "cc/000003": {"hash": "cc/000003", "process": "REPORT", "name": "REPORT",
                              "workdir": "s3://lab/runs/work/cc/000003", "workpath": "cc/000003",
                              "status": "COMPLETED", "script": "echo", "container": "img"},
            },
            "edges": [{"consumer": "cc/000003", "producer": "aa/000001", "filename": "s.bam", "target": ""}],
            "outputs": {"aa/000001": ["s.bam"], "cc/000003": ["report.html"]},
            "output_details": {"aa/000001": [{"file": "s.bam", "size": 300, "digest": sha(b"x" * 300)}],
                               "cc/000003": [{"file": "report.html", "size": 70, "digest": sha(b"z" * 70)}]},
            "published": {"out/report.html": {"size": 70, "digest": sha(b"z" * 70)}},
        }
        self.work, self.results = "s3://lab/runs/work", "s3://lab/runs/results"

    def plan(self, **kw):
        self.reclaimer = reclaim.Reclaimer(self.graph, self.work, self.results, **kw)
        return {i["task"]: i for i in self.reclaimer.plan()}

    def test_a_prefix_opens_as_a_bucket_tree(self):
        tree = open_tree("s3://lab/runs/work")
        self.assertIsInstance(tree, S3Tree)
        self.assertEqual(tree.describe("aa/000001"), "s3://lab/runs/work/aa/000001")
        self.assertEqual(tree.inside("s3://lab/runs/work/aa/000001"), "aa/000001")
        self.assertIsNone(tree.inside("s3://lab/runs/results/out"))
        self.assertIsNone(tree.inside("s3://lab/runs/work"))

    def test_verdicts_match_a_local_tree(self):
        v = self.plan()
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.REDUNDANT)
        self.assertEqual(v["cc/000003"]["dir"], "s3://lab/runs/work/cc/000003")
        self.assertEqual(v["cc/000003"]["bytes"], 74)
        self.assertEqual(v["aa/000001"]["verdict"], reclaim.KEEP)
        self.assertEqual(v["aa/000001"]["cause"], "not published; --intermediates not given")
        self.assertEqual(self.reclaimer.warnings, [])

    def test_a_published_copy_of_another_size_withholds(self):
        FakeS3.objects["runs/results/out/report.html"] = b"z" * 71
        v = self.plan()
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.KEEP)
        self.assertIn("not the size that was digested", v["cc/000003"]["reason"])

    def test_apply_deletes_the_objects_and_writes_the_receipt_first(self):
        v = self.plan()
        receipt = Path(tempfile.mkdtemp()) / "r.jsonl"
        removed, refused = reclaim.apply(list(v.values()), self.reclaimer.work, receipt,
                                         {reclaim.REDUNDANT}, self.reclaimer.recheck)
        self.assertEqual((removed, refused), (1, []))
        self.assertNotIn("runs/work/cc/000003/report.html", FakeS3.objects)
        self.assertNotIn("runs/work/cc/000003/.command.sh", FakeS3.objects)
        self.assertIn("runs/work/aa/000001/s.bam", FakeS3.objects)
        self.assertIn("runs/results/out/report.html", FakeS3.objects)
        line = json.loads(receipt.read_text().splitlines()[0])
        self.assertEqual(line["dir"], "s3://lab/runs/work/cc/000003")
        self.assertIn("removed_at", line)
        deletes = [k for op, k in FakeS3.log if op == "DELETE"]
        self.assertEqual(sorted(deletes), ["runs/work/cc/000003/.command.sh",
                                           "runs/work/cc/000003/report.html"])

    def test_apply_re_hashes_the_published_object(self):
        v = self.plan()
        FakeS3.objects["runs/results/out/report.html"] = b"y" * 70
        removed, refused = reclaim.apply(list(v.values()), self.reclaimer.work,
                                         Path(tempfile.mkdtemp()) / "r.jsonl",
                                         {reclaim.REDUNDANT}, self.reclaimer.recheck)
        self.assertEqual(removed, 0)
        self.assertIn("changed since it was digested", refused[0][1])
        self.assertIn("runs/work/cc/000003/report.html", FakeS3.objects)

    def test_apply_refuses_a_directory_outside_the_prefix(self):
        v = self.plan()
        v["cc/000003"]["dir"] = "s3://lab/runs/results/out"
        with self.assertRaises(SystemExit):
            reclaim.apply(list(v.values()), self.reclaimer.work,
                          Path(tempfile.mkdtemp()) / "r.jsonl", {reclaim.REDUNDANT})
        self.assertIn("runs/results/out/report.html", FakeS3.objects)

    def test_a_wrong_prefix_unplaces_everything(self):
        self.work = "s3://lab/elsewhere"
        v = self.plan()
        self.assertTrue(all(i["verdict"] == reclaim.KEEP for i in v.values()))
        self.assertIn("root looks wrong", self.reclaimer.warnings[0])

    def test_the_command_takes_bucket_roots(self):
        import contextlib
        import io
        graph = Path(tempfile.mkdtemp()) / "graph.json"
        graph.write_text(json.dumps(self.graph))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            reclaim.main(["--graph", str(graph), "--work-root", self.work, "--results", self.results])
        self.assertIn("REDUNDANT  1  74 B", out.getvalue())
        self.assertNotIn("hard-linked", out.getvalue())


class Signing(unittest.TestCase):
    def test_credentials_come_from_the_environment_first(self):
        saved = {k: os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")}
        os.environ.update({"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s", "AWS_SESSION_TOKEN": "t"})
        try:
            self.assertEqual(s3.credentials(), ("k", "s", "t"))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_the_signature_is_the_documented_example(self):
        # The GET example from the SigV4 documentation for S3, byte for byte.
        client = s3.S3("examplebucket", region_name="us-east-1",
                       creds=("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", None))
        captured = {}

        class Opened:
            def __init__(self, request):
                captured["authorization"] = request.get_header("Authorization")
        import urllib.request
        original, s3.urllib.request.urlopen = s3.urllib.request.urlopen, Opened
        try:
            client.request("GET", "test.txt", "", headers={"Range": "bytes=0-9"})
        finally:
            s3.urllib.request.urlopen = original
        self.assertTrue(captured["authorization"].startswith(
            "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/"))
        self.assertIn("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date,", captured["authorization"])


if __name__ == "__main__":
    unittest.main()
