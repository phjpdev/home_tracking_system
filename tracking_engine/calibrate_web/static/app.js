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
  // Line mode state
  mode: "point",                 // "point" | "line" | "led"
  lineN: 3,                      // number of landmarks per line (>=2)
  pendingPlanStart: null,        // [u,v] first plan click waiting for the second
  pendingLineGroup: null,        // {id, landmarkIds:[id,…]} after both plan clicks
  lineGroups: [],                // [{id, landmarkIds, planLine:[start,end]}]
  camPendingLineStart: {},       // { camName: [u,v] } — first cam click waiting for the second
  // LED marker overlay state
  ledOn: false,                  // whether the visual + physical markers are active
  ledStrips: [],                 // [{name, num_markers, markers:[{idx, mm, plan_px}], …}]
  ledLitPerStrip: {},            // { strip_name: [{idx, plan_px:[x,y]}, …] } — only LIT markers
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
  // Strip path lines — hidden when LED markers are active (markers replace the visualization)
  if (!state.ledOn) {
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
  }
  for (const s of ov.spots || []) {
    const [x, y] = planPxToCanvas(s.x_px, s.y_px, canvas);
    const r = Math.max(2, (s.r_px / planImageSize().w) * canvas.width);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255, 220, 100, 0.35)";
    ctx.fill();
  }

  // LED-marker clusters drawn as glowing line segments (one per lit run).
  // We only show start + end + line — the calibration uses those endpoints anyway.
  if (state.ledOn) {
    for (const [stripName, markers] of Object.entries(state.ledLitPerStrip || {})) {
      if (!markers || markers.length === 0) continue;
      // Group consecutive indices into clusters (gap > 1 starts new cluster)
      const clusters = [];
      let cur = [];
      for (const m of markers) {
        if (cur.length === 0 || m.idx === cur[cur.length - 1].idx + 1) cur.push(m);
        else { clusters.push(cur); cur = [m]; }
      }
      if (cur.length) clusters.push(cur);

      for (const cluster of clusters) {
        const start = cluster[0].plan_px;
        const end = cluster[cluster.length - 1].plan_px;
        const [x0, y0] = planPxToCanvas(start[0], start[1], canvas);
        const [x1, y1] = planPxToCanvas(end[0], end[1], canvas);

        // Outer glow line (wider, soft)
        ctx.beginPath();
        ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
        ctx.strokeStyle = "rgba(255, 240, 120, 0.22)";
        ctx.lineWidth = 9;
        ctx.lineCap = "round";
        ctx.stroke();

        // Core line
        ctx.beginPath();
        ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
        ctx.strokeStyle = "#fff0a0";
        ctx.lineWidth = 3;
        ctx.lineCap = "round";
        ctx.stroke();
      }
    }
  }

  // Lines between paired landmarks (line groups)
  for (const grp of state.lineGroups) {
    const pts = grp.landmarkIds
      .map((id) => state.positions.find((p) => p.id === id))
      .filter(Boolean);
    if (pts.length < 2) continue;
    ctx.beginPath();
    pts.forEach((p, i) => {
      const [x, y] = planPxToCanvas(p.world_xy_plan_px[0], p.world_xy_plan_px[1], canvas);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "rgba(80, 200, 255, 0.7)";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  // Pending line start indicator (waiting for the second click)
  if (state.pendingPlanStart) {
    const [x, y] = planPxToCanvas(state.pendingPlanStart[0], state.pendingPlanStart[1], canvas);
    ctx.beginPath();
    ctx.arc(x, y, 9, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(80, 200, 255, 0.7)";
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = "bold 10px system-ui";
    ctx.fillText("start", x + 12, y + 4);
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

  // Click on existing landmark → select it (works in both modes)
  for (const pos of state.positions) {
    const [px, py] = planPxToCanvas(pos.world_xy_plan_px[0], pos.world_xy_plan_px[1], canvas);
    if (Math.hypot(px - cx, py - cy) < 14) {
      selectPosition(pos.id);
      return;
    }
  }

  if (state.mode === "line") {
    handleLinePlanClick(u, v);
    return;
  }

  // Point mode — single landmark per click (original behaviour).
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

function handleLinePlanClick(u, v) {
  if (!state.pendingPlanStart) {
    // First click: store start, wait for second
    state.pendingPlanStart = [Math.round(u), Math.round(v)];
    drawFloorCanvas();
    toast("Line start set — click the line endpoint.", "");
    return;
  }
  // Second click: create N landmarks evenly distributed start→end
  const N = Math.max(2, Math.min(20, parseInt(state.lineN, 10) || 3));
  const [x0, y0] = state.pendingPlanStart;
  const x1 = Math.round(u), y1 = Math.round(v);
  const landmarkIds = [];
  for (let i = 0; i < N; i++) {
    const t = i / (N - 1);
    const x = Math.round(x0 + (x1 - x0) * t);
    const y = Math.round(y0 + (y1 - y0) * t);
    const id = uuid();
    state.positions.push({
      id,
      world_xy_plan_px: [x, y],
      clicks: {},
      notVisible: {},
      lineGroupId: null,  // filled below
    });
    landmarkIds.push(id);
  }
  const groupId = "G-" + Math.random().toString(36).slice(2, 8);
  for (const id of landmarkIds) {
    const p = state.positions.find((q) => q.id === id);
    p.lineGroupId = groupId;
  }
  state.lineGroups.push({
    id: groupId,
    landmarkIds,
    planLine: [[x0, y0], [x1, y1]],
  });
  state.pendingPlanStart = null;
  state.pendingLineGroup = groupId;
  state.camPendingLineStart = {};
  selectPosition(landmarkIds[0]);
  toast(`Line created (${N} pts). Click 2 endpoints in each camera that sees it.`, "");
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
        const ir = img.getBoundingClientRect();
        const u = ((ev.clientX - ir.left) / ir.width) * Number(img.dataset.w);
        const v = ((ev.clientY - ir.top) / ir.height) * Number(img.dataset.h);

        // Line mode: 2 clicks per camera, interpolate N positions on the line
        if (state.mode === "line" && state.pendingLineGroup) {
          const grp = state.lineGroups.find((g) => g.id === state.pendingLineGroup);
          if (!grp) {
            toast("Line group missing — set a line on the plan first.", "warn");
            return;
          }
          const start = state.camPendingLineStart[cam.name];
          if (!start) {
            state.camPendingLineStart[cam.name] = [Math.round(u), Math.round(v)];
            toast(`${cam.name}: start set, click line end.`, "");
            renderCameraGrid();
            return;
          }
          // Second click → interpolate clicks for the N landmarks of this line group
          const N = grp.landmarkIds.length;
          for (let i = 0; i < N; i++) {
            const t = i / (N - 1);
            const cu = Math.round(start[0] + (u - start[0]) * t);
            const cv = Math.round(start[1] + (v - start[1]) * t);
            const lm = state.positions.find((p) => p.id === grp.landmarkIds[i]);
            if (lm) {
              lm.clicks[cam.name] = [cu, cv];
              delete lm.notVisible[cam.name];
            }
          }
          delete state.camPendingLineStart[cam.name];
          toast(`${cam.name}: ${N} line points assigned ✓`, "good");
          markDirty();
          renderCameraGrid();
          renderPositionList();
          return;
        }

        // Point mode (or line mode without pending group): original single-landmark behaviour
        if (!pos) {
          toast("Add or select a landmark on the plan first.", "warn");
          return;
        }
        if (notVisible) {
          toast("Mark visible first.", "warn");
          return;
        }
        pos.clicks[cam.name] = [Math.round(u), Math.round(v)];
        delete pos.notVisible[cam.name];
        markDirty();
        renderCameraGrid();
        renderPositionList();
      });
      // Render overlay: dots for clicks + lines for line groups + pending-start indicator
      const overlay = document.createElement("div");
      overlay.className = "overlay";
      frame.appendChild(overlay);
      requestAnimationFrame(() => {
        const ir = img.getBoundingClientRect();
        const fr = frame.getBoundingClientRect();
        const W = Number(img.dataset.w), H = Number(img.dataset.h);
        const toCanvas = (u, v) => [
          ir.left - fr.left + (u / W) * ir.width,
          ir.top - fr.top + (v / H) * ir.height,
        ];

        const svgParts = [];

        // Collect clicks for this camera, grouped by line group
        const lineGroupClicks = {};   // groupId → [{lmId, click}]
        const standaloneClicks = [];  // for point-mode landmarks
        for (const p of state.positions) {
          const c = p.clicks && p.clicks[cam.name];
          if (!c) continue;
          if (p.lineGroupId) {
            (lineGroupClicks[p.lineGroupId] = lineGroupClicks[p.lineGroupId] || []).push({ p, c });
          } else {
            standaloneClicks.push({ p, c });
          }
        }

        // Draw connecting lines for line groups (in order of landmarks within group)
        for (const grp of state.lineGroups) {
          const entries = (lineGroupClicks[grp.id] || []).slice();
          if (entries.length < 2) continue;
          // Sort by order of landmarkIds in the group
          entries.sort(
            (a, b) =>
              grp.landmarkIds.indexOf(a.p.id) - grp.landmarkIds.indexOf(b.p.id)
          );
          const pts = entries.map((e) => toCanvas(e.c[0], e.c[1]));
          const d = pts.map((p, i) => (i === 0 ? `M ${p[0]} ${p[1]}` : `L ${p[0]} ${p[1]}`)).join(" ");
          svgParts.push(
            `<path d="${d}" fill="none" stroke="rgba(80,200,255,0.85)" stroke-width="2"/>`
          );
        }

        // Draw all click dots
        const allClicks = [
          ...Object.values(lineGroupClicks).flat(),
          ...standaloneClicks,
        ];
        for (const { p, c } of allClicks) {
          const [px, py] = toCanvas(c[0], c[1]);
          const isHere = pos && pos.id === p.id;
          const isWorstHere =
            cr && cr.worst_position_id && cr.worst_position_id === p.id;
          const col = isWorstHere ? "#e74c3c" : isHere ? "#3aa6ff" : "rgba(80,200,255,0.85)";
          const r = isHere ? 6 : 4.5;
          svgParts.push(
            `<circle cx="${px}" cy="${py}" r="${r}" fill="${col}" stroke="#fff" stroke-width="2"/>`
          );
        }

        // Pending line start (waiting for second click in this camera)
        if (state.mode === "line" && state.camPendingLineStart[cam.name]) {
          const [pu, pv] = state.camPendingLineStart[cam.name];
          const [px, py] = toCanvas(pu, pv);
          svgParts.push(
            `<circle cx="${px}" cy="${py}" r="7" fill="rgba(80,200,255,0.55)" stroke="#fff" stroke-width="2" stroke-dasharray="3,2"/>`,
            `<text x="${px + 9}" y="${py + 4}" font-size="10" font-weight="bold" fill="#fff">start</text>`
          );
        }

        if (svgParts.length > 0) {
          overlay.innerHTML =
            `<svg width="100%" height="100%" style="position:absolute;inset:0">${svgParts.join("")}</svg>`;
        }
      });
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

// ---------- LED marker overlay (visual helper, no click semantics) ----------

async function loadLedStrips() {
  try {
    const r = await api("GET", "/api/led/strips");
    state.ledStrips = r.strips || [];
  } catch (e) {
    toast("LED strips load failed: " + e.message, "bad");
    state.ledStrips = [];
  }
}

async function ledTurnOff() {
  try {
    await api("POST", "/api/led/off", {});
  } catch (_) {}
}

async function runLedAutoCal() {
  const btn = $("#btn-led-auto-cal");
  btn.disabled = true;
  try {
    await api("POST", "/api/auto-cal/led/start", {});
    toast("LED auto-cal started (dim room, wait ~2–5 min)", "");
    for (;;) {
      await new Promise((r) => setTimeout(r, 2000));
      const st = await api("GET", "/api/auto-cal/led/status");
      if (!st.running) {
        if (st.error) {
          toast("Auto-cal error: " + st.error, "bad");
        } else {
          const ok = Object.values(st.results || {}).filter((r) => r.ok).length;
          const total = Object.keys(st.results || {}).length;
          toast(`LED auto-cal done: ${ok}/${total} cameras OK`, ok === total ? "good" : "bad");
        }
        break;
      }
    }
    await doRecapture();
    await doCompute();
  } finally {
    btn.disabled = false;
  }
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
  $("#btn-led-auto-cal").addEventListener("click", () => runLedAutoCal().catch((e) => toast(e.message, "bad")));
  $("#btn-add-position").addEventListener("click", () =>
    toast("Click a landmark on the Maro plan.", "")
  );
  $("#btn-delete-position").addEventListener("click", deleteSelectedPosition);
  $("#btn-clear-all").addEventListener("click", () => {
    if (state.positions.length === 0) return;
    if (!confirm(`Clear all ${state.positions.length} landmarks?`)) return;
    state.positions = [];
    state.lineGroups = [];
    state.pendingPlanStart = null;
    state.pendingLineGroup = null;
    state.camPendingLineStart = {};
    state.selectedPositionId = null;
    state.computeResult = null;
    renderPositionList();
    renderCameraGrid();
    drawFloorCanvas();
    toast("All landmarks cleared", "good");
  });
  // Mode toggle (Point / Line — pure click semantics)
  document.querySelectorAll('input[name="mode"]').forEach((r) => {
    r.addEventListener("change", (ev) => {
      state.mode = ev.target.value;
      state.pendingPlanStart = null;
      state.camPendingLineStart = {};
      $("#line-n-wrap").hidden = state.mode !== "line";
      drawFloorCanvas();
      renderCameraGrid();
      toast(`Mode: ${state.mode}`, "");
    });
  });
  $("#line-n").addEventListener("change", (ev) => {
    const n = Math.max(2, Math.min(20, parseInt(ev.target.value, 10) || 3));
    state.lineN = n;
    ev.target.value = n;
  });

  // LED markers toggle — visual helper only (lights dotted pattern + shows dots on plan)
  $("#led-toggle").addEventListener("change", async (ev) => {
    state.ledOn = ev.target.checked;
    if (state.ledOn) {
      try {
        const r = await api("POST", "/api/led/all_markers", {
          rgbw: [255, 255, 255, 255],
          on_markers: 5,    // 50 cm lit block
          off_markers: 5,   // 50 cm dark gap
        });
        state.ledLitPerStrip = r.lit_with_px || {};
        toast(`LED ON — ${Object.values(state.ledLitPerStrip).flat().length} markers across all strips`, "good");
      } catch (e) {
        toast("LED on failed: " + e.message, "bad");
        ev.target.checked = false;
        state.ledOn = false;
      }
    } else {
      state.ledLitPerStrip = {};
      await ledTurnOff();
      toast("LED markers OFF", "");
    }
    drawFloorCanvas();
  });
  window.addEventListener("beforeunload", () => {
    navigator.sendBeacon &&
      navigator.sendBeacon("/api/led/off", new Blob(["{}"], { type: "application/json" }));
  });
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
