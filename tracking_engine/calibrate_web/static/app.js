"use strict";

const state = {
  cameras: [],
  planMeta: null,
  planImage: null,
  overlay: null,
  positions: [],
  selectedPositionId: null,
  snapshots: {},
  computeResult: null,
  computeInFlight: false,
};

const $ = (sel) => document.querySelector(sel);
const LOUPE_ZOOM = 4;

function uuid() {
  return "L-" + Math.random().toString(36).slice(2, 10);
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

function planImageSize() {
  return {
    w: state.planImage.naturalWidth || state.planMeta.plan_width,
    h: state.planImage.naturalHeight || state.planMeta.plan_height,
  };
}

function canvasToPlanPx(cx, cy, canvas) {
  const { w, h } = planImageSize();
  return [
    (cx / canvas.width) * w,
    (cy / canvas.height) * h,
  ];
}

function planPxToCanvas(px, py, canvas) {
  const { w, h } = planImageSize();
  return [
    (px / w) * canvas.width,
    (py / h) * canvas.height,
  ];
}

function showLoupe(sourceCanvas, sx, sy, clientX, clientY) {
  const loupe = $("#loupe");
  const lc = $("#loupe-canvas");
  if (!sourceCanvas || !loupe || !lc) return;
  const ctx = lc.getContext("2d");
  const half = 60;
  const sw = sourceCanvas.width || sourceCanvas.naturalWidth;
  const sh = sourceCanvas.height || sourceCanvas.naturalHeight;
  if (!sw || !sh) return;
  ctx.clearRect(0, 0, lc.width, lc.height);
  const srcSize = Math.max(8, Math.min(sw, sh) / LOUPE_ZOOM);
  const sx0 = Math.max(0, Math.min(sw - srcSize, sx - srcSize / 2));
  const sy0 = Math.max(0, Math.min(sh - srcSize, sy - srcSize / 2));
  ctx.drawImage(sourceCanvas, sx0, sy0, srcSize, srcSize, 0, 0, lc.width, lc.height);
  ctx.strokeStyle = "#3aa6ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(lc.width / 2, 0);
  ctx.lineTo(lc.width / 2, lc.height);
  ctx.moveTo(0, lc.height / 2);
  ctx.lineTo(lc.width, lc.height / 2);
  ctx.stroke();
  loupe.style.left = Math.min(window.innerWidth - 140, clientX + 16) + "px";
  loupe.style.top = Math.min(window.innerHeight - 140, clientY + 16) + "px";
  loupe.hidden = false;
}

function hideLoupe() {
  const loupe = $("#loupe");
  if (loupe) loupe.hidden = true;
}

async function loadCameras() {
  const data = await api("GET", "/api/cameras");
  state.cameras = data.cameras;
  renderCameraGrid();
  renderSummary();
}

async function loadPlan() {
  state.planMeta = await api("GET", "/api/maro/floorplan/meta");
  state.overlay = await api("GET", "/api/maro/floorplan/overlay.json");
  state.planImage = new Image();
  state.planImage.onload = () => drawFloorCanvas();
  state.planImage.src = "/api/maro/floorplan/bg?ts=" + Date.now();
}

function ensureCanvasSize() {
  const canvas = $("#floor-canvas");
  const img = state.planImage;
  if (!img || !img.naturalWidth) return;
  const wrap = $("#floor-wrap");
  const maxW = wrap.clientWidth - 4;
  const maxH = wrap.clientHeight - 4;
  const scale = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1.0);
  canvas.width = Math.max(50, Math.floor(img.naturalWidth * scale));
  canvas.height = Math.max(50, Math.floor(img.naturalHeight * scale));
}

function drawFloorCanvas() {
  const canvas = $("#floor-canvas");
  if (!canvas || !state.planImage || !state.planImage.naturalWidth) return;
  ensureCanvasSize();
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.planImage, 0, 0, canvas.width, canvas.height);

  const ov = state.overlay || {};
  for (const z of ov.zones || []) {
    const poly = z.polygon_px || [];
    if (poly.length < 3) continue;
    ctx.beginPath();
    poly.forEach((p, i) => {
      const [x, y] = planPxToCanvas(p[0], p[1], canvas);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.fillStyle = "rgba(80, 120, 200, 0.09)";
    ctx.fill();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "rgba(200, 200, 220, 0.28)";
    ctx.stroke();
    ctx.setLineDash([]);
  }
  for (const st of ov.strips || []) {
    const pts = st.points_px || [];
    if (pts.length < 2) continue;
    ctx.beginPath();
    pts.forEach((p, i) => {
      const [x, y] = planPxToCanvas(p[0], p[1], canvas);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "rgba(255, 200, 80, 0.5)";
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  for (const s of ov.spots || []) {
    const [x, y] = planPxToCanvas(s.x_px, s.y_px, canvas);
    const r = Math.max(2, (s.r_px / planImageSize().w) * canvas.width);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255, 220, 100, 0.35)";
    ctx.fill();
  }

  for (const pos of state.positions) {
    const [xpx, ypx] = pos.world_xy_plan_px;
    const [x, y] = planPxToCanvas(xpx, ypx, canvas);
    const isSelected = pos.id === state.selectedPositionId;
    const stat = state.computeResult
      ? (state.computeResult.positions || []).find((p) => p.id === pos.id)
      : null;
    const camRes = state.computeResult && state.computeResult.cameras
      ? Object.values(state.computeResult.cameras).find(
          (c) => c.worst_position_id === pos.id
        )
      : null;
    let color = "#3aa6ff";
    if (camRes) color = "#e74c3c";
    else if (stat && stat.cross_camera_disagreement_px != null) {
      const d = stat.cross_camera_disagreement_px;
      color = d > 250 ? "#e74c3c" : d > 100 ? "#f0ad4e" : "#38c172";
    }
    ctx.beginPath();
    ctx.arc(x, y, isSelected ? 9 : 6, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = isSelected ? 3 : 1.5;
    ctx.strokeStyle = isSelected ? "#fff" : "rgba(0,0,0,0.6)";
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = "bold 11px system-ui";
    ctx.fillText(String(state.positions.indexOf(pos) + 1), x + 11, y + 4);
  }
}

function floorCanvasClick(ev) {
  const canvas = $("#floor-canvas");
  if (!state.planImage || !state.planMeta) return;
  const rect = canvas.getBoundingClientRect();
  const cx = ev.clientX - rect.left;
  const cy = ev.clientY - rect.top;
  const [u, v] = canvasToPlanPx(cx, cy, canvas);

  for (const pos of state.positions) {
    const [px, py] = planPxToCanvas(pos.world_xy_plan_px[0], pos.world_xy_plan_px[1], canvas);
    if (Math.hypot(px - cx, py - cy) < 14) {
      selectPosition(pos.id);
      return;
    }
  }

  const id = uuid();
  state.positions.push({
    id,
    world_xy_plan_px: [Math.round(u), Math.round(v)],
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
  if (pos) {
    $("#sel-name").textContent =
      `#${state.positions.indexOf(pos) + 1} (${pos.world_xy_plan_px[0]}, ${pos.world_xy_plan_px[1]} px)`;
  } else {
    $("#sel-name").textContent = "none";
  }
}

function deleteSelectedPosition() {
  const id = state.selectedPositionId;
  if (!id) return;
  state.positions = state.positions.filter((p) => p.id !== id);
  state.selectedPositionId = state.positions.length
    ? state.positions[state.positions.length - 1].id
    : null;
  selectPosition(state.selectedPositionId);
  markDirty();
}

function renderPositionList() {
  const ul = $("#position-list");
  ul.innerHTML = "";
  state.positions.forEach((pos, i) => {
    const li = document.createElement("li");
    if (pos.id === state.selectedPositionId) li.classList.add("selected");
    li.addEventListener("click", () => selectPosition(pos.id));
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = "#" + (i + 1);
    const xy = document.createElement("span");
    xy.textContent = `(${pos.world_xy_plan_px[0]}, ${pos.world_xy_plan_px[1]}) px`;
    const camCount = document.createElement("span");
    camCount.className = "cam-count";
    const n = Object.keys(pos.clicks).length;
    camCount.textContent = `${n} cam${n === 1 ? "" : "s"}`;
    const disagree = document.createElement("span");
    disagree.className = "disagree";
    const stat = state.computeResult
      ? (state.computeResult.positions || []).find((p) => p.id === pos.id)
      : null;
    if (stat && stat.cross_camera_disagreement_px != null) {
      const d = stat.cross_camera_disagreement_px;
      disagree.textContent = `Δ ${d.toFixed(0)} px`;
      disagree.classList.add(d > 250 ? "bad" : d > 100 ? "warn" : "good");
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
    const click = pos ? pos.clicks[cam.name] : null;
    const notVisible = pos ? !!pos.notVisible[cam.name] : false;
    if (notVisible) tile.classList.add("not-visible");

    const header = document.createElement("div");
    header.className = "header";
    const nameBox = document.createElement("div");
    nameBox.innerHTML = `<span class="name">${cam.name}</span><span class="room"> · ${cam.room || "?"}</span>`;
    const resid = document.createElement("span");
    resid.className = "resid";
    const cr = state.computeResult && state.computeResult.cameras
      ? state.computeResult.cameras[cam.name]
      : null;
    if (cr && cr.ok) {
      const green = cr.green;
      resid.textContent = green
        ? `✓ mean ${cr.residual_px_mean}px · n=${cr.num_points}`
        : `mean ${cr.residual_px_mean} / max ${cr.residual_px_max} px · n=${cr.num_points}`;
      resid.classList.add(green ? "good" : cr.residual_px_mean > 8 ? "bad" : "warn");
    } else if (cr) {
      resid.textContent = cr.error || "no fit";
      resid.classList.add("bad");
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
      const isWorst =
        cr && cr.worst_position_id && pos && cr.worst_position_id === pos.id;
      frame.addEventListener("mousemove", (ev) => {
        const ir = img.getBoundingClientRect();
        const cx = ev.clientX - ir.left;
        const cy = ev.clientY - ir.top;
        if (cx < 0 || cy < 0 || cx > ir.width || cy > ir.height) {
          hideLoupe();
          return;
        }
        const u = (cx / ir.width) * Number(img.dataset.w);
        const v = (cy / ir.height) * Number(img.dataset.h);
        const off = document.createElement("canvas");
        off.width = Number(img.dataset.w);
        off.height = Number(img.dataset.h);
        const octx = off.getContext("2d");
        octx.drawImage(img, 0, 0);
        showLoupe(off, u, v, ev.clientX, ev.clientY);
      });
      frame.addEventListener("mouseleave", hideLoupe);
      frame.addEventListener("click", (ev) => {
        if (!pos) {
          toast("Add or select a landmark on the plan first.", "warn");
          return;
        }
        if (notVisible) {
          toast("Mark visible first.", "warn");
          return;
        }
        const ir = img.getBoundingClientRect();
        const u = ((ev.clientX - ir.left) / ir.width) * Number(img.dataset.w);
        const v = ((ev.clientY - ir.top) / ir.height) * Number(img.dataset.h);
        pos.clicks[cam.name] = [Math.round(u), Math.round(v)];
        delete pos.notVisible[cam.name];
        markDirty();
      });
      if (click) {
        const overlay = document.createElement("div");
        overlay.className = "overlay";
        frame.appendChild(overlay);
        requestAnimationFrame(() => {
          const ir = img.getBoundingClientRect();
          const fr = frame.getBoundingClientRect();
          const px =
            ir.left - fr.left + (click[0] / Number(img.dataset.w)) * ir.width;
          const py =
            ir.top - fr.top + (click[1] / Number(img.dataset.h)) * ir.height;
          const col = isWorst ? "#e74c3c" : "#3aa6ff";
          overlay.innerHTML =
            `<svg width="100%" height="100%" style="position:absolute;inset:0">` +
            `<circle cx="${px}" cy="${py}" r="6" fill="${col}" stroke="#fff" stroke-width="2"/>` +
            `</svg>`;
        });
      }
    } else {
      const ph = document.createElement("div");
      ph.className = "no-frame";
      ph.textContent = snap && snap.error
        ? snap.error
        : 'Tap "Recapture all" for live frames';
      frame.appendChild(ph);
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
      } else delete pos.notVisible[cam.name];
      markDirty();
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
    });
    controls.append(visLabel, clearBtn);
    tile.append(header, frame, controls);
    grid.appendChild(tile);
  }
}

function renderSummary() {
  const summary = $("#summary");
  summary.innerHTML = "";
  for (const cam of state.cameras) {
    const cr = state.computeResult && state.computeResult.cameras
      ? state.computeResult.cameras[cam.name]
      : null;
    let kind = "";
    let extra = cam.calib_status === "homography" ? " · prev calib" : "";
    if (cr) {
      if (!cr.ok) {
        kind = "bad";
        extra = ` · ${cr.error || "no fit"}`;
      } else {
        kind = cr.green ? "good" : cr.residual_px_mean > 8 ? "bad" : "warn";
        extra = ` · n=${cr.num_points} · mean ${cr.residual_px_mean}px`;
      }
    }
    const badge = document.createElement("span");
    badge.className = "badge " + kind;
    badge.innerHTML = `<span class="pip"></span><span class="name">${cam.name}</span><span>${extra}</span>`;
    summary.appendChild(badge);
  }
}

function buildPayload() {
  return {
    positions: state.positions.map((p) => ({
      id: p.id,
      world_xy_plan_px: p.world_xy_plan_px,
      clicks: { ...p.clicks },
    })),
  };
}

let computeTimer = null;
function markDirty() {
  if (computeTimer) clearTimeout(computeTimer);
  computeTimer = setTimeout(() => {
    if (canCompute()) {
      doCompute().catch((e) => toast("Recompute: " + e.message, "bad"));
    }
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
    if (!body.positions.length) {
      state.computeResult = null;
      renderSummary();
      renderPositionList();
      renderCameraGrid();
      return;
    }
    state.computeResult = await api("POST", "/api/compute", body);
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
    toast("Need at least 4 landmarks on one camera.", "warn");
    return;
  }
  const errors = state.computeResult.errors || [];
  const warnings = state.computeResult.warnings || [];
  let force = false;
  if (errors.length) {
    if (!confirm("Errors:\n\n" + errors.join("\n") + "\n\nSave anyway?")) return;
    force = true;
  } else if (warnings.length) {
    if (!confirm("Warnings:\n\n" + warnings.join("\n") + "\n\nProceed?")) return;
  }
  const body = buildPayload();
  body.force = force;
  try {
    const result = await api("POST", "/api/save", body);
    toast("Saved: " + (result.written || []).join(", "), "good");
    state.computeResult = result;
    await loadCameras();
    renderSummary();
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
    toast(`Recaptured ${ok}/${Object.keys(state.snapshots).length} cameras`, ok ? "good" : "warn");
  } catch (e) {
    toast("Recapture failed: " + e.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "Recapture all";
  }
}

function downloadSession() {
  const blob = new Blob(
    [JSON.stringify({
      version: 2,
      coordinate_space: "maro_floorplan_px",
      saved_at: new Date().toISOString(),
      plan_meta: state.planMeta,
      cameras: state.cameras.map((c) => c.name),
      positions: state.positions,
    }, null, 2)],
    { type: "application/json" }
  );
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `maro-calibration-${Date.now()}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function loadSessionFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      state.positions = (data.positions || []).map((p) => ({
        id: p.id || uuid(),
        world_xy_plan_px: p.world_xy_plan_px || p.world_xy_mm || [0, 0],
        clicks: p.clicks || {},
        notVisible: p.notVisible || {},
      }));
      state.selectedPositionId = state.positions[0]?.id || null;
      selectPosition(state.selectedPositionId);
      toast(`Loaded ${state.positions.length} landmarks`, "good");
      markDirty();
    } catch (e) {
      toast("Load failed: " + e.message, "bad");
    }
  };
  reader.readAsText(file);
}

function attachEvents() {
  $("#floor-canvas").addEventListener("click", floorCanvasClick);
  $("#floor-canvas").addEventListener("mousemove", (ev) => {
    const canvas = $("#floor-canvas");
    const rect = canvas.getBoundingClientRect();
    showLoupe(canvas, ev.clientX - rect.left, ev.clientY - rect.top, ev.clientX, ev.clientY);
  });
  $("#floor-canvas").addEventListener("mouseleave", hideLoupe);
  $("#btn-recapture").addEventListener("click", doRecapture);
  $("#btn-compute").addEventListener("click", () => doCompute().catch((e) => toast(e.message, "bad")));
  $("#btn-save").addEventListener("click", doSave);
  $("#btn-download").addEventListener("click", downloadSession);
  $("#btn-add-position").addEventListener("click", () =>
    toast("Click a landmark on the Maro plan.", "")
  );
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
    await loadPlan();
    doRecapture().catch(() => {});
  } catch (e) {
    toast("Startup failed: " + e.message, "bad");
  }
}

document.addEventListener("DOMContentLoaded", boot);
