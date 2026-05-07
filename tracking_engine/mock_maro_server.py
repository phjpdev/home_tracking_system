#!/usr/bin/env python3
"""Lightweight sink for ``POST /tracking/positions`` — prints JSON bodies to stdout."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


class Handler(BaseHTTPRequestHandler):
    server_version = "MockMaro/0.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/tracking/positions":
            self.send_error(404, "use POST /tracking/positions")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except json.JSONDecodeError as e:
            self.send_error(400, str(e))
            return
        self.send_response(204)
        self.end_headers()


def main() -> int:
    host = "0.0.0.0"
    port = 8765
    if len(sys.argv) >= 2:
        port = int(sys.argv[1])
    httpd = HTTPServer((host, port), Handler)
    sys.stderr.write(
        f"[mock_maro] listening on http://127.0.0.1:{port}/tracking/positions\n"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
