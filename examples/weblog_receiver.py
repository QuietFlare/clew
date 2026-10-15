"""
Receive Nextflow weblog events and append each one as a line to a file.

The nf-weblog plugin posts one JSON object per event. This writes them
to the path given, which is what `clew extract nextflow-work --jsonl`
reads. Usage: python examples/weblog_receiver.py weblog.jsonl [port]
"""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def main(argv):
    out = argv[0] if argv else "weblog.jsonl"
    port = int(argv[1]) if len(argv) > 1 else 8765

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with open(out, "ab") as f:
                f.write(body + b"\n")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    print(f"writing weblog events to {out}; url http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
