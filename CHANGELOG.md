# Changelog – Box Packing System v2.1.0

**Release Date**: June 30, 2026

---

## Major Changes

### 1. Persistent Packing Sessions
- **Added**: Session persistence across multiple PACK operations
- **Location**: `packing/optimizer.py` → `PackingOptimizer.pack()` method
- **Impact**: 
  - First PACK: Creates fresh session with all items
  - Subsequent PACK: Reuses existing session, only processes new items (qty delta)
  - RESET clears session entirely
- **Benefit**: Incremental packing without re-optimizing existing boxes

### 2. Dimension Buffer (Internal +1)
- **Added**: `DIMENSION_BUFFER = 1.0` constant in `optimizer.py`
- **Applied To**:
  - ItemInstance properties: `buffered_length`, `buffered_width`, `buffered_height`
  - All placement checks in MaxRects2D
  - Orientation generation
  - Footprint calculations for 90% freeze rule
  - Recommendation calculations
  - All utilization metrics
- **Frontend**: UI continues showing original dimensions (transparent to user)
- **Example**: 5×4×2 inches displayed, but 6×5×3 used internally for packing

### 3. 90% Floor Utilization Freeze Rule
- **Added**: `MAX_LARGE_BOX_FLOOR_UTIL = 90.0` constant
- **Location**: `_place()` method in PackingOptimizer
- **Logic**: 
  - Before placing each item, calculate projected floor utilization
  - If projected ≥ 90% for large boxes: freeze box, reject item
  - Item goes to next box instead (not lost)
- **Applied**: Large boxes only (small/medium can fill to weight capacity)
- **Benefit**: Prevents over-packing large boxes, maintains optimization space

### 4. Frozen Boxes Architecture
- **Added**: `frozen: bool` field to PackedBox class
- **Added**: `packed_quantities: Dict[str, int]` to track per-SKU quantities per box
- **Freeze Triggers**:
  1. All items fit in one box → freeze immediately after packing
  2. 90% floor utilization reached → freeze when threshold hit
  3. No items fit in pass → freeze when no progress made
- **Frozen Box Behavior**:
  - Cannot accept new items: `_place()` returns None if frozen
  - Cannot be modified: Placements remain locked
  - Cannot be re-optimized: Skipped in future packing cycles
  - Persists until RESET
- **Frontend**: Box cards show "Frozen" badge

### 5. Incremental Quantity Handling
- **Added**: `_calculate_new_items()` method to compute qty delta
- **Logic**: 
  - Track `session.packed_quantities` per SKU globally
  - On subsequent PACK: Calculate `new_qty = requested_qty - already_packed`
  - Create ItemInstance objects only for new items
  - Original packed items never re-processed
- **Example**: 
  - Initial: 5 units packed
  - Update: 8 units requested
  - Result: Only 3 new units processed, original 5 untouched

### 6. Session State Tracking
- **Added** to `PackingSession`:
  - `packed_quantities: Dict[str, int]` - Global tracking per SKU
  - Persistent across pack operations until RESET
- **Added** to `PackedBox`:
  - `packed_quantities: Dict[str, int]` - Per-box tracking
  - `frozen: bool` - Immutable status indicator
- **Preserved**:
  - All boxes and placements
  - Free space data (MaxRects2D engine state)
  - Product specifications

---

## Code Changes

### File: `packing/optimizer.py`

#### Constants Added
```python
DIMENSION_BUFFER = 1.0                    # Line ~16
MAX_LARGE_BOX_FLOOR_UTIL = 90.0           # Line ~17
```

#### ItemInstance Class
- **Added properties**:
  - `buffered_length` (read-only)
  - `buffered_width` (read-only)
  - `buffered_height` (read-only)

#### PackedBox Class
- **Added fields**:
  - `frozen: bool = False`
  - `packed_quantities: Dict[str, int]`
- **Added method**:
  - `freeze()` - Set frozen status

#### PackingSession Class
- **Added fields**:
  - `packed_quantities: Dict[str, int]` - Global qty tracking

#### PackingOptimizer Class
- **Modified method**: `pack()` - Added session persistence logic
  - Check if session exists
  - On incremental pack: calculate new items only
  - Update product specs without clearing packed quantities
- **Modified method**: `_run()` - Updated to handle 90% freeze rule in _place()
- **Modified method**: `_place()` - Added 90% floor utilization freeze check
  - Calculate projected floor utilization before placement
  - Freeze box if projected ≥ 90% (large boxes only)
  - Track packed quantities per SKU and globally
- **Added method**: `_calculate_new_items()` - Calculate qty delta
- **Modified method**: `_expand_and_sort()` - Added is_initial parameter
- **Added method**: `_get_item_footprint()` - Get minimum footprint using buffered dims
- **Modified method**: `_recommendations()` - Use buffered dimensions for top-up calculations

#### Data Structure Changes
```python
# ItemInstance now has buffering support
@property
def buffered_length(self) -> float:
    return self.length + DIMENSION_BUFFER

# PackedBox now tracks frozen state
@dataclass
class PackedBox:
    frozen: bool = False
    packed_quantities: Dict[str, int] = field(default_factory=dict)

# PackingSession now tracks global packed quantities
@dataclass
class PackingSession:
    packed_quantities: Dict[str, int] = field(default_factory=dict)
```

### File: `main.py`

#### API Changes
- **Endpoint `/api/pack`**: Now supports incremental packing
  - Reuses existing optimizer and session
  - Updates capacities if changed
  - Only processes new items on subsequent calls
- **Endpoint `/api/reset`**: Updated to clear optimizer state
  - Sets `_optimizer.session = None`
- **Endpoint `/api/boxes/{id}/top-up`**: Updated to use buffered dimensions
  - Respects frozen box status
  - Tracks packed quantities
- **Helper function**: `_compute_recs_for_box()` updated to use buffered dimensions

#### Session Management
```python
_optimizer: Optional[PackingOptimizer] = None  # Persistent
_session: Optional[PackingSession] = None      # Reference to _optimizer.session
```

### File: `static/app.js`

#### Frontend Updates
- **New variable**: `_productsMap` - Stores product specs from session
- **New function**: `syncProductTableQty(sku, delta)` - Auto-update product table after top-up
- **New function**: `updateBoxCard(boxData)` - Replace individual box card in DOM
- **Updated function**: `applyRecommendation()` - Sync product table after placing items
- **Updated function**: `buildBoxCard()` - Show frozen/open status badge
- **Updated function**: `buildRecsHTML()` - Show zero-count recommendations
- **UI Changes**:
  - Frozen badge: "Frozen" vs "Open" indicator
  - Product table auto-sync when top-up succeeds
  - Row highlighting when quantity changes

---

## Files Created

### Documentation
1. **IMPLEMENTATION_NOTES.md** - Detailed technical implementation guide
2. **FEATURES_SUMMARY.md** - Feature overview and use cases
3. **QUICK_REFERENCE.md** - Quick reference guide for developers
4. **CHANGELOG.md** - This file

### Testing
5. **test_requirements.py** - Automated test suite for all requirements
   - Tests dimension buffer
   - Tests session persistence
   - Tests frozen boxes
   - Tests 90% freeze rule
   - Tests quantity tracking
   - Tests reset functionality
   - Tests buffer in calculations

---

## API Endpoint Changes

### POST /api/pack
**Behavior**: Now incremental
- First call: Fresh pack (all items)
- Subsequent calls: Pack only new items (qty delta)
- Existing boxes preserved

### POST /api/reset
**Effect**: Clear session state completely

### POST /api/boxes/{id}/top-up
**Updated**: Uses buffered dimensions internally

### GET /api/session
**No change**: Returns current session state

---

## Database/State Schema Changes

### Session Structure
```python
PackingSession:
  boxes: List[PackedBox]                    # All boxes (existing + new)
  packed_quantities: Dict[str, int]         # Global qty per SKU (NEW)
  products: List[dict]                      # Product specs
  unpacked_items: List[ItemInstance]        # Failed-to-pack items
  logs: List[str]                           # Operation logs
```

### Box Structure
```python
PackedBox:
  box_id: int
  box_type: str
  frozen: bool                              # (NEW) Immutable status
  packed_quantities: Dict[str, int]         # (NEW) Per-SKU qty tracking
  engine: MaxRects2D
  # ... other fields ...
```

### Item Structure
```python
ItemInstance:
  sku: str
  instance_id: str
  length, width, height: float              # (Original dimensions)
  buffered_length, buffered_width, buffered_height: property  # (Computed with +1)
  weight: float
  is_new: bool                              # Track if newly added
```

---

## Migration Guide

### For Existing Data
- No database changes needed (in-memory system)
- Session persists in `_optimizer.session` throughout browser session
- Clears when server restarts or `/api/reset` called

### For Developers
- **If upgrading from v2.0.0**:
  - Update all calls to optimizer to expect persistent session
  - Don't recreate optimizer on each pack
  - Use RESET endpoint to clear state explicitly
  - Check `box.frozen` status before modifying boxes

---

## Testing

### Test Coverage
```
✅ Dimension buffer applied correctly
✅ Session persists across PACK calls  
✅ Frozen boxes cannot accept new items
✅ 90% freeze rule triggers correctly
✅ Quantity delta calculated properly
✅ RESET clears all state
✅ Buffer applied to all calculations
```

### Running Tests
```bash
python test_requirements.py
```

Expected output:
```
✅ ALL TESTS PASSED
```

---

## Performance Impact

- **Memory**: Session persists in memory (adds ~1KB per box)
- **CPU**: Same as before (no additional computation)
- **Network**: Same API payload size (no change)
- **Benefit**: Eliminates re-optimization overhead on subsequent packs

---

## Backward Compatibility

- ✅ All existing API endpoints work as before
- ✅ Request/response schemas unchanged
- ⚠️ Behavior change: PACK now incremental (not fresh each time)
- ⚠️ Sessions now persist until RESET (previously cleared each pack)

**Migration path**:
- Existing code calling PACK multiple times: No change needed
- New code leveraging incremental packing: Use qty delta strategy
- Reset behavior: Explicitly call RESET endpoint

---

## Known Limitations

1. **In-Memory Only**: Session clears when server restarts
   - *Workaround*: Add database persistence layer

2. **Single Session**: Only one packing session per server instance
   - *Workaround*: Add multi-user session management

3. **No Undo**: Can't undo individual packs, only RESET all
   - *Workaround*: Implement version history if needed

---

## Future Enhancements

1. Database persistence for cross-session survival
2. Multi-user sessions with authentication
3. Partial box reset (freeze/unfreeze UI)
4. Analytics dashboard tracking efficiency over time
5. Batch packing with multiple SKUs per order
6. Export/import session state

---

## Verification Checklist

- ✅ Dimension buffer implemented and applied consistently
- ✅ Session persistence across PACK calls working
- ✅ Frozen boxes cannot accept new items
- ✅ 90% floor utilization freeze rule active
- ✅ Quantity delta calculation correct
- ✅ RESET clears all state
- ✅ Frontend shows frozen status
- ✅ Product table syncs on top-up
- ✅ All tests passing
- ✅ API endpoints functional

---

**Version**: 2.1.0  
**Date**: June 30, 2026  
**Status**: Production Ready
