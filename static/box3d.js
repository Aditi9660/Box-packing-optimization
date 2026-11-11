/* =========================================================
   Box3DViewer – interactive, rotatable 3-D box view.
   Pure canvas + vanilla JS, no libraries. Renders the box
   wireframe and placed items (as extruded cuboids) from data
   already present in the pack/session response — no network
   calls. Drag to orbit, scroll to zoom.
   ========================================================= */

"use strict";

const BOX3D_PALETTE = [
  "#2563EB", "#D97706", "#059669", "#DC2626", "#7C3AED",
  "#DB2777", "#0891B2", "#65A30D", "#EA580C", "#9333EA",
  "#0284C7", "#16A34A", "#CA8A04", "#E11D48", "#6366F1",
];

const BOX3D_LIGHT = normalize3([0.35, -0.55, 0.75]);
const BOX3D_DEFAULT_AZIMUTH   = -0.6;
const BOX3D_DEFAULT_ELEVATION = 0.45;

class Box3DViewer {
  constructor(canvas, box) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.box = box;

    this.azimuth   = BOX3D_DEFAULT_AZIMUTH;
    this.elevation = BOX3D_DEFAULT_ELEVATION;
    this.zoom      = 1.0;

    this._dragging = false;
    this._lastX = 0;
    this._lastY = 0;
    this._skuColor = {};
    this._skuOrder = [];

    this._onPointerDown = this._onPointerDown.bind(this);
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp   = this._onPointerUp.bind(this);
    this._onWheel        = this._onWheel.bind(this);

    this._bindEvents();

    this._resizeObserver = new ResizeObserver(() => { this._resize(); this.render(); });
    this._resizeObserver.observe(canvas.parentElement);

    this._resize();
    this.render();
  }

  destroy() {
    if (this._resizeObserver) this._resizeObserver.disconnect();
    const c = this.canvas;
    c.removeEventListener("pointerdown", this._onPointerDown);
    c.removeEventListener("pointermove", this._onPointerMove);
    c.removeEventListener("pointerup",   this._onPointerUp);
    c.removeEventListener("pointerleave", this._onPointerUp);
    c.removeEventListener("pointercancel", this._onPointerUp);
    c.removeEventListener("wheel", this._onWheel);
  }

  resetView() {
    this.azimuth   = BOX3D_DEFAULT_AZIMUTH;
    this.elevation = BOX3D_DEFAULT_ELEVATION;
    this.zoom      = 1.0;
    this.render();
  }

  // ── Interaction ─────────────────────────────────────────

  _bindEvents() {
    const c = this.canvas;
    c.style.touchAction = "none";
    c.addEventListener("pointerdown", this._onPointerDown);
    c.addEventListener("pointermove", this._onPointerMove);
    c.addEventListener("pointerup",   this._onPointerUp);
    c.addEventListener("pointerleave", this._onPointerUp);
    c.addEventListener("pointercancel", this._onPointerUp);
    c.addEventListener("wheel", this._onWheel, { passive: false });
  }

  _onPointerDown(e) {
    this._dragging = true;
    this._lastX = e.clientX;
    this._lastY = e.clientY;
    this.canvas.setPointerCapture(e.pointerId);
    this.canvas.classList.add("dragging");
  }

  _onPointerMove(e) {
    if (!this._dragging) return;
    const dx = e.clientX - this._lastX;
    const dy = e.clientY - this._lastY;
    this._lastX = e.clientX;
    this._lastY = e.clientY;
    this.azimuth   += dx * 0.008;
    this.elevation  = clamp(this.elevation - dy * 0.008, -1.45, 1.45);
    this.render();
  }

  _onPointerUp() {
    this._dragging = false;
    this.canvas.classList.remove("dragging");
  }

  _onWheel(e) {
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * 0.001);
    this.zoom = clamp(this.zoom * factor, 0.4, 3.0);
    this.render();
  }

  // ── Sizing ───────────────────────────────────────────────

  _resize() {
    const parent = this.canvas.parentElement;
    const w = Math.max(parent.clientWidth, 260);
    const h = Math.round(w * 0.72);
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width  = w * dpr;
    this.canvas.height = h * dpr;
    this.canvas.style.width  = w + "px";
    this.canvas.style.height = h + "px";
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._cssW = w;
    this._cssH = h;
  }

  // ── Rendering ────────────────────────────────────────────

  _colorFor(sku) {
    if (!(sku in this._skuColor)) {
      this._skuColor[sku] = BOX3D_PALETTE[this._skuOrder.length % BOX3D_PALETTE.length];
      this._skuOrder.push(sku);
    }
    return this._skuColor[sku];
  }

  render() {
    const { ctx, box } = this;
    const w = this._cssW, h = this._cssH;
    ctx.clearRect(0, 0, w, h);

    const bl = box.box_l, bw = box.box_w, bh = box.box_h;
    const cx = bl / 2, cy = bw / 2, cz = bh / 2;
    const maxDim = Math.max(bl, bw, bh, 1e-6);
    const scale = (Math.min(w, h) / maxDim) * 0.62 * this.zoom;

    const cosA = Math.cos(this.azimuth),   sinA = Math.sin(this.azimuth);
    const cosE = Math.cos(this.elevation), sinE = Math.sin(this.elevation);

    const rotate = (x, y, z) => {
      const px = x - cx, py = y - cy, pz = z - cz;
      const x1 = px * cosA - py * sinA;
      const y1 = px * sinA + py * cosA;
      const z1 = pz;
      const y2 = y1 * cosE - z1 * sinE;
      const z2 = y1 * sinE + z1 * cosE;
      return [x1, y2, z2];
    };

    const project = (x, y, z) => {
      const [rx, ry, rz] = rotate(x, y, z);
      return { sx: w / 2 + rx * scale, sy: h / 2 - rz * scale, depth: ry };
    };

    const drawables = [];

    // Box wireframe
    const boxRaw = cuboidVertices(0, 0, 0, bl, bw, bh);
    const boxProj = boxRaw.map(v => project(v[0], v[1], v[2]));
    for (const [a, b] of CUBOID_EDGES) {
      const pa = boxProj[a], pb = boxProj[b];
      drawables.push({ type: "line", depth: (pa.depth + pb.depth) / 2, a: pa, b: pb });
    }

    // Placed items
    for (const it of box.items) {
      const rawVerts  = cuboidVertices(it.x, it.y, 0, it.pw, it.pd, it.ph);
      const projVerts = rawVerts.map(v => project(v[0], v[1], v[2]));
      const baseColor = this._colorFor(it.sku);

      for (const idxs of CUBOID_FACES) {
        const pts = idxs.map(i => projVerts[i]);
        const depth = pts.reduce((s, p) => s + p.depth, 0) / pts.length;

        const rawFace = idxs.map(i => rawVerts[i]);
        const normal = faceNormal(rawFace);
        const [nx, ny, nz] = rotateNormal(normal, cosA, sinA, cosE, sinE);
        const brightness = 0.55 + 0.45 * Math.max(0, nx * BOX3D_LIGHT[0] + ny * BOX3D_LIGHT[1] + nz * BOX3D_LIGHT[2]);

        drawables.push({ type: "face", depth, pts, color: shade(baseColor, brightness) });
      }
    }

    // Painter's algorithm: farthest first so nearer geometry overwrites it
    drawables.sort((a, b) => b.depth - a.depth);

    for (const d of drawables) {
      if (d.type === "line") {
        ctx.beginPath();
        ctx.moveTo(d.a.sx, d.a.sy);
        ctx.lineTo(d.b.sx, d.b.sy);
        ctx.strokeStyle = "#1E293B";
        ctx.lineWidth = 1.6;
        ctx.stroke();
      } else {
        ctx.beginPath();
        d.pts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.sx, p.sy) : ctx.lineTo(p.sx, p.sy)));
        ctx.closePath();
        ctx.fillStyle = d.color;
        ctx.fill();
        ctx.strokeStyle = "rgba(15,23,42,0.55)";
        ctx.lineWidth = 0.7;
        ctx.stroke();
      }
    }
  }
}

// ── Geometry helpers ────────────────────────────────────────

const CUBOID_FACES = [
  [0, 1, 2, 3], // bottom
  [4, 5, 6, 7], // top
  [0, 1, 5, 4], // front
  [2, 3, 7, 6], // back
  [1, 2, 6, 5], // right
  [0, 3, 7, 4], // left
];

const CUBOID_EDGES = [
  [0, 1], [1, 2], [2, 3], [3, 0],
  [4, 5], [5, 6], [6, 7], [7, 4],
  [0, 4], [1, 5], [2, 6], [3, 7],
];

function cuboidVertices(x, y, z, dx, dy, dz) {
  return [
    [x,      y,      z],
    [x + dx, y,      z],
    [x + dx, y + dy, z],
    [x,      y + dy, z],
    [x,      y,      z + dz],
    [x + dx, y,      z + dz],
    [x + dx, y + dy, z + dz],
    [x,      y + dy, z + dz],
  ];
}

function faceNormal(v3) {
  const [p0, p1, p2] = v3;
  const u = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]];
  const w = [p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]];
  return normalize3([
    u[1] * w[2] - u[2] * w[1],
    u[2] * w[0] - u[0] * w[2],
    u[0] * w[1] - u[1] * w[0],
  ]);
}

function rotateNormal(n, cosA, sinA, cosE, sinE) {
  const [x, y, z] = n;
  const x1 = x * cosA - y * sinA;
  const y1 = x * sinA + y * cosA;
  const z1 = z;
  const y2 = y1 * cosE - z1 * sinE;
  const z2 = y1 * sinE + z1 * cosE;
  return [x1, y2, z2];
}

function normalize3(v) {
  const len = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / len, v[1] / len, v[2] / len];
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function shade(hex, brightness) {
  const v = hex.replace("#", "");
  const r = parseInt(v.substring(0, 2), 16);
  const g = parseInt(v.substring(2, 4), 16);
  const b = parseInt(v.substring(4, 6), 16);
  const nr = Math.round(clamp(r * brightness, 0, 255));
  const ng = Math.round(clamp(g * brightness, 0, 255));
  const nb = Math.round(clamp(b * brightness, 0, 255));
  return `rgb(${nr},${ng},${nb})`;
}
