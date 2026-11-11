# Box Packing System – Quick Reference Guide

## 🎯 Key Concepts (30 seconds)

| Concept | Meaning |
|---------|---------|
| **Persistent Session** | Boxes survive across PACK clicks – only new items processed |
| **Dimension Buffer** | All dimensions get +1 internally (5×4×2 → 6×5×3 internally) |
| **Frozen Box** | Cannot accept new items, cannot be modified, persists until RESET |
| **90% Freeze Rule** | Large boxes freeze when floor utilization reaches 90% |
| **Quantity Delta** | Only the increase in quantity is packed (8-5=3 new items) |
| **Incremental Packing** | Each PACK processes only new inventory, never touches existing items |

---

## ⚙️ System Workflow

```
Initial Pack
├─ User enters: SKU-A (5), SKU-B (3)
├─ Click PACK
└─ Result: Boxes created, frozen, ready for shipment

Receive Restock (Qty +3 of SKU-A)
├─ User updates: SKU-A (8)
├─ Click PACK
├─ System calculates: 8 - 5 = 3 new items
└─ Result: 3 items packed into new/available space, first 5 remain untouched

Start Fresh
├─ Click RESET
├─ Session cleared entirely
└─ Next PACK starts with clean slate
```

---

## 📋 API Quick Reference

### PACK Endpoint
```
POST /api/pack
Request:  { products: [...], capacities: {...} }
Response: Full session with boxes, analytics, logs
Behavior: Incremental if session exists, fresh if not
```

### RESET Endpoint
```
POST /api/reset
Effect:   Clear all session state
Response: { success: true, message: "..." }
```

### Top-Up Endpoint
```
POST /api/boxes/{id}/top-up
Request:  { sku: "SKU-A", quantity: 3 }
Effect:   Pack additional items into specific box
Response: Updated box data + placement count
```

### Session Endpoint
```
GET /api/session
Response: Current session state (boxes, items, logs)
```

---

## 🔌 Configuration Constants

Edit `packing/optimizer.py` to adjust:

```python
DIMENSION_BUFFER = 1.0              # Buffer for all dimensions
MAX_LARGE_BOX_FLOOR_UTIL = 90.0     # Large box freeze threshold
EPS = 1e-9                          # Floating-point tolerance
```

---

## 🧠 Packing Algorithm (Simplified)

### Phase 1: "Fit All" Check
- Can all remaining items fit in one box?
- If YES → Create box, pack all, freeze immediately
- If NO → Go to Phase 2

### Phase 2: "Fill Largest" Greedy
- Create new large box
- Pack items one-by-one (best fit)
- Stop when:
  - 90% floor util reached (large boxes) → freeze
  - No items fit → freeze
  - Out of capacity → freeze
- Return to Phase 1 for remaining items

### Result
- Minimal boxes used
- All boxes frozen after packing
- New items can be added incrementally later

---

## 🔍 Data Flow

```
User Input
    ↓
/api/pack (POST)
    ↓
PackingOptimizer.pack()
    ├─ Load existing session (if exists)
    ├─ Calculate qty delta
    ├─ Create ItemInstance objects (buffered dims)
    ├─ Run packing algorithm
    └─ Update session state
    ↓
Response: { boxes, unpacked, analytics, logs }
    ↓
Frontend Render
    ├─ Display boxes with frozen status
    ├─ Show recommendations
    ├─ Update product table
    └─ Display visualizations on demand
```

---

## 💾 Session Persistence

### What Gets Saved
✅ All boxes (placement, dimensions, config)  
✅ Frozen status per box  
✅ Free space data (MaxRects2D engine state)  
✅ Packed quantity per SKU (global + per-box)  
✅ Product specifications  

### What Gets Cleared
🔄 Only by RESET:
- All boxes
- All placements
- Frozen status
- Packed quantities
- Session state

---

## 📊 Utilization Metrics

```
Floor Utilization = (used_floor_area / box_floor_area) * 100
├─ Uses buffered dimensions for placement
└─ Triggers 90% freeze on large boxes

Volume Utilization = (used_volume / box_volume) * 100
├─ Uses buffered dimensions
└─ Informational only

Capacity Utilization = (used_weight / max_weight) * 100
├─ Based on actual weight
└─ Secondary constraint
```

---

## 🛠️ Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| Items not packing | Dimensions too large | Reduce dims or increase box capacity |
| Box frozen too early | 90% threshold reached | Increase `MAX_LARGE_BOX_FLOOR_UTIL` or reduce item footprint |
| Existing items moved | Session cleared | Don't click RESET if you want to preserve boxes |
| Quantity not updating | Session didn't persist | Verify `/api/session` returns existing boxes |
| Top-up failing | Box is frozen | Can only top-up into open boxes |

---

## 🎓 Examples

### Example 1: Simple Initial Pack
```
Input:
  SKU-A: 4" × 3" × 2", 5 lbs, quantity 2
  
Process:
  Internal dims: 5" × 4" × 3"
  Both fit in Small box (8.69" × 5.44")
  
Output:
  Box #1 (Small): 2 items, frozen, floor_util 15%
```

### Example 2: Incremental Addition
```
Existing:
  Box #1 (Small): SKU-A ×2, frozen

New Input:
  SKU-A: quantity 5 (changed from 2)
  
Process:
  Delta = 5 - 2 = 3 new items
  Don't fit in Box #1 (frozen)
  Create Box #2 (Large)
  Pack 3 items into Box #2
  
Output:
  Box #1: 2 items, frozen, unchanged
  Box #2: 3 items, frozen, newly created
```

### Example 3: 90% Freeze in Action
```
Large box: 12.25" × 12.25" (floor area 150 sq in)
Packed items: 135 sq in (90% utilized)

Next item: 10 sq in footprint
Projected: 145/150 = 96.7%

Result:
  Item rejected (96.7% >= 90%)
  Box #1 frozen
  Item packed into Box #2
```

---

## 📞 Key Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI server, endpoints |
| `packing/optimizer.py` | Packing logic, session mgmt |
| `packing/maxrects.py` | 2D bin-packing algorithm |
| `packing/usps_boxes.py` | Box configurations |
| `static/app.js` | Frontend logic |
| `templates/index.html` | UI layout |

---

## 🚀 Running the System

```bash
# Start server
python main.py

# Run in browser
http://localhost:8000

# Run tests
python test_requirements.py
```

---

## 🔐 Important Notes

- **Dimension buffer is transparent** - Users see original dimensions
- **Frozen boxes are immutable** - Cannot be re-packed, reshaped, or optimized
- **Incremental only** - Second PACK won't process already-packed items
- **Delta calculation** - Only qty difference is processed
- **All state in-memory** - Clears when server restarts (add DB for persistence)

---

**Version**: 2.1.0 (Persistent + Incremental)  
**Last Updated**: June 30, 2026
