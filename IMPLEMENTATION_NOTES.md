# Box Packing System – Requirements Implementation Summary

## Date: June 30, 2026

This document details the implementation of persistent packing sessions with dimension buffering, freezing rules, and incremental inventory handling.

---

## 1. Persistent Packing Session ✓

### Implementation Location
- **File**: `packing/optimizer.py`
- **Class**: `PackingOptimizer`
- **Method**: `pack(products: List[dict])`

### Behavior
- **First PACK**: Creates fresh session, packs all items, freezes all boxes
- **Subsequent PACK**: Incremental mode—reuses existing session, only packs new items (qty delta)
- **RESET**: Clears entire session state (`self.session = None`)

### Key State Tracking
```python
PackingOptimizer:
  self.session: Optional[PackingSession]  # Persistent across pack() calls

PackingSession:
  self.boxes: List[PackedBox]  # All boxes (frozen and open)
  self.packed_quantities: Dict[str, int]  # Global qty per SKU
  self.products: List[dict]  # Product specs
```

### Logic Flow (Incremental Pack)
1. Check if session exists; if None, create fresh
2. Update product specs (dimensions/weight) but preserve existing packed_quantities
3. Calculate delta: `new_qty = requested_qty - packed_quantities[sku]`
4. Expand only new items to ItemInstance objects
5. Run packing only on new items
6. All existing boxes remain untouched
7. New items use remaining free space in boxes or create new boxes

---

## 2. Dimension Buffer (Internal +1) ✓

### Implementation
- **Constant**: `DIMENSION_BUFFER = 1.0` (top of `optimizer.py`)
- **Applied**: All packing logic, calculations, orientations, fit checks

### Where Buffer is Applied
```python
# ItemInstance properties (read-only computed)
@property
def buffered_length(self) -> float:
    return self.length + DIMENSION_BUFFER

@property
def buffered_width(self) -> float:
    return self.width + DIMENSION_BUFFER

@property
def buffered_height(self) -> float:
    return self.height + DIMENSION_BUFFER
```

### Usage Points
1. **Placement Check** (packing algorithm):
   ```python
   placed = box.engine.place_item(
       item.sku, item.instance_id,
       item.buffered_length, item.buffered_width, item.buffered_height, item.weight
   )
   ```

2. **Footprint Calculation** (for 90% freeze check):
   ```python
   def _get_item_footprint(self, item: ItemInstance) -> tuple:
       orientations = generate_orientations(
           item.buffered_length, item.buffered_width, item.buffered_height, 100.0
       )
   ```

3. **Recommendations** (top-up suggestions):
   ```python
   buffered_l = l + DIMENSION_BUFFER
   buffered_w = w + DIMENSION_BUFFER
   buffered_h = h + DIMENSION_BUFFER
   placed = sim.place_item(..., buffered_l, buffered_w, buffered_h, ...)
   ```

4. **Utilization Calculations**: Buffered dims used in all area/volume metrics

### Frontend Display
- UI shows **original dimensions** to users (5 × 4 × 2)
- Backend uses **buffered dimensions** internally (6 × 5 × 3)
- Transparent to user

---

## 3. 90% Floor Utilization Freeze Rule ✓

### Constant
```python
MAX_LARGE_BOX_FLOOR_UTIL = 90.0
```

### Rule Implementation
**Location**: `_place()` method, before item placement

```python
def _place(self, box: PackedBox, item: ItemInstance) -> Optional[PlacedItem]:
    if box.frozen:
        return None
    
    # NEW: Check 90% threshold for large boxes ONLY
    if box.box_type == "large":
        item_pw, item_pd = self._get_item_footprint(item)
        projected_area = box.engine.used_floor_area() + (item_pw * item_pd)
        box_floor_area = box.box_l * box.box_w
        projected_util = (projected_area / box_floor_area * 100.0) if box_floor_area > EPS else 0.0
        
        if projected_util >= MAX_LARGE_BOX_FLOOR_UTIL - EPS:
            # Freeze box immediately
            box.freeze()
            return None  # Item rejected
    
    # ... continue with placement ...
```

### Behavior
1. **Before placing each item**, calculate:
   - `projected_utilization = (used_floor_area + item_area) / box_floor_area * 100`
2. **If projected ≥ 90%**:
   - Freeze the box
   - Reject the item
   - Item goes into next box (not lost)
3. **Applied only to large boxes**
   - Small/medium boxes can fill to weight capacity
4. **Once frozen**: Box cannot accept any more items

---

## 4. Frozen Boxes ✓

### Data Structure
```python
@dataclass
class PackedBox:
    frozen: bool = False  # Initially open
    packed_quantities: Dict[str, int]  # Track qty per SKU
```

### Freeze Trigger Points
1. **After "fit all" optimization**: All items fit in one box → freeze immediately
2. **90% floor utilization**: Large box reaches 90% → freeze during _place()
3. **No items fit**: Zero placements in a pass → freeze at end of pass

### Frozen Box Behavior
- **Cannot accept new items**: `_place()` returns None
- **Cannot be modified**: Existing placement/layout preserved
- **Cannot be re-optimized**: Skipped in future pack cycles
- **Remains until RESET**: Only `/api/reset` clears frozen status

### Serialization
```python
def to_dict(self) -> dict:
    return {
        "frozen": self.frozen,  # Included in API response
        # ... other fields ...
    }
```

---

## 5. Quantity Increase Handling ✓

### Example Workflow
```
Initial pack:  SKU-A qty 5 → 5 items packed, session.packed_quantities["SKU-A"] = 5

User updates:  SKU-A qty 5 → 8
Next pack:     Delta = 8 - 5 = 3 units
               Only 3 new items created and packed
               Original 5 remain untouched in their boxes
```

### Implementation
**Location**: `_calculate_new_items()` method

```python
def _calculate_new_items(self, products: List[dict]) -> List[ItemInstance]:
    for prod in products:
        sku = prod["sku"]
        requested_qty = int(prod["quantity"])
        already_packed = self.session.packed_quantities.get(sku, 0)
        new_qty = max(0, requested_qty - already_packed)
        
        if new_qty > 0:
            # Create only `new_qty` items
            for i in range(new_qty):
                instance_num = already_packed + i + 1
                items.append(ItemInstance(
                    sku=sku,
                    instance_id=f"{sku}-{instance_num}",
                    # ... other fields ...
                ))
```

### Key Points
- **Original units** are never touched
- **No repacking** of existing items
- **No reshuffling** across boxes
- **New units** use remaining space in non-frozen boxes or new boxes
- **Instance numbering** preserved: SKU-A-1 through SKU-A-8

---

## 6. Incremental Packing Workflow ✓

### Full Workflow (End-to-End)

#### Step 1: Load Session (if exists)
```python
def pack(self, products: List[dict]) -> PackingSession:
    if self.session is None:
        # First pack
        self.session = PackingSession(products=products)
    else:
        # Subsequent pack – keep session alive
        # Update product specs (not qty)
        self.session.products = updated_specs
```

#### Step 2: Calculate New Items Only
```python
if self.session.boxes:  # Boxes already exist
    new_items = self._calculate_new_items(products)  # Delta only
else:
    new_items = self._expand_and_sort(products, is_initial=True)
```

#### Step 3: Pack New Items
```python
self._run(new_items)  # Uses existing boxes' free space where possible
```

#### Step 4: Persist State
All state is automatically preserved in `self.session`:
- ✅ Existing boxes (with placements)
- ✅ Frozen status per box
- ✅ Free space data (in `MaxRects2D.free_rects`)
- ✅ Packed quantity per SKU/box
- ✅ All item placements

### Packing Algorithm Details

**Phase 1: "Fit All" Check**
- Try to fit all remaining new items into a single new box
- If success → create box, pack all items, freeze

**Phase 2: "Fill Largest" Greedy**
- Create new large box
- Fill items one-by-one (BSSF heuristic)
- Stop when:
  - 90% floor utilization reached (large boxes)
  - No items fit
  - Out of capacity
- Freeze box
- Repeat for next batch

### Boxes Never Rebuilt
- Existing boxes are **read-only** during incremental pack
- Their `engine.free_rects` remain stable
- New items only use remaining free space
- Box placements never change

---

## 7. RESET Behavior ✓

**Endpoint**: `POST /api/reset`

```python
def reset(self) -> None:
    """Clear all session state."""
    self.session = None
```

**Effect**:
- Clears all boxes
- Clears all placements
- Clears all frozen status
- Resets packed quantities
- Clears optimization state
- Next PACK starts fresh

---

## 8. API Endpoints – Updated Behavior

### `POST /api/pack`
**Request**: `{ products: [...], capacities: {...} }`

**Behavior**:
- If no session: Fresh pack (all products)
- If session exists: Incremental pack (qty delta only)
- Returns full session with all boxes (old + new)

**Response**: `{ success: true, data: { boxes, unpacked, analytics, products_map, logs } }`

### `POST /api/reset`
**Effect**: Clear session state

**Response**: `{ success: true, message: "Session reset." }`

### `POST /api/boxes/{id}/top-up`
**Behavior**: Pack extra units of one SKU into a specific box
- Respects box frozen status (will fail if frozen)
- Uses buffered dimensions
- Updates product table on success

### `GET /api/session`
**Response**: Full current session state

---

## 9. Frontend Changes (app.js)

### New Features
1. **Quantity Sync**: After top-up, product table quantity auto-updates
2. **Frozen Badge**: Shows "Frozen" or "Open" on each box card
3. **Recommendations**: Shows how many extra units can fit (filtered by frozen status)

### Example Flow
```
User: SKU-A qty 5 → Pack
Result: 5 units packed in Box #1 (frozen)

User: Changes input to SKU-A qty 8 → Pack again
Backend: Packs 3 new units in new Box #2
Frontend: Shows both Box #1 and Box #2
```

---

## 10. Data Structures

### ItemInstance (with buffering support)
```python
@dataclass
class ItemInstance:
    sku: str
    instance_id: str
    seq: int
    length: float         # Original dimension
    width: float          # Original dimension
    height: float         # Original dimension
    weight: float
    is_new: bool = True
    
    # Read-only properties with buffer
    @property
    def buffered_length(self) -> float:
        return self.length + DIMENSION_BUFFER
```

### PackedBox (with frozen status)
```python
@dataclass
class PackedBox:
    box_id: int
    frozen: bool = False  # NEW: Freezing support
    packed_quantities: Dict[str, int]  # NEW: Per-SKU tracking
    engine: MaxRects2D    # 2-D packing engine
    # ... other fields ...
```

### PackingSession (with incremental support)
```python
@dataclass
class PackingSession:
    boxes: List[PackedBox]
    packed_quantities: Dict[str, int]  # NEW: Global qty tracking
    products: List[dict]
    # ... other fields ...
```

---

## 11. Constants Reference

| Constant | Value | Purpose |
|----------|-------|---------|
| `DIMENSION_BUFFER` | 1.0 | Internal dimension cushion (inches) |
| `MAX_LARGE_BOX_FLOOR_UTIL` | 90.0 | Freeze threshold for large boxes (%) |
| `EPS` | 1e-9 | Floating-point epsilon |

---

## 12. Testing Scenarios

### Scenario 1: Initial Pack
```
Input: SKU-A (qty 5), SKU-B (qty 3)
→ All 8 items packed into boxes
→ All boxes frozen
→ packed_quantities = {"SKU-A": 5, "SKU-B": 3}
```

### Scenario 2: Quantity Increase
```
Existing: SKU-A (5 packed) in Box #1 (frozen)
Input: SKU-A (qty 8)
→ Delta = 3 units calculated
→ 3 new items packed into Box #2
→ Box #1 untouched
→ packed_quantities = {"SKU-A": 8}
```

### Scenario 3: 90% Freeze
```
Large box with 89% floor utilization
→ Next item would push to 92%
→ Item rejected, box frozen
→ Item packed into new box instead
```

### Scenario 4: Reset
```
Existing: Boxes #1-5 with items
→ User clicks RESET
→ Session cleared
→ Next pack starts fresh
```

---

## 13. Verification Checklist

- ✅ Dimension buffer applied to all placement logic
- ✅ Dimension buffer applied to all calculations
- ✅ UI shows original dimensions
- ✅ 90% freeze rule on large boxes only
- ✅ Frozen boxes cannot accept new items
- ✅ Quantity delta correctly calculated
- ✅ No repacking of existing items
- ✅ Session persists across PACK calls
- ✅ RESET clears all state
- ✅ Incremental packing uses remaining space
- ✅ New boxes created when needed
- ✅ Boxes remain frozen until RESET
- ✅ Product table sync on top-up

---

## 14. Future Enhancements (Optional)

1. **Persistent Storage**: Save session to database between browser sessions
2. **Partial Reset**: Reset only specific boxes instead of entire session
3. **Freeze/Unfreeze UI**: Allow manual box freezing before 90% threshold
4. **Dimension Validation**: Warn user if buffer causes items to exceed box
5. **Analytics Dashboard**: Track packing efficiency over time

---

**End of Implementation Notes**
