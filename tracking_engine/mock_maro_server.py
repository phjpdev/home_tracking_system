#!/usr/bin/env python3
"""Lightweight sink for ``POST /tracking/positions`` and ``POST /tracking/events``.

Prints incoming JSON bodies to stdout, one per line, prefixed by their endpoint
so the multi-camera and thermal pipelines can be exercised against the same
process during development.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


_VALID_PATHS = ("/tracking/positions", "/tracking/events")


_GET_HELP = """\
Mock Maro sink — this URL accepts POST only.

Endpoints:
  POST /tracking/positions   (per-tick position payloads)
  POST /tracking/events      (fall events, etc.)

Send JSON with Content-Type: application/json (same shape as the tracking engine POST body).

Example:

  curl -s -X POST http://127.0.0.1:8765/tracking/positions \\
    -H "Content-Type: application/json" \\
    -d '{"cam_id":"demo","ts":0,"persons":[]}'

Open this page in a browser to confirm the server is up; run the tracking pipeline to receive real payloads.
""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "MockMaro/0.2"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _normalized_path(self) -> str:
        return urlparse(self.path).path.rstrip("/") or "/"

    def do_GET(self) -> None:
        path = self._normalized_path()
        if path in _VALID_PATHS:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(_GET_HELP)))
            self.end_headers()
            self.wfile.write(_GET_HELP)
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404, "not found")

    def do_POST(self) -> None:
        path = self._normalized_path()
        if path not in _VALID_PATHS:
            self.send_error(404, f"use POST {_VALID_PATHS!r}")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            self.send_error(400, str(e))
            return

        kind = "positions" if path.endswith("positions") else "events"
        envelope = {"endpoint": kind, "payload": payload}
        print(json.dumps(envelope, ensure_ascii=False), flush=True)
        self.send_response(204)
        self.end_headers()


def main() -> int:
    host = "0.0.0.0"
    port = 8765
    if len(sys.argv) >= 2:
        port = int(sys.argv[1])
    httpd = HTTPServer((host, port), Handler)
    sys.stderr.write(
        f"[mock_maro] listening on http://127.0.0.1:{port}/tracking/positions "
        f"and /tracking/events\n"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
