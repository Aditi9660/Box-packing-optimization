"""
Packing orchestrator (2-D floor-only, no stacking).

Every item is placed flat on the floor of its box.
Box height acts only as an orientation filter:
an orientation is valid when the item's height dimension ≤ box height.

Persistent Packing Features:
- Sessions persist across PACK operations
- Existing boxes are never re-optimized
- Only new inventory is processed
- Dimension buffer (+20% on every dimension)
- Best-fit box selection: box type is chosen once at creation (least
  wasted space among types that fit everything, else ascending size
  fallback) and never changes afterward
- A box auto-freezes once its recommendations for every SKU reach zero
- A box also auto-freezes as soon as its floor utilization reaches 90%,
  regardless of remaining capacity or recommendations
- Placed items are never moved between boxes once packed — an open
  (non-frozen) box only ever gains new items; existing items keep their
  box and position across every subsequent pack/top-up call
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .maxrects import MaxRects2D, PlacedItem
from .usps_boxes import BOX_ORDER, BOX_TYPE_MAP

EPS = 1e-9
DIMENSION_BUFFER_PCT = 0.20      # +20% applied to every item dimension before packing
MAX_FLOOR_UTIL_FOR_FREEZE = 90.0 # % floor utilization at which any box auto-freezes


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------

@dataclass
class ProductSpec:
    sku: str
    length: float
    width: float
    height: float
    weight: float
    quantity: int


@dataclass
class ItemInstance:
    sku: str
    instance_id: str
    seq: int            # global insertion order – numeric tiebreaker for sort
    length: float
    width: float
    height: float
    weight: float
    is_new: bool = True  # Track if this is newly added inventory

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height

    @property
    def footprint(self) -> float:
        l, w, h = self.length, self.width, self.height
        return max(l * w, l * h, w * h)

    @property
    def max_dim(self) -> float:
        return max(self.length, self.width, self.height)
    
    @property
    def buffered_length(self) -> float:
        """Internal dimension with buffer applied."""
        return self.length * (1 + DIMENSION_BUFFER_PCT)

    @property
    def buffered_width(self) -> float:
        """Internal dimension with buffer applied."""
        return self.width * (1 + DIMENSION_BUFFER_PCT)

    @property
    def buffered_height(self) -> float:
        """Internal dimension with buffer applied."""
        return self.height * (1 + DIMENSION_BUFFER_PCT)


@dataclass
class PackedBox:
    box_id: int
    box_type: str
    box_name: str
    box_l: float
    box_w: float
    box_h: float
    capacity: float
    engine: MaxRects2D
    frozen: bool = False
    packed_quantities: Dict[str, int] = field(default_factory=dict)  # Track packed qty per SKU

    # ---- computed properties ------------------------------------------------

    @property
    def used_weight(self) -> float:
        return self.engine.used_weight()

    @property
    def remaining_capacity(self) -> float:
        return self.capacity - self.used_weight

    @property
    def item_count(self) -> int:
        return len(self.engine.placed_items)

    @property
    def floor_util(self) -> float:
        floor_area = self.box_l * self.box_w
        if floor_area < EPS:
            return 0.0
        return min(self.engine.used_floor_area() / floor_area * 100.0, 100.0)

    @property
    def volume_util(self) -> float:
        vol = self.box_l * self.box_w * self.box_h
        if vol < EPS:
            return 0.0
        return min(self.engine.used_volume() / vol * 100.0, 100.0)

    @property
    def capacity_util(self) -> float:
        if self.capacity < EPS:
            return 0.0
        return min(self.used_weight / self.capacity * 100.0, 100.0)

    def freeze(self) -> None:
        self.frozen = True

    def to_dict(self) -> dict:
        items_data = []
        for it in self.engine.placed_items:
            items_data.append({
                "sku": it.sku,
                "instance_id": it.instance_id,
                "x": round(it.x, 4),
                "y": round(it.y, 4),
                "pw": round(it.pw, 4),
                "pd": round(it.pd, 4),
                "ph": round(it.ph, 4),
                "weight": round(it.weight, 4),
                "orientation": it.orientation,
                "original_l": round(it.original_l, 4),
                "original_w": round(it.original_w, 4),
                "original_h": round(it.original_h, 4),
                "buffered_l": round(it.buffered_l, 4),
                "buffered_w": round(it.buffered_w, 4),
                "buffered_h": round(it.buffered_h, 4),
            })
        free_data = []
        for fr in self.engine.free_rects:
            free_data.append({
                "x": round(fr.x, 4),
                "y": round(fr.y, 4),
                "dx": round(fr.dx, 4),
                "dy": round(fr.dy, 4),
            })
        return {
            "box_id": self.box_id,
            "box_type": self.box_type,
            "box_name": self.box_name,
            "box_l": self.box_l,
            "box_w": self.box_w,
            "box_h": self.box_h,
            "capacity": self.capacity,
            "used_weight": round(self.used_weight, 4),
            "remaining_capacity": round(self.remaining_capacity, 4),
            "item_count": self.item_count,
            "frozen": self.frozen,
            "floor_util": round(self.floor_util, 2),
            "volume_util": round(self.volume_util, 2),
            "capacity_util": round(self.capacity_util, 2),
            "items": items_data,
            "free_rects": free_data,
        }


@dataclass
class PackingSession:
    boxes: List[PackedBox] = field(default_factory=list)
    unpacked_items: List[ItemInstance] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)
    _next_box_id: int = field(default=1, repr=False)
    _topup_seq: int = field(default=0, repr=False)   # global counter for top-up instance IDs
    products: List[dict] = field(default_factory=list)
    packed_quantities: Dict[str, int] = field(default_factory=dict)  # SKU → total packed qty

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def next_box_id(self) -> int:
        bid = self._next_box_id
        self._next_box_id += 1
        return bid


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

class PackingOptimizer:
    def __init__(self, capacities: Dict[str, float]):
        self.capacities = capacities
        self.session: Optional[PackingSession] = None

    # -----------------------------------------------------------------------
    # Public entry points
    # -----------------------------------------------------------------------

    def pack(self, products: List[dict]) -> PackingSession:
        """
        Incremental pack with persistent session.
        - Load existing session if available
        - Only pack new inventory (difference from prior packed quantities)
        - Keep all existing boxes frozen and unchanged
        """
        if self.session is None:
            # First pack - create fresh session
            self.session = PackingSession(products=products)
            new_items = self._expand_and_sort(products, is_initial=True)
            self.session.log("=== INITIAL PACKING SESSION (2D floor-only, no stacking) ===")
            self.session.log(f"Products: {len(products)}  |  Items (expanded): {len(new_items)}")
            self._run(new_items)
        else:
            # Subsequent pack - incremental mode
            self.session.log("\n" + "="*70)
            self.session.log("=== INCREMENTAL PACK ===")
            
            # Update product specs (in case dimensions changed - but not qty for existing SKUs)
            existing_map = {p["sku"]: p for p in self.session.products}
            for prod in products:
                sku = prod["sku"]
                if sku not in existing_map:
                    # New SKU entirely
                    existing_map[sku] = dict(prod)
                else:
                    # Update dimensions/weight in case they changed, but handle qty specially
                    existing_map[sku]["length"] = prod.get("length", existing_map[sku]["length"])
                    existing_map[sku]["width"] = prod.get("width", existing_map[sku]["width"])
                    existing_map[sku]["height"] = prod.get("height", existing_map[sku]["height"])
                    existing_map[sku]["weight"] = prod.get("weight", existing_map[sku]["weight"])
            
            self.session.products = list(existing_map.values())
            
            # Calculate new items only (qty difference)
            new_items = self._calculate_new_items(products)
            
            if new_items:
                self.session.log(f"New items to pack: {len(new_items)}")
                self._run(new_items)
            else:
                self.session.log("No new items to pack.")
        
        self._finalise()
        return self.session

    def reset(self) -> None:
        """Clear all session state."""
        self.session = None

    # -----------------------------------------------------------------------
    # Core packing loop
    # -----------------------------------------------------------------------

    def _run(self, items: List[ItemInstance]) -> None:
        """Pack new items: first try existing open (non-frozen) boxes, then
        create new best-fit box(es) for whatever doesn't fit. A box's type
        is chosen once at creation and never changes afterward."""
        self.session.log(f"\n--- {len(items)} items remaining ---")

        # Phase 1: try to fit each item into an existing open box.
        remaining: List[ItemInstance] = []
        for item in items:
            placed = False
            for box in self.session.boxes:
                if box.frozen:
                    continue
                if self._place(box, item):
                    placed = True
                    break
            if not placed:
                remaining.append(item)

        if not remaining:
            self.session.log("All items fitted into existing open boxes.")
            return

        # Phase 2: create new best-fit box(es) for the rest.
        self.session.log(f"Creating new box(es) for {len(remaining)} items...")
        self._fill_new_boxes(remaining)

    # -----------------------------------------------------------------------
    # Box-selection helpers
    # -----------------------------------------------------------------------

    def _simulate_fit(self, box_type: str, items: List[ItemInstance]) -> tuple:
        """Simulate packing `items` (in their current order) into a fresh box
        of `box_type`. Returns (fit_count, all_fit, floor_utilization_pct)."""
        cfg = BOX_TYPE_MAP[box_type]
        cap = self.capacities.get(box_type, cfg["default_capacity"])
        engine = MaxRects2D(cfg["length"], cfg["width"], cfg["height"])

        fit_count = 0
        used_weight = 0.0
        for it in items:
            if used_weight + it.weight > cap + EPS:
                continue
            p = engine.place_item(
                it.sku, it.instance_id,
                it.buffered_length, it.buffered_width, it.buffered_height, it.weight
            )
            if p is not None:
                fit_count += 1
                used_weight += it.weight

        all_fit = fit_count == len(items)
        floor_area = cfg["length"] * cfg["width"]
        utilization = (engine.used_floor_area() / floor_area * 100.0) if floor_area > EPS else 0.0
        return fit_count, all_fit, utilization

    def _choose_best_fit_type(self, items: List[ItemInstance]) -> Optional[str]:
        """Evaluate all four box types independently. If any can hold every
        item in `items`, return the one with the least wasted space (highest
        floor utilization). Returns None if no single box type fits everything."""
        best_type: Optional[str] = None
        best_util = -1.0

        for box_cfg in BOX_ORDER:
            btype = box_cfg["type"]
            fit_count, all_fit, utilization = self._simulate_fit(btype, items)
            if all_fit:
                self.session.log(
                    f"  {box_cfg['name']}: fits all {len(items)} item(s), "
                    f"floor_util={utilization:.1f}%"
                )
                if utilization > best_util:
                    best_util = utilization
                    best_type = btype
            else:
                self.session.log(
                    f"  {box_cfg['name']}: fits {fit_count}/{len(items)} item(s) – not all"
                )

        return best_type

    def _fill_new_boxes(self, items: List[ItemInstance]) -> None:
        """Create new best-fit box(es) for items that don't fit in any
        existing open box. Box type is chosen once at creation and is
        permanent — boxes are never converted to another type."""
        remaining = list(items)

        best_type = self._choose_best_fit_type(remaining)
        if best_type is not None:
            box = self._create_box(best_type)
            self.session.log(
                f"Box #{box.box_id} created: {box.box_name}  "
                f"({box.box_l}×{box.box_w}×{box.box_h} in, cap={box.capacity} lbs) – "
                f"best fit for all {len(remaining)} item(s)"
            )
            for item in remaining:
                self._place(box, item)
            return

        # No single box type fits everything at once — step through sizes in
        # fixed ascending order, filling each box maximally before moving on.
        self.session.log("No single box type fits all remaining items. Filling by ascending size...")
        for box_cfg in BOX_ORDER:
            if not remaining:
                break

            box = self._create_box(box_cfg["type"])
            next_remaining = []
            for item in remaining:
                if self._place(box, item):
                    continue
                next_remaining.append(item)
            remaining = next_remaining

            if box.item_count:
                self.session.log(
                    f"Box #{box.box_id} created: {box.box_name}  "
                    f"({box.box_l}×{box.box_w}×{box.box_h} in, cap={box.capacity} lbs) – "
                    f"filled with {box.item_count} item(s), {len(remaining)} remaining"
                )
            else:
                # Nothing fit even one item in this box type — discard it and try the next size.
                self.session.boxes.remove(box)
                self.session.log(f"  {box_cfg['name']}: nothing fit – skipping to next size")

        if remaining:
            self.session.log(
                f"WARNING: {len(remaining)} item(s) don't fit in any box type. Marking unpacked."
            )
            self.session.unpacked_items.extend(remaining)

    def _create_box(self, btype: str) -> PackedBox:
        cfg = BOX_TYPE_MAP[btype]
        cap = self.capacities.get(btype, cfg["default_capacity"])
        engine = MaxRects2D(cfg["length"], cfg["width"], cfg["height"])
        box = PackedBox(
            box_id=self.session.next_box_id(),
            box_type=btype,
            box_name=cfg["name"],
            box_l=cfg["length"],
            box_w=cfg["width"],
            box_h=cfg["height"],
            capacity=cap,
            engine=engine,
        )
        self.session.boxes.append(box)
        return box

    # -----------------------------------------------------------------------
    # Placement
    # -----------------------------------------------------------------------

    def _place(self, box: PackedBox, item: ItemInstance) -> Optional[PlacedItem]:
        if box.frozen:
            self.session.log(f"  SKIP {item.instance_id}: box #{box.box_id} is frozen")
            return None

        if box.used_weight + item.weight > box.capacity + EPS:
            self.session.log(
                f"  CAP-VIOLATION {item.instance_id}: "
                f"{box.used_weight:.3f}+{item.weight:.3f} > {box.capacity}"
            )
            return None

        # Use buffered dimensions for placement
        placed = box.engine.place_item(
            item.sku, item.instance_id,
            item.buffered_length, item.buffered_width, item.buffered_height, item.weight
        )
        if placed:
            # Track quantity per SKU in this box
            if item.sku not in box.packed_quantities:
                box.packed_quantities[item.sku] = 0
            box.packed_quantities[item.sku] += 1
            
            # Track global packed quantity
            if item.sku not in self.session.packed_quantities:
                self.session.packed_quantities[item.sku] = 0
            self.session.packed_quantities[item.sku] += 1
            
            self.session.log(
                f"  PLACED {item.instance_id} @ ({placed.x:.3f},{placed.y:.3f}) "
                f"floor {placed.pw:.3f}×{placed.pd:.3f} h={placed.ph:.3f} "
                f"orient={placed.orientation}"
            )

            self._maybe_freeze_on_floor_util(box)
        else:
            self.session.log(f"  NO-FIT {item.instance_id} in box #{box.box_id}")
        return placed

    def _maybe_freeze_on_floor_util(self, box: PackedBox) -> None:
        """Freeze a box the moment its floor utilization reaches (or is
        within epsilon of) the freeze threshold, regardless of remaining
        weight capacity or SKU recommendations."""
        if not box.frozen and box.floor_util >= MAX_FLOOR_UTIL_FOR_FREEZE - EPS:
            box.freeze()
            self.session.log(
                f"  Box #{box.box_id} FROZEN – floor utilization {box.floor_util:.1f}% "
                f">= {MAX_FLOOR_UTIL_FOR_FREEZE}% threshold"
            )

    # -----------------------------------------------------------------------
    # Item expansion + sorting
    # -----------------------------------------------------------------------

    def _expand_and_sort(self, products: List[dict], is_initial: bool = False) -> List[ItemInstance]:
        """Expand products to items and sort them."""
        items: List[ItemInstance] = []
        seq = 0
        
        for prod in products:
            sku = prod["sku"]
            qty = int(prod["quantity"])
            for i in range(qty):
                items.append(ItemInstance(
                    sku=sku,
                    instance_id=f"{sku}-{i + 1}",
                    seq=seq,
                    length=float(prod["length"]),
                    width=float(prod["width"]),
                    height=float(prod["height"]),
                    weight=float(prod["weight"]),
                    is_new=is_initial,
                ))
                seq += 1
        
        items.sort(key=lambda it: (-it.footprint, -it.volume, -it.max_dim, it.seq))
        return items

    def _calculate_new_items(self, products: List[dict]) -> List[ItemInstance]:
        """
        Calculate only the new items (qty difference from already packed).
        Also handle item removals and dimension updates from open boxes.
        """
        # First, handle removals and dimension changes
        for prod in products:
            sku = prod["sku"]
            requested_qty = int(prod["quantity"])
            already_packed = self.session.packed_quantities.get(sku, 0)
            
            # Handle quantity removal
            if requested_qty < already_packed:
                qty_to_remove = already_packed - requested_qty
                self.session.log(f"  {sku}: removing {qty_to_remove} items (was {already_packed}, now {requested_qty})")
                self._remove_items_from_boxes(sku, qty_to_remove)
            
            # Handle dimension changes - remove ALL items of this SKU if dimensions changed
            # Then they'll be re-added with new dimensions
            old_dims = self._get_original_dims_for_sku(sku)
            new_dims = (float(prod["length"]), float(prod["width"]), float(prod["height"]))
            
            if old_dims and old_dims != new_dims:
                self.session.log(f"  {sku}: dimensions changed from {old_dims} to {new_dims}")
                self.session.log(f"  {sku}: removing all items due to dimension change")
                # Remove all items of this SKU so they get re-packed with new dimensions
                total_packed = self.session.packed_quantities.get(sku, 0)
                if total_packed > 0:
                    self._remove_items_from_boxes(sku, total_packed)
        
        # Then, calculate new items to add
        items: List[ItemInstance] = []
        # Count total items placed so far
        total_placed = sum(b.item_count for b in self.session.boxes)
        seq = total_placed
        
        for prod in products:
            sku = prod["sku"]
            requested_qty = int(prod["quantity"])
            already_packed = self.session.packed_quantities.get(sku, 0)
            new_qty = max(0, requested_qty - already_packed)
            
            if new_qty > 0:
                self.session.log(f"  {sku}: {already_packed} already packed, +{new_qty} new")
                
                for i in range(new_qty):
                    # Instance ID includes offset
                    instance_num = already_packed + i + 1
                    items.append(ItemInstance(
                        sku=sku,
                        instance_id=f"{sku}-{instance_num}",
                        seq=seq,
                        length=float(prod["length"]),
                        width=float(prod["width"]),
                        height=float(prod["height"]),
                        weight=float(prod["weight"]),
                        is_new=True,
                    ))
                    seq += 1
        
        items.sort(key=lambda it: (-it.footprint, -it.volume, -it.max_dim, it.seq))
        return items

    def _get_original_dims_for_sku(self, sku: str) -> Optional[tuple]:
        """Get original dimensions of a SKU from any placed item."""
        for box in self.session.boxes:
            for item in box.engine.placed_items:
                if item.sku == sku:
                    return (round(item.original_l, 2), round(item.original_w, 2), round(item.original_h, 2))
        return None

    def _remove_items_from_boxes(self, sku: str, qty_to_remove: int) -> None:
        """Remove items of a given SKU from open (non-frozen) boxes."""
        removed_count = 0
        
        # Iterate through boxes in reverse order to remove from most recent first
        for box in reversed(self.session.boxes):
            if box.frozen:
                self.session.log(f"    Skipping frozen box #{box.box_id} for removal")
                continue
            
            # Find items of this SKU in this box
            items_to_remove = [
                it for it in box.engine.placed_items 
                if it.sku == sku
            ]
            
            # Remove items until we've removed enough
            for item in items_to_remove:
                if removed_count >= qty_to_remove:
                    break
                
                # Remove from engine
                box.engine.placed_items.remove(item)
                
                # Update box's packed quantities
                if sku in box.packed_quantities:
                    box.packed_quantities[sku] = max(0, box.packed_quantities[sku] - 1)
                
                # Update session's packed quantities
                if sku in self.session.packed_quantities:
                    self.session.packed_quantities[sku] = max(0, self.session.packed_quantities[sku] - 1)
                
                removed_count += 1
                self.session.log(f"    Removed {item.instance_id} from box #{box.box_id}")
                
                # Recalculate free rects for this box after removal
                box.engine._rebuild_free_rects()
            
            if removed_count >= qty_to_remove:
                break
        
        self.session.log(f"  Total removed: {removed_count} × {sku}")

    # -----------------------------------------------------------------------
    # Recommendations + analytics
    # -----------------------------------------------------------------------

    def _finalise(self) -> None:
        self.session.log("\n=== COMPUTING RECOMMENDATIONS ===")
        for box in self.session.boxes:
            recs = self._recommendations(box)
            box._recommendations = recs  # type: ignore[attr-defined]

            if not box.frozen and sum(recs.values()) == 0:
                box.freeze()
                self.session.log(
                    f"Box #{box.box_id} FROZEN – no further units of any SKU fit "
                    f"(weight={box.used_weight:.3f}/{box.capacity} lbs, "
                    f"items={box.item_count}, floor_util={box.floor_util:.1f}%)"
                )

        total_placed = sum(b.item_count for b in self.session.boxes)
        total_weight = sum(b.used_weight for b in self.session.boxes)
        utils = [
            (b.floor_util + b.volume_util + b.capacity_util) / 3
            for b in self.session.boxes
        ]
        avg_util = sum(utils) / len(utils) if utils else 0.0

        self.session.log("\n=== SESSION SUMMARY ===")
        self.session.log(f"Boxes used     : {len(self.session.boxes)}")
        self.session.log(f"Items packed   : {total_placed}")
        self.session.log(f"Items unpacked : {len(self.session.unpacked_items)}")
        self.session.log(f"Weight packed  : {total_weight:.3f} lbs")
        self.session.log(f"Avg utilisation: {avg_util:.1f}%")

    def _recommendations(self, box: PackedBox) -> Dict[str, int]:
        """
        Simulate additional placements per SKU using a clone of the engine's
        free-rect list. Evaluates all 6 orientations, respects weight capacity.
        Computed for every box (frozen or not) so callers can detect the
        moment a box can no longer accept any more units.
        """
        recs: Dict[str, int] = {}
        for prod in self.session.products:
            sku = prod["sku"]
            l, w, h, wt = prod["length"], prod["width"], prod["height"], prod["weight"]

            sim = MaxRects2D(box.box_l, box.box_w, box.box_h)
            sim.free_rects = box.engine.clone_free_rects()
            sim.placed_items = []

            rem_cap = box.remaining_capacity
            count = 0
            while True:
                if rem_cap < wt - EPS:
                    break

                # Use buffered dimensions for recommendation check
                buffered_l = l * (1 + DIMENSION_BUFFER_PCT)
                buffered_w = w * (1 + DIMENSION_BUFFER_PCT)
                buffered_h = h * (1 + DIMENSION_BUFFER_PCT)

                placed = sim.place_item(sku, f"rec_{sku}_{count}", buffered_l, buffered_w, buffered_h, wt)
                if placed is None:
                    break
                rem_cap -= wt
                count += 1

            recs[sku] = count
            self.session.log(f"  Box #{box.box_id} | {sku}: +{count}")

        return recs


# ---------------------------------------------------------------------------
# Serialisation helper used by main.py
# ---------------------------------------------------------------------------

def session_to_dict(session: PackingSession) -> dict:
    boxes_data = []
    for box in session.boxes:
        d = box.to_dict()
        d["recommendations"] = getattr(box, "_recommendations", {})
        boxes_data.append(d)

    unpacked_data = [
        {
            "sku": it.sku,
            "instance_id": it.instance_id,
            "length": it.length,
            "width": it.width,
            "height": it.height,
            "weight": it.weight,
        }
        for it in session.unpacked_items
    ]

    total_placed = sum(b["item_count"] for b in boxes_data)
    total_weight = sum(b["used_weight"] for b in boxes_data)
    utils = [
        (b["floor_util"] + b["volume_util"] + b["capacity_util"]) / 3
        for b in boxes_data
    ]
    avg_util = round(sum(utils) / len(utils), 2) if utils else 0.0

    # products_map: SKU → dims (used by frontend to pre-fill rec buttons)
    products_map = {
        p["sku"]: {
            "length": p["length"],
            "width":  p["width"],
            "height": p["height"],
            "weight": p["weight"],
        }
        for p in session.products
    }

    return {
        "boxes": boxes_data,
        "unpacked": unpacked_data,
        "analytics": {
            "total_boxes":         len(boxes_data),
            "total_packed":        total_placed,
            "total_unpacked":      len(unpacked_data),
            "total_weight_packed": round(total_weight, 4),
            "average_utilization": avg_util,
        },
        "products_map": products_map,
        "logs": session.logs,
    }
