"""
USPS Box Packing Optimization System – FastAPI backend (2-D floor-only mode).

Endpoints
---------
GET  /                              Serve index.html
GET  /api/boxes                     USPS box catalogue
POST /api/pack                      Full pack (fresh session)
POST /api/reset                     Reset session
GET  /api/session                   Current session state (JSON)
GET  /api/visualization/{id}        2-D floor-plan PNG for a specific box
                                     (the 3-D view is rendered client-side
                                     from the box data already in this
                                     response / the pack response)
POST /api/boxes/{id}/top-up         Directly pack extra SKU units into a box
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from packing.maxrects import MaxRects2D
from packing.optimizer import PackingOptimizer, PackingSession, session_to_dict
from packing.usps_boxes import USPS_BOXES
from packing.visualizer import generate_box_png

EPS = 1e-9

# ---------------------------------------------------------------------------
# Application state (single in-memory session)
# ---------------------------------------------------------------------------

_optimizer: Optional[PackingOptimizer] = None
_session:   Optional[PackingSession]   = None
_vis_cache: Dict[int, bytes]           = {}


def _clear_caches() -> None:
    _vis_cache.clear()


# ---------------------------------------------------------------------------
# FastAPI setup
# ---------------------------------------------------------------------------

app = FastAPI(title="USPS Box Packing Optimizer", version="2.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ProductInput(BaseModel):
    sku: str = Field(..., min_length=1)
    length: float = Field(..., gt=0)
    width:  float = Field(..., gt=0)
    height: float = Field(..., gt=0)
    weight: float = Field(..., gt=0)
    quantity: int = Field(..., ge=1)

    @field_validator("sku")
    @classmethod
    def sku_strip(cls, v: str) -> str:
        return v.strip()


class CapacitiesInput(BaseModel):
    small:       float = Field(70.0, gt=0)
    medium_top:  float = Field(70.0, gt=0)
    medium_side: float = Field(70.0, gt=0)
    large:       float = Field(70.0, gt=0)


class PackRequest(BaseModel):
    products:   List[ProductInput] = Field(..., min_length=1)
    capacities: CapacitiesInput    = Field(default_factory=CapacitiesInput)


class TopUpRequest(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)

    @field_validator("sku")
    @classmethod
    def sku_strip(cls, v: str) -> str:
        return v.strip()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    with open("templates/index.html", encoding="utf-8") as fh:
        return HTMLResponse(
            content=fh.read(),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )


@app.get("/api/boxes")
async def get_boxes() -> JSONResponse:
    return JSONResponse({"boxes": USPS_BOXES})


@app.post("/api/pack")
async def pack(req: PackRequest) -> JSONResponse:
    global _optimizer, _session
    _clear_caches()

    caps = {
        "small":       req.capacities.small,
        "medium_top":  req.capacities.medium_top,
        "medium_side": req.capacities.medium_side,
        "large":       req.capacities.large,
    }
    products = [p.model_dump() for p in req.products]

    # Use persistent session - create optimizer only if needed
    if _optimizer is None:
        _optimizer = PackingOptimizer(caps)
    else:
        # Update capacities if they changed
        _optimizer.capacities = caps
    
    _session = _optimizer.pack(products)

    return JSONResponse({"success": True, "data": session_to_dict(_session)})


@app.post("/api/reset")
async def reset() -> JSONResponse:
    global _optimizer, _session
    if _optimizer:
        _optimizer.reset()
    _session   = None
    _clear_caches()
    return JSONResponse({"success": True, "message": "Session reset."})


@app.get("/api/session")
async def get_session() -> JSONResponse:
    if _session is None:
        return JSONResponse({"success": False, "data": None})
    return JSONResponse({"success": True, "data": session_to_dict(_session)})


@app.get("/api/visualization/{box_id}")
async def visualization(box_id: int) -> Response:
    if _session is None:
        raise HTTPException(status_code=404, detail="No session.")
    if box_id not in _vis_cache:
        box = _find_box(box_id)
        _vis_cache[box_id] = generate_box_png(box.to_dict())
    return Response(content=_vis_cache[box_id], media_type="image/png")


@app.post("/api/boxes/{box_id}/top-up")
async def top_up_box(box_id: int, req: TopUpRequest) -> JSONResponse:
    """
    Place additional units of one SKU directly into an existing box.
    Uses the box's remaining floor free-space (no stacking).
    Respects weight capacity. Recomputes recommendations after placement.
    Uses buffered dimensions internally.
    """
    from packing.optimizer import DIMENSION_BUFFER_PCT, MAX_FLOOR_UTIL_FOR_FREEZE

    if _session is None or _optimizer is None:
        raise HTTPException(status_code=400, detail="No active session.")

    box = _find_box(box_id)

    if box.frozen:
        raise HTTPException(
            status_code=400,
            detail=f"Box #{box_id} is frozen and cannot be modified."
        )

    # Look up product specification
    prod = next((p for p in _session.products if p["sku"] == req.sku), None)
    if prod is None:
        raise HTTPException(
            status_code=400,
            detail=f"SKU '{req.sku}' not found in session. Pack it first."
        )

    l, w, h, wt = prod["length"], prod["width"], prod["height"], prod["weight"]
    buffered_l = l * (1 + DIMENSION_BUFFER_PCT)
    buffered_w = w * (1 + DIMENSION_BUFFER_PCT)
    buffered_h = h * (1 + DIMENSION_BUFFER_PCT)

    placed_count = 0
    failed_count = 0

    for _ in range(req.quantity):
        # Weight check
        if box.engine.used_weight() + wt > box.capacity + EPS:
            failed_count += req.quantity - placed_count - failed_count
            break

        _session._topup_seq += 1
        instance_id = f"{req.sku}-TU{_session._topup_seq}"

        placed = box.engine.place_item(req.sku, instance_id, buffered_l, buffered_w, buffered_h, wt)
        if placed:
            placed_count += 1
            # Track packed quantity
            if req.sku not in box.packed_quantities:
                box.packed_quantities[req.sku] = 0
            box.packed_quantities[req.sku] += 1
            if req.sku not in _session.packed_quantities:
                _session.packed_quantities[req.sku] = 0
            _session.packed_quantities[req.sku] += 1

            # Auto-freeze the moment floor utilization reaches the threshold;
            # stop accepting further units from this same top-up request.
            if box.floor_util >= MAX_FLOOR_UTIL_FOR_FREEZE - EPS:
                box.frozen = True
                failed_count += req.quantity - placed_count - failed_count
                break
        else:
            failed_count += 1

    # Recompute recommendations for this box
    recs = _compute_recs_for_box(box)
    box._recommendations = recs  # type: ignore[attr-defined]

    # Auto-freeze once no more units of any SKU fit
    if not box.frozen and sum(recs.values()) == 0:
        box.frozen = True

    # Invalidate cached visualization for this box
    _vis_cache.pop(box_id, None)

    d = box.to_dict()
    d["recommendations"] = recs

    return JSONResponse({
        "success":      True,
        "placed_count": placed_count,
        "failed_count": failed_count,
        "box":          d,
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_box(box_id: int):
    if _session is None:
        raise HTTPException(status_code=404, detail="No session.")
    for box in _session.boxes:
        if box.box_id == box_id:
            return box
    raise HTTPException(status_code=404, detail=f"Box #{box_id} not found.")


def _compute_recs_for_box(box) -> dict:
    """Recompute how many extra units of each session SKU fit in box's remaining space."""
    from packing.optimizer import DIMENSION_BUFFER_PCT

    if _session is None:
        return {}

    recs: dict = {}
    for prod in _session.products:
        sku = prod["sku"]
        l, w, h, wt = prod["length"], prod["width"], prod["height"], prod["weight"]
        buffered_l = l * (1 + DIMENSION_BUFFER_PCT)
        buffered_w = w * (1 + DIMENSION_BUFFER_PCT)
        buffered_h = h * (1 + DIMENSION_BUFFER_PCT)

        sim = MaxRects2D(box.box_l, box.box_w, box.box_h)
        sim.free_rects  = box.engine.clone_free_rects()
        sim.placed_items = []

        rem_cap = box.remaining_capacity
        count = 0
        while True:
            if rem_cap < wt - EPS:
                break

            placed = sim.place_item(sku, f"rec_{sku}_{count}", buffered_l, buffered_w, buffered_h, wt)
            if placed is None:
                break
            rem_cap -= wt
            count   += 1

        recs[sku] = count

    return recs


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
