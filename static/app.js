/* =========================================================
   USPS Box Packing Optimizer – Frontend
   Vanilla ES6+, no frameworks.
   ========================================================= */

"use strict";

// ── Constants ────────────────────────────────────────────

const ORIENT_LABELS = [
  "L×W (h=H)", "L×H (h=W)", "W×L (h=H)",
  "W×H (h=L)", "H×L (h=W)", "H×W (h=L)"
];

// ── State ────────────────────────────────────────────────

let rowId        = 0;
let _productsMap = {};   // SKU → {length, width, height, weight}
let _boxesById   = {};   // box_id → latest box data (for the 3D viewer)
const _box3DViewers = {}; // box_id → Box3DViewer instance

// ── DOM refs ─────────────────────────────────────────────

const tblBody          = document.getElementById("tbl-body");
const btnAddRow        = document.getElementById("btn-add-row");
const btnPack          = document.getElementById("btn-pack");
const btnReset         = document.getElementById("btn-reset");
const statusBar        = document.getElementById("status-bar");
const analyticsBanner  = document.getElementById("analytics-banner");
const resultsContainer = document.getElementById("results-container");
// const logsSection      = document.getElementById("logs-section");
// const logOutput        = document.getElementById("log-output");
// const logCount         = document.getElementById("log-count");

// ── Initialise ───────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  addProductRow("SKU-A", 4, 3, 2, 5.0, 5);
  addProductRow("SKU-B", 6, 4, 3, 4.0, 3);
  addProductRow("SKU-C", 2, 2, 1, 2.0, 8);

  btnAddRow.addEventListener("click", () => addProductRow());
  btnPack.addEventListener("click", runPack);
  btnReset.addEventListener("click", resetSession);
});

// ── Product table helpers ─────────────────────────────────

function addProductRow(sku="", l="", w="", h="", wt="", qty=1) {
  const id = ++rowId;
  const tr = document.createElement("tr");
  tr.dataset.id = id;
  tr.innerHTML = `
    <td><input type="text"   class="f-sku" value="${sku}" placeholder="SKU" /></td>
    <td><input type="number" class="f-l"   value="${l}"   min="0.01" step="0.01" /></td>
    <td><input type="number" class="f-w"   value="${w}"   min="0.01" step="0.01" /></td>
    <td><input type="number" class="f-h"   value="${h}"   min="0.01" step="0.01" /></td>
    <td><input type="number" class="f-wt"  value="${wt}"  min="0.001" step="0.001" /></td>
    <td><input type="number" class="f-qty" value="${qty}" min="1" step="1" /></td>
    <td><button class="btn-del" title="Remove row" onclick="removeRow(${id})">✕</button></td>
  `;
  tblBody.appendChild(tr);
  return tr;
}

function removeRow(id) {
  const tr = tblBody.querySelector(`tr[data-id="${id}"]`);
  if (tr) tblBody.removeChild(tr);
}

function collectProducts() {
  const rows = tblBody.querySelectorAll("tr");
  const products = [];
  const errors   = [];

  rows.forEach((tr, i) => {
    const sku = tr.querySelector(".f-sku").value.trim();
    const l   = parseFloat(tr.querySelector(".f-l").value);
    const w   = parseFloat(tr.querySelector(".f-w").value);
    const h   = parseFloat(tr.querySelector(".f-h").value);
    const wt  = parseFloat(tr.querySelector(".f-wt").value);
    const qty = parseInt(tr.querySelector(".f-qty").value, 10);

    if (!sku)              errors.push(`Row ${i+1}: SKU is required.`);
    if (isNaN(l) || l<=0) errors.push(`Row ${i+1}: Length must be > 0.`);
    if (isNaN(w) || w<=0) errors.push(`Row ${i+1}: Width must be > 0.`);
    if (isNaN(h) || h<=0) errors.push(`Row ${i+1}: Height must be > 0.`);
    if (isNaN(wt)||wt<=0) errors.push(`Row ${i+1}: Weight must be > 0.`);
    if (isNaN(qty)||qty<1) errors.push(`Row ${i+1}: Quantity must be >= 1.`);

    if (!errors.length) {
      products.push({ sku, length: l, width: w, height: h, weight: wt, quantity: qty });
    }
  });

  return { products, errors };
}

function collectCapacities() {
  return {
    small:       parseFloat(document.getElementById("cap-small").value)       || 70,
    medium_top:  parseFloat(document.getElementById("cap-medium-top").value)  || 70,
    medium_side: parseFloat(document.getElementById("cap-medium-side").value) || 70,
    large:       parseFloat(document.getElementById("cap-large").value)       || 70,
  };
}

// ── Pack ──────────────────────────────────────────────────

async function runPack() {
  const { products, errors } = collectProducts();
  if (errors.length)       { showStatus("error", errors.join("<br>")); return; }
  if (!products.length)    { showStatus("error", "No products entered."); return; }

  setLoading(true, "Packing...");

  try {
    const resp = await fetch("/api/pack", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ products, capacities: collectCapacities() }),
    });
    const json = await resp.json();
    if (!resp.ok || !json.success) throw new Error(json?.detail || JSON.stringify(json));

    renderResults(json.data);
    showStatus("success", `Pack complete – ${json.data.analytics.total_boxes} box(es) used.`);
  } catch (err) {
    showStatus("error", "Error: " + err.message);
  } finally {
    setLoading(false);
  }
}

async function resetSession() {
  await fetch("/api/reset", { method: "POST" });
  _productsMap = {};
  _boxesById   = {};
  destroyAll3DViewers();
  resultsContainer.innerHTML = "";
  analyticsBanner.classList.add("hidden");
  // logsSection.classList.add("hidden");
  // logOutput.textContent = "";
  showStatus("info", "Session reset. Enter products and click Pack.");
}

function destroyAll3DViewers() {
  for (const boxId of Object.keys(_box3DViewers)) {
    _box3DViewers[boxId].destroy();
    delete _box3DViewers[boxId];
  }
}

// ── Recommendation top-up ─────────────────────────────────

async function applyRecommendation(boxId, sku, count, btn) {
  btn.disabled   = true;
  btn.classList.add("rec-btn-loading");
  const original = btn.innerHTML;

  try {
    const resp = await fetch(`/api/boxes/${boxId}/top-up`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ sku, quantity: count }),
    });
    const json = await resp.json();

    if (!resp.ok || !json.success) throw new Error(json?.detail || "Server error");

    const placed = json.placed_count;
    const failed = json.failed_count;

    // Update the box card in-place with fresh data
    updateBoxCard(json.box);

    // Sync product table so the added quantity is reflected for future packs
    if (placed > 0) {
      syncProductTableQty(sku, placed);
    }

    // Feedback message inside button (briefly)
    const feedbackBtn = document.querySelector(`#box-card-${boxId} .rec-btn[data-sku="${esc(sku)}"]`);
    if (feedbackBtn) {
      feedbackBtn.innerHTML = placed > 0
        ? `<span class="rec-sku">${esc(sku)}</span><span class="rec-plus rec-ok">+${placed} added</span>`
        : `<span class="rec-sku">${esc(sku)}</span><span class="rec-plus rec-fail">no space</span>`;
      feedbackBtn.disabled = true;
      setTimeout(() => { feedbackBtn.disabled = false; }, 1800);
    }

    if (placed > 0 && failed === 0) {
      showStatus("success", `Packed ${placed} × ${sku} into Box #${boxId}. Product table updated.`);
    } else if (placed > 0) {
      showStatus("info", `Packed ${placed} × ${sku} into Box #${boxId}. ${failed} did not fit. Table updated.`);
    } else {
      showStatus("error", `No space or capacity left in Box #${boxId} for ${sku}.`);
    }
  } catch (err) {
    showStatus("error", "Top-up error: " + err.message);
    btn.disabled = false;
    btn.innerHTML = original;
  } finally {
    btn.classList.remove("rec-btn-loading");
  }
}

// ── Render full results ───────────────────────────────────

function renderResults(data) {
  const { boxes, unpacked, analytics, products_map, logs } = data;

  _productsMap = products_map || {};

  // Analytics banner
  document.getElementById("an-boxes").textContent    = analytics.total_boxes;
  document.getElementById("an-packed").textContent   = analytics.total_packed;
  document.getElementById("an-unpacked").textContent = analytics.total_unpacked;
  document.getElementById("an-weight").textContent   = analytics.total_weight_packed.toFixed(3);
  document.getElementById("an-util").textContent     = analytics.average_utilization.toFixed(1) + "%";
  analyticsBanner.classList.remove("hidden");

  destroyAll3DViewers();
  _boxesById = {};
  resultsContainer.innerHTML = "";

  if (unpacked && unpacked.length > 0) {
    const warn = document.createElement("div");
    warn.className = "unpacked-warning";
    warn.innerHTML = `<strong>Warning: ${unpacked.length} item(s) could not be packed</strong>
      SKUs: ${[...new Set(unpacked.map(u => u.sku))].join(", ")}`;
    resultsContainer.appendChild(warn);
  }

  boxes.forEach(box => {
    _boxesById[box.box_id] = box;
    resultsContainer.appendChild(buildBoxCard(box));
  });

  // logOutput.textContent = logs.join("\n");
  // logCount.textContent  = logs.length + " lines";
  // logsSection.classList.remove("hidden");
}

// Increment a SKU's quantity in the product table by delta.
// If the SKU row doesn't exist yet, add it pre-filled from _productsMap.
function syncProductTableQty(sku, delta) {
  const rows = tblBody.querySelectorAll("tr");
  for (const tr of rows) {
    const skuInput = tr.querySelector(".f-sku");
    if (skuInput && skuInput.value.trim() === sku) {
      const qtyInput   = tr.querySelector(".f-qty");
      qtyInput.value   = (parseInt(qtyInput.value, 10) || 0) + delta;
      tr.classList.add("row-flash");
      setTimeout(() => tr.classList.remove("row-flash"), 1400);
      return;
    }
  }
  // SKU not yet in table – insert it using stored product spec
  const spec = _productsMap[sku];
  if (spec) {
    const tr = addProductRow(sku, spec.length, spec.width, spec.height, spec.weight, delta);
    tr.classList.add("row-flash");
    setTimeout(() => tr.classList.remove("row-flash"), 1400);
    tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

// Replace existing box card DOM node with rebuilt version
function updateBoxCard(boxData) {
  const old = document.getElementById(`box-card-${boxData.box_id}`);
  if (!old) return;
  if (_box3DViewers[boxData.box_id]) {
    _box3DViewers[boxData.box_id].destroy();
    delete _box3DViewers[boxData.box_id];
  }
  _boxesById[boxData.box_id] = boxData;
  const fresh = buildBoxCard(boxData);
  old.replaceWith(fresh);
}

// ── Box card builder ──────────────────────────────────────

function buildBoxCard(box) {
  const card = document.createElement("div");
  card.className = box.frozen ? "box-card frozen" : "box-card";
  card.id = `box-card-${box.box_id}`;

  const frozenBadge = box.frozen
    ? '<span class="box-frozen-badge">Frozen</span>'
    : '<span class="box-frozen-badge open-badge">Open</span>';

  const frozenBanner = box.frozen 
    ? '<div class="frozen-banner">🔒 FROZEN BOX</div>'
    : '';

  card.innerHTML = `
    ${frozenBanner}
    <div class="box-card-header">
      <div class="box-card-title">
        <h3>Box #${box.box_id} &ndash; ${box.box_name}</h3>
        ${frozenBadge}
      </div>
      <span class="box-dims">${box.box_l}" &times; ${box.box_w}" &times; ${box.box_h}"</span>
    </div>
    <div class="box-card-body">

      <div class="box-stats">
        ${statChip("Capacity",       box.capacity + " lbs")}
        ${statChip("Used Weight",    box.used_weight.toFixed(3) + " lbs")}
        ${statChip("Remaining",      box.remaining_capacity.toFixed(3) + " lbs")}
        ${statChip("Items on Floor", box.item_count)}
      </div>

      <div class="util-bars">
        ${utilBar("Floor Utilization",    box.floor_util)}
        ${utilBar("Volume Utilization",   box.volume_util)}
        ${utilBar("Capacity Utilization", box.capacity_util)}
      </div>

      ${buildRecsHTML(box.recommendations, box.box_id)}

      <div class="items-section">
        <details>
          <summary>Packed Items (${box.item_count})</summary>
          ${buildItemsTable(box.items)}
        </details>
      </div>

      <div class="vis-toggle-row">
        <button class="btn btn-sm btn-secondary" onclick="load2DVis(${box.box_id}, this)">
          Show Packing Layout
        </button>
        <button class="btn btn-sm btn-secondary" onclick="toggle3DVis(${box.box_id}, this)">
          Show 3D View
        </button>
      </div>
      <div class="vis-panel" id="vis-panel-${box.box_id}-2d"></div>
      <div class="vis-panel" id="vis-panel-${box.box_id}-3d"></div>

    </div>
  `;

  return card;
}

// ── Recommendation buttons ────────────────────────────────

function buildRecsHTML(recs, boxId) {
  if (!recs || Object.keys(recs).length === 0) return "";

  const buttons = Object.entries(recs).map(([sku, cnt]) => {
    if (cnt === 0) {
      return `<span class="rec-chip zero">
        <span class="rec-sku">${esc(sku)}</span>
        <span class="rec-count zero">+0</span>
      </span>`;
    }
    return `<button
        class="rec-btn"
        data-sku="${esc(sku)}"
        onclick="applyRecommendation(${boxId}, '${esc(sku)}', ${cnt}, this)"
        title="Pack ${cnt} more ${esc(sku)} into Box #${boxId}">
      <span class="rec-sku">${esc(sku)}</span>
      <span class="rec-plus">+${cnt}</span>
    </button>`;
  }).join("");

  return `<div class="recs-section">
    <h4>Additional Units That Can Fit
    </h4>
    <div class="recs-grid">${buttons}</div>
  </div>`;
}

// ── Other card helpers ────────────────────────────────────

function statChip(label, value) {
  return `<div class="stat-item">
    <div class="stat-value">${value}</div>
    <div class="stat-label">${label}</div>
  </div>`;
}

function utilBar(label, pct) {
  pct = Math.min(Math.max(pct, 0), 100);
  let cls = "util-green";
  if (pct >= 90)      cls = "util-red";
  else if (pct >= 70) cls = "util-yellow";
  else if (pct < 30)  cls = "util-blue";

  return `<div class="util-row">
    <label>${label}</label>
    <div class="util-bar-track">
      <div class="util-bar-fill ${cls}" style="width:${pct}%"></div>
    </div>
    <span class="util-pct">${pct.toFixed(1)}%</span>
  </div>`;
}

function buildItemsTable(items) {
  if (!items || !items.length) return "<p style='color:#6B7280;font-size:.8rem'>No items.</p>";
  const rows = items.map(it => `
    <tr>
      <td>${esc(it.sku)}</td>
      <td>${esc(it.instance_id)}</td>
      <td>(${it.x.toFixed(3)}, ${it.y.toFixed(3)})</td>
      <td>${it.pw.toFixed(3)}" × ${it.pd.toFixed(3)}"</td>
      <td>${it.ph.toFixed(3)}"</td>
      <td><small style="color:#6B7280">${it.original_l.toFixed(2)}" × ${it.original_w.toFixed(2)}" × ${it.original_h.toFixed(2)}"<br/>(orig)</small></td>
      <td><small style="color:#2563EB">${it.buffered_l.toFixed(2)}" × ${it.buffered_w.toFixed(2)}" × ${it.buffered_h.toFixed(2)}"<br/>(+20% buf)</small></td>
      <td>${it.weight} lbs</td>
      <td><span class="orient-badge">${ORIENT_LABELS[it.orientation] ?? it.orientation}</span></td>
    </tr>`).join("");

  return `<table class="items-table">
    <thead><tr>
      <th>SKU</th><th>Instance</th><th>Floor (x,y)</th>
      <th>Footprint</th><th>Height</th><th>Original Dims</th><th>Buffered Dims</th><th>Weight</th><th>Orientation</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// ── 2-D visualization (server-rendered PNG, lazy-loaded) ──

async function load2DVis(boxId, btn) {
  const panel = document.getElementById(`vis-panel-${boxId}-2d`);

  // Toggle if already shown
  if (panel.dataset.loaded === "1") {
    const img = panel.querySelector("img");
    if (img) {
      const hidden = img.style.display === "none";
      img.style.display = hidden ? "block" : "none";
      btn.textContent = hidden ? "Hide Layout" : "Show Packing Layout";
    }
    return;
  }

  panel.innerHTML = `<div class="vis-loading">Generating layout...</div>`;
  btn.disabled = true;

  const img = new Image();
  img.onload = () => {
    panel.innerHTML = "";
    panel.appendChild(img);
    panel.dataset.loaded = "1";
    btn.disabled    = false;
    btn.textContent = "Hide Layout";
  };
  img.onerror = () => {
    panel.innerHTML = `<div class="vis-loading" style="color:#EF4444">Failed to load layout.</div>`;
    btn.disabled = false;
  };
  img.alt   = `Box #${boxId} floor plan`;
  img.style.cssText = "width:100%;border-radius:6px;border:1px solid #E2E8F0";
  img.src = `/api/visualization/${boxId}?t=${Date.now()}`;
}

// ── 3-D visualization (interactive canvas, rendered from data
//    already in the pack/session response — no network call) ─

function toggle3DVis(boxId, btn) {
  const panel = document.getElementById(`vis-panel-${boxId}-3d`);

  // Already built — just show/hide it.
  if (panel.dataset.loaded === "1") {
    const hidden = panel.style.display === "none";
    panel.style.display = hidden ? "" : "none";
    btn.textContent = hidden ? "Hide 3D View" : "Show 3D View";
    if (hidden) {
      const viewer = _box3DViewers[boxId];
      if (viewer) { viewer._resize(); viewer.render(); }
    }
    return;
  }

  const boxData = _boxesById[boxId];
  if (!boxData) return;

  panel.innerHTML = `
    <div class="box3d-toolbar">
      <span class="box3d-hint">Drag to rotate &middot; Scroll to zoom</span>
      <button type="button" class="btn btn-sm btn-secondary box3d-reset-btn">Reset View</button>
    </div>
    <canvas class="box3d-canvas"></canvas>
  `;

  const canvas = panel.querySelector("canvas");
  const viewer = new Box3DViewer(canvas, boxData);
  _box3DViewers[boxId] = viewer;
  panel.querySelector(".box3d-reset-btn").addEventListener("click", () => viewer.resetView());

  panel.dataset.loaded = "1";
  btn.textContent = "Hide 3D View";
}

// ── UI helpers ────────────────────────────────────────────

function setLoading(isLoading, label = "Processing...") {
  btnPack.disabled  = isLoading;
  btnReset.disabled = isLoading;
  btnPack.innerHTML = isLoading
    ? `<span class="spinner"></span>${label}`
    : "Pack";
}

function showStatus(type, html) {
  statusBar.className = `status-bar ${type}`;
  statusBar.innerHTML = html;
  statusBar.classList.remove("hidden");
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
