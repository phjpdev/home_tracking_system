"""FastAPI enrollment + identity management UI.

Backs the same gallery/face stack as the CLI in ``tracking_engine/enroll.py``.

Endpoints:

    GET  /                        — HTML form
    GET  /identities              — JSON list of enrolled identities
    POST /enroll                  — form-data: camera, name, frames, consent_basis
    POST /identities/{id}/revoke  — revoke consent
    POST /identities/{id}/delete  — hard delete (GDPR erasure)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import FastAPI, Form, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "fastapi + uvicorn are required for the enrollment UI:\n"
        "  pip install 'fastapi>=0.110' 'uvicorn[standard]>=0.27'"
    ) from exc

from ..enroll import run as enroll_run
from ..reid.config import ReidConfig
from ..reid.gallery_sqlite import GallerySqliteFaiss


_CONFIG_PATH = Path(os.environ.get(
    "TRACKING_CONFIG",
    "tracking_engine/config.multi_camera.yaml",
))


app = FastAPI(title="Tracking System Enrollment")


def _load_gallery() -> GallerySqliteFaiss:
    import yaml

    with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    rcfg = ReidConfig.from_cfg(cfg, _CONFIG_PATH.parent)
    return GallerySqliteFaiss(rcfg)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)


@app.get("/identities")
def list_identities() -> JSONResponse:
    g = _load_gallery()
    try:
        return JSONResponse(g.list_identities())
    finally:
        g.close()


@app.post("/enroll")
def enroll(
    name: str = Form(...),
    camera: str = Form(...),
    frames: int = Form(5),
    consent_basis: str = Form("consent"),
    retention_days: Optional[int] = Form(None),
) -> JSONResponse:
    if not name.strip():
        raise HTTPException(status_code=400, detail="name is required")

    args = argparse.Namespace(
        config=_CONFIG_PATH,
        camera=camera,
        name=name.strip(),
        frames=int(frames),
        consent_basis=consent_basis,
        retention_days=retention_days,
        video=None,
        timeout_sec=60.0,
    )
    rc = enroll_run(args)
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"enrollment exit code {rc}")
    return JSONResponse({"status": "ok"})


@app.post("/identities/{identity_id}/revoke")
def revoke(identity_id: str) -> JSONResponse:
    g = _load_gallery()
    try:
        g.revoke_consent(identity_id)
        return JSONResponse({"status": "revoked"})
    finally:
        g.close()


@app.post("/identities/{identity_id}/delete")
def delete(identity_id: str) -> JSONResponse:
    g = _load_gallery()
    try:
        info = g.delete_identity(identity_id)
        return JSONResponse({"status": "deleted", **info})
    finally:
        g.close()


_INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Tracking system — enrollment</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 720px; padding: 0 1rem; }
    label { display: block; margin: 0.6rem 0 0.2rem; font-weight: 600; }
    input, select { font-size: 1rem; padding: 0.4rem; width: 100%; max-width: 18rem; }
    button { font-size: 1rem; padding: 0.5rem 1rem; margin-top: 1rem; cursor: pointer; }
    table { border-collapse: collapse; width: 100%; margin-top: 2rem; }
    th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }
    .danger { color: #b00; }
  </style>
</head>
<body>
  <h1>Enroll a new identity</h1>
  <p>Capture 5 frontal face frames from a live camera and link them to a
  display name. GDPR consent record is created automatically.</p>

  <form id="enroll-form">
    <label>Display name</label>
    <input name="name" required />
    <label>Camera (layout name)</label>
    <input name="camera" placeholder="cam_kwz_sw" required />
    <label>Frames to capture</label>
    <input name="frames" type="number" value="5" min="1" max="20" />
    <label>Consent basis</label>
    <select name="consent_basis">
      <option value="consent" selected>consent</option>
      <option value="legitimate_interest">legitimate_interest</option>
      <option value="contract">contract</option>
    </select>
    <label>Retention days (optional)</label>
    <input name="retention_days" type="number" min="1" />
    <button type="submit">Start enrollment</button>
  </form>
  <pre id="enroll-out" style="background:#f6f6f6;padding:0.6rem;margin-top:1rem;"></pre>

  <h2>Existing identities</h2>
  <table id="identities">
    <thead><tr>
      <th>Name</th><th>ID</th><th>Faces</th><th>Consent</th><th>Actions</th>
    </tr></thead>
    <tbody></tbody>
  </table>

  <script>
    async function refresh() {
      const r = await fetch('/identities');
      const rows = await r.json();
      const tbody = document.querySelector('#identities tbody');
      tbody.innerHTML = '';
      for (const row of rows) {
        const tr = document.createElement('tr');
        const revoked = row.revoked_at ? 'REVOKED' : (row.lawful_basis || '—');
        tr.innerHTML = `
          <td>${row.display_name || ''}</td>
          <td><code>${row.identity_id}</code></td>
          <td>${row.face_count}</td>
          <td>${revoked}</td>
          <td>
            <button onclick="revoke('${row.identity_id}')">Revoke</button>
            <button class="danger" onclick="del('${row.identity_id}')">Delete</button>
          </td>`;
        tbody.appendChild(tr);
      }
    }

    async function revoke(id) {
      if (!confirm('Revoke consent for ' + id + '?')) return;
      await fetch('/identities/' + id + '/revoke', {method:'POST'});
      refresh();
    }
    async function del(id) {
      if (!confirm('Hard-delete ' + id + '? This cannot be undone.')) return;
      const r = await fetch('/identities/' + id + '/delete', {method:'POST'});
      alert(JSON.stringify(await r.json()));
      refresh();
    }

    document.querySelector('#enroll-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const out = document.querySelector('#enroll-out');
      out.textContent = 'Starting enrollment, look at the camera...';
      const r = await fetch('/enroll', {method:'POST', body: fd});
      out.textContent = await r.text();
      refresh();
    });

    refresh();
  </script>
</body>
</html>
"""
