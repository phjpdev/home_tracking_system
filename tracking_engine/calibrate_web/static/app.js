"use strict";

const state = {
  cameras: [],
  floorMeta: null,
  floorImage: null,
  positions: [],
  selectedPositionId: null,
  snapshots: {},
  computeResult: null,
  computeInFlight: false,
  computeDirty: false,
};

const $ = (sel) => document.querySelector(sel);

function uuid() {
  return "p-" + Math.random().toString(36).slice(2, 10);
}

function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (kind ? " " + kind : "");
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, 4500);
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  const text = await r.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch (_) {}
  if (!r.ok) {
    const detail = (json && (json.detail || json.reason)) || text || `HTTP ${r.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return json;
}

async function loadCameras() {
  const data = await api("GET", "/api/cameras");
  state.cameras = data.cameras;
  renderCameraGrid();
  renderSummary();
}

async function loadFloorMeta() {
  state.floorMeta = await api("GET", "/api/floor_plan_meta");
  state.floorImage = new Image();
  state.floorImage.onload = () => drawFloorCanvas();
  state.floorImage.src = "/api/floor_plan.png?ts=" + Date.now();
}

function ensureCanvasSize() {
  const canvas = $("#floor-canvas");
  const img = state.floorImage;
  if (!img || !img.naturalWidth) return;
  const wrap = $("#floor-wrap");
  const maxW = wrap.clientWidth - 4;
  const maxH = wrap.clientHeight - 4;
  const scale = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1.0);
  const w = Math.max(50, Math.floor(img.naturalWidth * scale));
  const h = Math.max(50, Math.floor(img.naturalHeight * scale));
  canvas.width = w;
  canvas.height = h;
}

function drawFloorCanvas() {
  const canvas = $("#floor-canvas");
  if (!canvas || !state.floorImage || !state.floorImage.naturalWidth) return;
  ensureCanvasSize();
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.floorImage, 0, 0, canvas.width, canvas.height);

  const sx = canvas.width / state.floorImage.naturalWidth;
  const sy = canvas.height / state.floorImage.naturalHeight;
  const envW = state.floorMeta.envelope_mm[0];
  const envH = state.floorMeta.envelope_mm[1];
  const imgW = state.floorImage.naturalWidth;
  const imgH = state.floorImage.naturalHeight;

  for (const pos of state.positions) {
    const xpx = pos.world_xy_mm[0] / envW * imgW * sx;
    const ypx = pos.world_xy_mm[1] / envH * imgH * sy;
    const isSelected = pos.id === state.selectedPositionId;
    const stat = state.computeResult ? state.computeResult.positions.find((p) => p.id === pos.id) : null;
    let color = "#3aa6ff";
    if (stat && stat.cross_camera_disagreement_mm != null) {
      const d = stat.cross_camera_disagreement_mm;
      color = d > 250 ? "#e74c3c" : d > 100 ? "#f0ad4e" : "#38c172";
    }
    ctx.beginPath();
    ctx.arc(xpx, ypx, isSelected ? 9 : 6, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = isSelected ? 3 : 1.5;
    ctx.strokeStyle = isSelected ? "#fff" : "rgba(0,0,0,0.6)";
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = "bold 11px system-ui";
    ctx.textBaseline = "middle";
    const label = String(state.positions.indexOf(pos) + 1);
    ctx.fillText(label, xpx + 11, ypx);
  }
}

function floorCanvasClick(ev) {
  const canvas = $("#floor-canvas");
  if (!state.floorImage || !state.floorMeta || !state.floorMeta.envelope_mm) return;
  const rect = canvas.getBoundingClientRect();
  const cx = ev.clientX - rect.left;
  const cy = ev.clientY - rect.top;
  const imgW = state.floorImage.naturalWidth;
  const imgH = state.floorImage.naturalHeight;
  const u = cx / canvas.width * imgW;
  const v = cy / canvas.height * imgH;
  const envW = state.floorMeta.envelope_mm[0];
  const envH = state.floorMeta.envelope_mm[1];
  const xMm = u / imgW * envW;
  const yMm = v / imgH * envH;

  for (const pos of state.positions) {
    const px = pos.world_xy_mm[0] / envW * imgW;
    const py = pos.world_xy_mm[1] / envH * imgH;
    const distPx = Math.hypot(px - u, py - v);
    if (distPx < 14) {
      selectPosition(pos.id);
      return;
    }
  }

  const id = uuid();
  state.positions.push({
    id,
    world_xy_mm: [Math.round(xMm), Math.round(yMm)],
    clicks: {},
    notVisible: {},
  });
  selectPosition(id);
  markDirty();
}

function selectPosition(id) {
  state.selectedPositionId = id;
  renderPositionList();
  renderCameraGrid();
  drawFloorCanvas();
  const pos = state.positions.find((p) => p.id === id);
  $("#sel-name").textContent = pos ? `#${state.positions.indexOf(pos) + 1} (${pos.world_xy_mm[0]}, ${pos.world_xy_mm[1]} mm)` : "none";
}

function deleteSelectedPosition() {
  const id = state.selectedPositionId;
  if (!id) return;
  state.positions = state.positions.filter((p) => p.id !== id);
  state.selectedPositionId = state.positions.length ? state.positions[state.positions.length - 1].id : null;
  selectPosition(state.selectedPositionId);
  markDirty();
}

function renderPositionList() {
  const ul = $("#position-list");
  ul.innerHTML = "";
  state.positions.forEach((pos, i) => {
    const li = document.createElement("li");
    if (pos.id === state.selectedPositionId) li.classList.add("selected");
    li.dataset.id = pos.id;
    li.addEventListener("click", () => selectPosition(pos.id));

    const num = document.createElement("span");
    num.className = "num";
    num.textContent = "#" + (i + 1);

    const xy = document.createElement("span");
    xy.textContent = `(${pos.world_xy_mm[0]}, ${pos.world_xy_mm[1]}) mm`;

    const camCount = document.createElement("span");
    camCount.className = "cam-count";
    const nClicks = Object.keys(pos.clicks).length;
    camCount.textContent = `${nClicks} cam${nClicks === 1 ? "" : "s"}`;

    const disagree = document.createElement("span");
    disagree.className = "disagree";
    const stat = state.computeResult ? state.computeResult.positions.find((p) => p.id === pos.id) : null;
    if (stat && stat.cross_camera_disagreement_mm != null) {
      const d = stat.cross_camera_disagreement_mm;
      disagree.textContent = `Δ ${d.toFixed(0)} mm`;
      disagree.classList.add(d > 250 ? "bad" : d > 100 ? "warn" : "good");
    } else {
      disagree.textContent = nClicks >= 2 ? "Δ —" : "";
    }

    li.append(num, xy, camCount, disagree);
    ul.appendChild(li);
  });
}

function renderCameraGrid() {
  const grid = $("#cam-grid");
  grid.innerHTML = "";
  const pos = state.positions.find((p) => p.id === state.selectedPositionId);

  for (const cam of state.cameras) {
    const tile = document.createElement("div");
    tile.className = "cam-tile";
    tile.dataset.cam = cam.name;

    const click = pos ? pos.clicks[cam.name] : null;
    const notVisible = pos ? !!pos.notVisible[cam.name] : false;
    if (notVisible) tile.classList.add("not-visible");

    const header = document.createElement("div");
    header.className = "header";

    const nameBox = document.createElement("div");
    const nameSpan = document.createElement("span");
    nameSpan.className = "name";
    nameSpan.textContent = cam.name;
    const roomSpan = document.createElement("span");
    roomSpan.className = "room";
    roomSpan.textContent = " · " + (cam.room || "?");
    nameBox.append(nameSpan, roomSpan);

    const resid = document.createElement("span");
    resid.className = "resid";
    if (state.computeResult && state.computeResult.cameras[cam.name]) {
      const r = state.computeResult.cameras[cam.name];
      if (r.ok) {
        resid.textContent = `mean ${r.residual_mm_mean.toFixed(0)} / max ${r.residual_mm_max.toFixed(0)} mm · n=${r.num_points}`;
        const worst = Math.max(r.residual_mm_mean, r.residual_mm_max / 2);
        resid.classList.add(worst > 250 ? "bad" : worst > 100 ? "warn" : "good");
      } else {
        resid.textContent = r.error || "no fit";
        resid.classList.add("bad");
      }
    } else {
      resid.textContent = "—";
    }
    header.append(nameBox, resid);

    const frame = document.createElement("div");
    frame.className = "frame";

    const snap = state.snapshots[cam.name];
    if (snap && snap.ok && snap.url) {
      const img = document.createElement("img");
      img.src = snap.url;
      img.alt = cam.name;
      img.dataset.w = snap.w;
      img.dataset.h = snap.h;
      frame.appendChild(img);

      frame.addEventListener("click", (ev) => {
        if (!pos) {
          toast("Add (or select) a position on the floor plan first.", "warn");
          return;
        }
        if (notVisible) {
          toast("Mark this tile visible first.", "warn");
          return;
        }
        const r = frame.getBoundingClientRect();
        const imgRect = img.getBoundingClientRect();
        const cx = ev.clientX - imgRect.left;
        const cy = ev.clientY - imgRect.top;
        if (cx < 0 || cy < 0 || cx > imgRect.width || cy > imgRect.height) return;
        const u = cx / imgRect.width * Number(img.dataset.w);
        const v = cy / imgRect.height * Number(img.dataset.h);
        pos.clicks[cam.name] = [Math.round(u), Math.round(v)];
        delete pos.notVisible[cam.name];
        markDirty();
        renderPositionList();
        renderCameraGrid();
      });

      if (click) {
        const overlay = document.createElement("div");
        overlay.className = "overlay";
        frame.appendChild(overlay);
        requestAnimationFrame(() => {
          const imgRect = img.getBoundingClientRect();
          const frameRect = frame.getBoundingClientRect();
          const px = imgRect.left - frameRect.left + click[0] / Number(img.dataset.w) * imgRect.width;
          const py = imgRect.top - frameRect.top + click[1] / Number(img.dataset.h) * imgRect.height;
          overlay.innerHTML =
            `<svg width="100%" height="100%" style="position:absolute;inset:0">` +
            `<circle cx="${px}" cy="${py}" r="6" fill="#3aa6ff" stroke="#fff" stroke-width="2"/>` +
            `<circle cx="${px}" cy="${py}" r="12" fill="none" stroke="#3aa6ff" stroke-width="1" opacity="0.6"/>` +
            `</svg>`;
        });
      }
    } else {
      const placeholder = document.createElement("div");
      placeholder.className = "no-frame";
      placeholder.textContent = snap && snap.error
        ? `no snapshot: ${snap.error}\nTap "Recapture all"`
        : "no snapshot yet — tap \"Recapture all\"";
      frame.appendChild(placeholder);
    }

    const controls = document.createElement("div");
    controls.className = "controls";

    const visLabel = document.createElement("label");
    const visCheck = document.createElement("input");
    visCheck.type = "checkbox";
    visCheck.checked = notVisible;
    visCheck.disabled = !pos;
    visCheck.addEventListener("change", () => {
      if (!pos) return;
      if (visCheck.checked) {
        pos.notVisible[cam.name] = true;
        delete pos.clicks[cam.name];
      } else {
        delete pos.notVisible[cam.name];
      }
      markDirty();
      renderPositionList();
      renderCameraGrid();
    });
    visLabel.append(visCheck, document.createTextNode(" not visible"));

    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.textContent = "Clear click";
    clearBtn.disabled = !pos || !click;
    clearBtn.addEventListener("click", () => {
      if (!pos) return;
      delete pos.clicks[cam.name];
      markDirty();
      renderPositionList();
      renderCameraGrid();
    });

    controls.append(visLabel, clearBtn);

    tile.append(header, frame, controls);
    grid.appendChild(tile);
  }
}

function renderSummary() {
  const summary = $("#summary");
  summary.innerHTML = "";

  const minRequired = 6;
  for (const cam of state.cameras) {
    let kind = "";
    let label = `${cam.name}`;
    let extra = "";
    if (state.computeResult && state.computeResult.cameras[cam.name]) {
      const r = state.computeResult.cameras[cam.name];
      if (!r.ok) {
        kind = "bad";
        extra = ` · ${r.error || "no fit"}`;
      } else {
        const worst = Math.max(r.residual_mm_mean, r.residual_mm_max / 2);
        kind = worst > 250 ? "bad" : worst > 100 ? "warn" : "good";
        if (r.num_points < minRequired) kind = kind === "good" ? "warn" : kind;
        extra = ` · n=${r.num_points} · mean ${r.residual_mm_mean.toFixed(0)} mm`;
      }
    } else {
      kind = cam.calib_status === "homography" ? "warn" : "";
      extra = cam.calib_status === "homography"
        ? " · previously calibrated (not yet recomputed)"
        : ` · ${cam.calib_status}`;
    }
    const badge = document.createElement("span");
    badge.className = "badge " + kind;
    badge.innerHTML = `<span class="pip"></span><span class="name">${label}</span><span>${extra}</span>`;
    summary.appendChild(badge);
  }

  if (state.computeResult) {
    if (state.computeResult.errors && state.computeResult.errors.length) {
      const b = document.createElement("span");
      b.className = "badge bad";
      b.innerHTML = `<span class="pip"></span><span class="name">errors</span><span>${state.computeResult.errors.length}</span>`;
      summary.appendChild(b);
    }
    if (state.computeResult.warnings && state.computeResult.warnings.length) {
      const b = document.createElement("span");
      b.className = "badge warn";
      b.innerHTML = `<span class="pip"></span><span class="name">warnings</span><span>${state.computeResult.warnings.length}</span>`;
      summary.appendChild(b);
    }
  }
}

function buildPayload() {
  return {
    positions: state.positions.map((p) => ({
      id: p.id,
      world_xy_mm: p.world_xy_mm,
      clicks: { ...p.clicks },
    })),
  };
}

function markDirty() {
  state.computeDirty = true;
  scheduleAutoCompute();
}

let computeTimer = null;
function scheduleAutoCompute() {
  if (computeTimer) clearTimeout(computeTimer);
  computeTimer = setTimeout(() => {
    if (canCompute()) doCompute().catch((e) => toast("Recompute failed: " + e.message, "bad"));
  }, 350);
}

function canCompute() {
  for (const cam of state.cameras) {
    let n = 0;
    for (const p of state.positions) if (p.clicks[cam.name]) n++;
    if (n >= 4) return true;
  }
  return false;
}

async function doCompute() {
  if (state.computeInFlight) return;
  state.computeInFlight = true;
  try {
    const body = buildPayload();
    if (body.positions.length === 0) {
      state.computeResult = null;
      renderSummary();
      renderPositionList();
      renderCameraGrid();
      return;
    }
    state.computeResult = await api("POST", "/api/compute", body);
    state.computeDirty = false;
    renderSummary();
    renderPositionList();
    renderCameraGrid();
    drawFloorCanvas();
  } finally {
    state.computeInFlight = false;
  }
}

async function doSave() {
  await doCompute();
  if (!state.computeResult) {
    toast("Add at least one camera with 4+ clicks before saving.", "warn");
    return;
  }
  const errors = state.computeResult.errors || [];
  const warnings = state.computeResult.warnings || [];

  let force = false;
  if (errors.length) {
    const ok = confirm(
      "Validation errors:\n\n" + errors.join("\n") +
      "\n\nSave anyway with force=true?"
    );
    if (!ok) return;
    force = true;
  } else if (warnings.length) {
    const ok = confirm("Warnings:\n\n" + warnings.join("\n") + "\n\nProceed with save?");
    if (!ok) return;
  }

  const body = buildPayload();
  body.force = force;
  try {
    const result = await api("POST", "/api/save", body);
    const written = (result.written || []).join(", ");
    toast(`Saved ${written}\n${result.calib_path}`, "good");
    state.computeResult = result;
    await loadCameras();
    renderSummary();
    renderPositionList();
    renderCameraGrid();
    drawFloorCanvas();
  } catch (e) {
    toast("Save failed: " + e.message, "bad");
  }
}

async function doRecapture() {
  const btn = $("#btn-recapture");
  btn.disabled = true;
  btn.textContent = "Capturing…";
  try {
    const r = await api("POST", "/api/snapshot_all");
    state.snapshots = r.snapshots || {};
    renderCameraGrid();
    const ok = Object.values(state.snapshots).filter((s) => s.ok).length;
    const total = Object.keys(state.snapshots).length;
    toast(`Recaptured ${ok}/${total} cameras`, ok === total ? "good" : "warn");
  } catch (e) {
    toast("Recapture failed: " + e.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "Recapture all";
  }
}

function downloadSession() {
  const data = {
    version: 1,
    saved_at: new Date().toISOString(),
    envelope_mm: state.floorMeta ? state.floorMeta.envelope_mm : null,
    cameras: state.cameras.map((c) => c.name),
    positions: state.positions,
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `calibration-session-${Date.now()}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function loadSessionFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      if (!Array.isArray(data.positions)) throw new Error("missing positions[]");
      state.positions = data.positions.map((p) => ({
        id: p.id || uuid(),
        world_xy_mm: p.world_xy_mm,
        clicks: p.clicks || {},
        notVisible: p.notVisible || {},
      }));
      state.selectedPositionId = state.positions.length ? state.positions[0].id : null;
      state.computeResult = null;
      renderPositionList();
      renderCameraGrid();
      renderSummary();
      drawFloorCanvas();
      toast(`Loaded ${state.positions.length} positions`, "good");
      scheduleAutoCompute();
    } catch (e) {
      toast("Load failed: " + e.message, "bad");
    }
  };
  reader.readAsText(file);
}

function attachEvents() {
  $("#floor-canvas").addEventListener("click", floorCanvasClick);
  $("#btn-recapture").addEventListener("click", doRecapture);
  $("#btn-compute").addEventListener("click", () => doCompute().catch((e) => toast(e.message, "bad")));
  $("#btn-save").addEventListener("click", doSave);
  $("#btn-download").addEventListener("click", downloadSession);
  $("#btn-add-position").addEventListener("click", () => toast("Click anywhere on the floor plan to add a position.", ""));
  $("#btn-delete-position").addEventListener("click", deleteSelectedPosition);
  $("#file-load").addEventListener("change", (ev) => {
    const f = ev.target.files && ev.target.files[0];
    if (f) loadSessionFile(f);
  });
  window.addEventListener("resize", () => drawFloorCanvas());
}

async function boot() {
  attachEvents();
  try {
    await loadCameras();
    await loadFloorMeta();
    doRecapture().catch(() => {});
  } catch (e) {
    toast("Startup failed: " + e.message, "bad");
  }
}

document.addEventListener("DOMContentLoaded", boot);
