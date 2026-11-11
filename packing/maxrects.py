"""
2-D MaxRects bin-packing engine with Best Short Side Fit (BSSF) heuristic.

No stacking: every item occupies floor space only.
Box height is a per-orientation feasibility constraint — an orientation is
valid only when the item's height dimension fits inside the box.

Coordinate axes
---------------
  X  – length axis (floor)
  Y  – width / depth axis (floor)
  (Z is implicitly 0 for all items — floor placement only)

Free rectangles are 2-D axis-aligned rectangles (x, y, dx, dy).
Items placed at the corner (x, y) of the chosen free rect.
After each placement the overlapping free rects are split into sub-rects
and fully-contained rects are pruned — the standard MaxRects invariant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

EPS = 1e-9


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FreeRect:
    x: float
    y: float
    dx: float   # extent in X
    dy: float   # extent in Y

    def valid(self) -> bool:
        return self.dx > EPS and self.dy > EPS

    def area(self) -> float:
        return self.dx * self.dy

    def contains(self, other: "FreeRect") -> bool:
        return (
            self.x  <= other.x  + EPS
            and self.y  <= other.y  + EPS
            and self.x + self.dx >= other.x + other.dx - EPS
            and self.y + self.dy >= other.y + other.dy - EPS
        )


@dataclass
class PlacedItem:
    sku: str
    instance_id: str
    x: float            # floor X position
    y: float            # floor Y position
    pw: float           # footprint in X direction (buffered)
    pd: float           # footprint in Y direction (buffered)
    ph: float           # item height in placed orientation (≤ box_h, buffered)
    weight: float
    original_l: float   # original dimensions (before buffer)
    original_w: float
    original_h: float
    orientation: int    # index 0-5
    buffered_l: float = 0.0  # buffered dimensions (for UI display)
    buffered_w: float = 0.0
    buffered_h: float = 0.0

    def floor_area(self) -> float:
        return self.pw * self.pd

    def volume(self) -> float:
        return self.pw * self.pd * self.ph


# ---------------------------------------------------------------------------
# Orientation generation
# ---------------------------------------------------------------------------

_ORIENT_FNS = [
    lambda l, w, h: (l, w, h),  # 0: floor l×w, height h
    lambda l, w, h: (l, h, w),  # 1: floor l×h, height w
    lambda l, w, h: (w, l, h),  # 2: floor w×l, height h
    lambda l, w, h: (w, h, l),  # 3: floor w×h, height l
    lambda l, w, h: (h, l, w),  # 4: floor h×l, height w
    lambda l, w, h: (h, w, l),  # 5: floor h×w, height l
]


def generate_orientations(
    l: float, w: float, h: float, box_h: float
) -> List[Tuple[float, float, float, int]]:
    """
    Return unique (pw, pd, ph, orient_idx) tuples for all 6 orientations.
    Only orientations where ph ≤ box_h are included (height must fit in box).
    """
    seen: set = set()
    result: List[Tuple[float, float, float, int]] = []
    for idx, fn in enumerate(_ORIENT_FNS):
        pw, pd, ph = fn(l, w, h)
        if ph > box_h + EPS:          # height doesn't fit — skip
            continue
        # Deduplicate: (min_floor, max_floor, height) is invariant under 90° rotation
        key = (round(min(pw, pd), 9), round(max(pw, pd), 9), round(ph, 9))
        if key not in seen:
            seen.add(key)
            result.append((pw, pd, ph, idx))
    return result


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class MaxRects2D:
    """
    2-D MaxRects packer for a single bin.
    Items are placed on the floor only — no stacking.
    """

    def __init__(self, box_l: float, box_w: float, box_h: float):
        self.box_l = box_l
        self.box_w = box_w
        self.box_h = box_h
        self.free_rects: List[FreeRect] = [FreeRect(0.0, 0.0, box_l, box_w)]
        self.placed_items: List[PlacedItem] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def place_item(
        self,
        sku: str,
        instance_id: str,
        item_l: float,
        item_w: float,
        item_h: float,
        weight: float,
    ) -> Optional[PlacedItem]:
        """Place one item on the floor. Returns PlacedItem on success, None on failure."""
        result = self._find_best(item_l, item_w, item_h)
        if result is None:
            return None

        free_rect, (pw, pd, ph), orient_idx = result
        
        # Extract original (non-buffered) dimensions.
        # The buffer is a uniform +20% on every axis, so dividing it back out
        # recovers the true dimension regardless of which axis it came from.
        from packing.optimizer import DIMENSION_BUFFER_PCT
        buffer_ratio = 1 + DIMENSION_BUFFER_PCT
        original_l = item_l / buffer_ratio
        original_w = item_w / buffer_ratio
        original_h = item_h / buffer_ratio

        placed = PlacedItem(
            sku=sku,
            instance_id=instance_id,
            x=free_rect.x,
            y=free_rect.y,
            pw=pw,
            pd=pd,
            ph=ph,
            weight=weight,
            original_l=original_l,
            original_w=original_w,
            original_h=original_h,
            orientation=orient_idx,
            buffered_l=item_l,
            buffered_w=item_w,
            buffered_h=item_h,
        )

        self._update_free_rects(placed)
        self.placed_items.append(placed)
        return placed

    def clone_free_rects(self) -> List[FreeRect]:
        return [FreeRect(r.x, r.y, r.dx, r.dy) for r in self.free_rects]

    def _rebuild_free_rects(self) -> None:
        """Rebuild free rectangles by recalculating from current placed items."""
        self.free_rects = [FreeRect(0.0, 0.0, self.box_l, self.box_w)]
        for placed in self.placed_items:
            self._update_free_rects(placed)

    def used_floor_area(self) -> float:
        return sum(it.floor_area() for it in self.placed_items)

    def used_volume(self) -> float:
        return sum(it.volume() for it in self.placed_items)

    def used_weight(self) -> float:
        return sum(it.weight for it in self.placed_items)

    # ------------------------------------------------------------------
    # Placement search — BSSF on floor XY plane
    # ------------------------------------------------------------------

    def _find_best(
        self, item_l: float, item_w: float, item_h: float
    ) -> Optional[Tuple[FreeRect, Tuple[float, float, float], int]]:
        best_score = (math.inf, math.inf)
        best_fr: Optional[FreeRect] = None
        best_dims: Optional[Tuple[float, float, float]] = None
        best_orient = -1

        for pw, pd, ph, orient_idx in generate_orientations(item_l, item_w, item_h, self.box_h):
            for fr in self.free_rects:
                if pw <= fr.dx + EPS and pd <= fr.dy + EPS:
                    rem_w = fr.dx - pw
                    rem_d = fr.dy - pd
                    short_side = min(rem_w, rem_d)
                    long_side  = max(rem_w, rem_d)
                    score = (short_side, long_side)
                    if score < best_score:
                        best_score = score
                        best_fr    = fr
                        best_dims  = (pw, pd, ph)
                        best_orient = orient_idx

        if best_fr is None:
            return None
        return best_fr, best_dims, best_orient  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Free-rect management
    # ------------------------------------------------------------------

    def _update_free_rects(self, placed: PlacedItem) -> None:
        new_free: List[FreeRect] = []
        for fr in self.free_rects:
            if _overlaps(fr, placed):
                new_free.extend(_split(fr, placed))
            else:
                new_free.append(fr)
        self.free_rects = _prune(new_free)

    # ------------------------------------------------------------------
    # Collision check (used by tests / external callers)
    # ------------------------------------------------------------------

    def has_collision(self) -> bool:
        """Return True if any two placed items overlap (sanity check)."""
        items = self.placed_items
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if not (
                    a.x + a.pw <= b.x + EPS or b.x + b.pw <= a.x + EPS
                    or a.y + a.pd <= b.y + EPS or b.y + b.pd <= a.y + EPS
                ):
                    return True
        return False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _overlaps(fr: FreeRect, it: PlacedItem) -> bool:
    return not (
        fr.x + fr.dx <= it.x  + EPS
        or it.x + it.pw <= fr.x  + EPS
        or fr.y + fr.dy <= it.y  + EPS
        or it.y + it.pd <= fr.y  + EPS
    )


def _split(fr: FreeRect, it: PlacedItem) -> List[FreeRect]:
    """Split fr around placed item, producing up to 4 maximal sub-rects."""
    ix1, ix2 = it.x, it.x + it.pw
    iy1, iy2 = it.y, it.y + it.pd
    fx1, fx2 = fr.x, fr.x + fr.dx
    fy1, fy2 = fr.y, fr.y + fr.dy

    candidates: List[FreeRect] = []

    # Left of item
    if ix1 > fx1 + EPS:
        candidates.append(FreeRect(fx1, fy1, ix1 - fx1, fr.dy))
    # Right of item
    if ix2 < fx2 - EPS:
        candidates.append(FreeRect(ix2, fy1, fx2 - ix2, fr.dy))
    # Below item (small Y)
    if iy1 > fy1 + EPS:
        candidates.append(FreeRect(fx1, fy1, fr.dx, iy1 - fy1))
    # Above item (large Y)
    if iy2 < fy2 - EPS:
        candidates.append(FreeRect(fx1, iy2, fr.dx, fy2 - iy2))

    return [c for c in candidates if c.valid()]


def _prune(rects: List[FreeRect]) -> List[FreeRect]:
    """Remove any FreeRect fully contained within another."""
    n = len(rects)
    dominated = [False] * n
    for i in range(n):
        if dominated[i]:
            continue
        for j in range(n):
            if i == j or dominated[j]:
                continue
            if rects[j].contains(rects[i]):
                dominated[i] = True
                break
    return [r for i, r in enumerate(rects) if not dominated[i]]
