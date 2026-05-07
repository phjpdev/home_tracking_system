"""HTTP POST JSON batches to Maro / FastAPI (keep-alive session)."""

from __future__ import annotations

import json
from typing import Any

import requests


class PositionPoster:
    def __init__(self, url: str, timeout: float, dry_run: bool, verify_tls: bool = True):
        self.url = url
        self.timeout = timeout
        self.dry_run = dry_run
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._verify = verify_tls

    def post(self, payload: dict[str, Any]) -> tuple[bool, str]:
        if self.dry_run:
            return True, json.dumps(payload)

        try:
            r = self._session.post(
                self.url,
                data=json.dumps(payload),
                timeout=self.timeout,
                verify=self._verify,
            )
            if r.status_code >= 400:
                return False, f"HTTP {r.status_code}: {r.text[:200]}"
            return True, ""
        except requests.RequestException as e:
            return False, str(e)

    def close(self) -> None:
        self._session.close()
