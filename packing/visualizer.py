"""
2-D floor-plan visualizer for packed boxes.

Generates a single top-down PNG per box showing:
  - Box boundary
  - Placed item rectangles (coloured, labelled with SKU + dims)
  - Remaining free-space rectangles (green dashed)
  - Utilisation statistics in the title
  - Colour legend
"""

from __future__ import annotations

import io
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe

from .optimizer import DIMENSION_BUFFER_PCT

_BUFFER_RATIO = 1 + DIMENSION_BUFFER_PCT


# ---------------------------------------------------------------------------
# Colour palette (colour-blind-friendly)
# ---------------------------------------------------------------------------

_PALETTE = [
    "#2563EB", "#D97706", "#059669", "#DC2626", "#7C3AED",
    "#DB2777", "#0891B2", "#65A30D", "#EA580C", "#9333EA",
    "#0284C7", "#16A34A", "#CA8A04", "#E11D48", "#6366F1",
]


def _sku_color(sku: str, index: Dict[str, int]) -> str:
    if sku not in index:
        index[sku] = len(index) % len(_PALETTE)
    return _PALETTE[index[sku]]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_box_png(box_dict: dict, dpi: int = 140) -> bytes:
    """
    Generate a 2-D top-down floor-plan PNG for one packed box.
    Items are coloured rectangles; free spaces are green dashed outlines.
    """
    bl   = box_dict["box_l"]       # floor length  (X)
    bw   = box_dict["box_w"]       # floor width   (Y)
    bh   = box_dict["box_h"]       # box height    (info only)
    items      = box_dict["items"]
    free_rects = box_dict["free_rects"]
    box_name   = box_dict["box_name"]
    box_id     = box_dict["box_id"]
    floor_u    = box_dict["floor_util"]
    vol_u      = box_dict["volume_util"]
    cap_u      = box_dict["capacity_util"]
    used_w     = box_dict["used_weight"]
    capacity   = box_dict["capacity"]
    item_count = box_dict["item_count"]

    # ── Figure sizing ────────────────────────────────────────────────────
    aspect = bl / bw if bw > 0 else 1.0
    base = 7.0
    if aspect >= 1.0:
        figw = base + 3.2        # +3.2 for right-side legend
        figh = base / aspect + 1.8
    else:
        figw = base * aspect + 3.2
        figh = base + 1.8
    figw = max(figw, 8.0)
    figh = max(figh, 5.0)

    fig, ax = plt.subplots(figsize=(figw, figh))
    fig.patch.set_facecolor("#F8FAFC")
    ax.set_facecolor("#FFFFFF")

    margin_x = bl * 0.03
    margin_y = bw * 0.03
    ax.set_xlim(-margin_x, bl + margin_x)
    ax.set_ylim(-margin_y, bw + margin_y)
    ax.set_aspect("equal")
    ax.set_xlabel("Length (in)", fontsize=9, labelpad=6)
    ax.set_ylabel("Width (in)",  fontsize=9, labelpad=6)
    ax.tick_params(labelsize=8)
    ax.grid(True, color="#E2E8F0", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)

    # ── Box boundary ─────────────────────────────────────────────────────
    ax.add_patch(mpatches.FancyBboxPatch(
        (0, 0), bl, bw,
        boxstyle="square,pad=0",
        fill=False, edgecolor="#1E293B", linewidth=2.5, zorder=4,
    ))

    # ── Free spaces ──────────────────────────────────────────────────────
    for fr in free_rects:
        if fr["dx"] < 1e-6 or fr["dy"] < 1e-6:
            continue
        ax.add_patch(mpatches.Rectangle(
            (fr["x"], fr["y"]), fr["dx"], fr["dy"],
            facecolor="#DCFCE7", edgecolor="#16A34A",
            linewidth=0.9, linestyle="--", alpha=0.55, zorder=1,
        ))

    # ── Placed items ─────────────────────────────────────────────────────
    sku_index: Dict[str, int] = {}
    for it in items:
        color = _sku_color(it["sku"], sku_index)
        pw, pd = it["pw"], it["pd"]

        # The buffer is a uniform +20% on every axis, so dividing the placed
        # (buffered) footprint by the ratio recovers the true item footprint
        # on each axis independently — correct regardless of orientation.
        actual_w = pw / _BUFFER_RATIO
        actual_h = pd / _BUFFER_RATIO
        buf_w = pw - actual_w
        buf_h = pd - actual_h

        # Draw buffer zone as dashed rectangle (outer)
        ax.add_patch(mpatches.Rectangle(
            (it["x"], it["y"]), pw, pd,
            facecolor="none", edgecolor="#94A3B8",
            linewidth=0.5, linestyle=":", alpha=0.6, zorder=1.5,
        ))

        # Draw actual item area (inner solid rectangle)
        # Shrink from buffered to show the actual item inside
        actual_x = it["x"] + buf_w * 0.5
        actual_y = it["y"] + buf_h * 0.5
        
        if actual_w > 0.05 and actual_h > 0.05:
            ax.add_patch(mpatches.Rectangle(
                (actual_x, actual_y), actual_w, actual_h,
                facecolor=color, edgecolor="#1E293B",
                linewidth=1.0, alpha=0.85, zorder=2,
            ))
            
            # Labels on actual item area
            cx = actual_x + actual_w / 2
            cy = actual_y + actual_h / 2
            min_side = min(actual_w, actual_h)
        else:
            # Fallback if no room for inner box
            ax.add_patch(mpatches.Rectangle(
                (it["x"], it["y"]), pw, pd,
                facecolor=color, edgecolor="#1E293B",
                linewidth=0.8, alpha=0.85, zorder=2,
            ))
            
            cx = it["x"] + pw / 2
            cy = it["y"] + pd / 2
            min_side = min(pw, pd)

        # Font size scales with item size but stays readable
        fs_sku  = max(5.0, min(11.0, min_side * 2.8))
        fs_dim  = max(4.0, min( 8.0, min_side * 1.9))

        # SKU label
        ax.text(
            cx, cy, it["sku"],
            ha="center", va="center",
            fontsize=fs_sku, fontweight="bold", color="white",
            zorder=3, clip_on=True,
            path_effects=[pe.withStroke(linewidth=1.2, foreground="black")],
        )

        # Dimension annotation below SKU (only if item is big enough)
        if min_side >= 0.8:
            dim_txt = f'{actual_w:.2f}"×{actual_h:.2f}"'
            ax.text(
                cx, cy - min_side * 0.22, dim_txt,
                ha="center", va="center",
                fontsize=fs_dim, color="white", alpha=0.90,
                zorder=3, clip_on=True,
            )

    # ── Title ────────────────────────────────────────────────────────────
    title = (
        f"Box #{box_id}  –  {box_name}\n"
        f'{bl}" × {bw}" × {bh}"    '
        f"Items: {item_count}    "
        f"Floor: {floor_u:.1f}%    "
        f"Volume: {vol_u:.1f}%    "
        f"Weight: {used_w:.2f}/{capacity} lbs ({cap_u:.1f}%)"
    )
    ax.set_title(title, fontsize=9, pad=10, color="#1E293B")

    # ── Legend ───────────────────────────────────────────────────────────
    handles = []
    for sku, idx in sorted(sku_index.items(), key=lambda kv: kv[1]):
        handles.append(mpatches.Patch(
            facecolor=_PALETTE[idx % len(_PALETTE)],
            edgecolor="black", linewidth=0.6, label=sku,
        ))
    handles.append(mpatches.Patch(
        facecolor="#DCFCE7", edgecolor="#16A34A",
        linestyle="--", linewidth=0.9, label="Free Space", alpha=0.6,
    ))
    handles.append(mpatches.Patch(
        facecolor="none", edgecolor="#94A3B8",
        linestyle=":", linewidth=0.5, label=f"Buffer (+{int(DIMENSION_BUFFER_PCT * 100)}%)", alpha=0.6,
    ))
    if handles:
        ax.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(1.01, 1.0),
            fontsize=8,
            framealpha=0.9,
            borderpad=0.8,
            handlelength=1.4,
        )

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
