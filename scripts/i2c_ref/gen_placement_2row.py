"""
gen_placement_2row.py

Second placement pass (design_notes.md section 37): two stacked rows,
channel / row1 / channel(shared) / row2 / channel, instead of the
single-row trial's one row. User-specified packing policy:

  1. Split the netlist into two cell groups by simple cumulative-width
     bisection (first N instances, in file order, until half the total
     width is reached -> row1; the rest -> row2). This is the
     "simple split" the user agreed to try first, accepting that it
     may produce more cross-row nets than a locality-aware (FM-style)
     partition would -- see design_notes.md section 37 for the
     resulting cross-row net count, which determines whether that
     optimization becomes necessary.
  2. TAP2 columns go down FIRST, at a fixed X grid shared by BOTH rows
     (so a TAP2 in row1 and the TAP2 directly above/below it in row2
     land at identical X) -- not derived from either row's own cell
     content. Only after the TAP grid is fixed are netlist cells
     packed into the gaps between consecutive TAP columns, per row,
     independently. Any leftover width in a gap (a row's cells didn't
     exactly fill it) is padded with FILL2/FILL3 (section 35.10).
     Because both rows share the exact same TAP grid, this also
     guarantees both rows end up the same total width -- solving the
     row-width-matching problem for free.

Gap width is expressed in 5.4um TRACK units (TAP_INTERVAL_TRACKS),
not raw um, because every cell/filler width is itself a track
multiple -- an interval that isn't itself a track multiple could never
be exactly filled. 111 tracks (599.4um) was chosen to stay close to
the single-row trial's 600um TAP spacing.
"""
import json
import sys
from collections import deque

sys.path.insert(0, "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/script")
from lef_parser import parse_lef  # noqa: E402
from netlist_parser import parse_netlist  # noqa: E402

OUT_JSON = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/placement_2row.json"

TRACK_UM = 5.4
TAP_INTERVAL_TRACKS = 111  # 599.4um
TAP_CELL = "TAP2"


def fill_combo(tracks):
    """List of filler cell type names (FILL2=2 tracks, FILL3=3 tracks)
    summing to exactly `tracks` tracks, or None if impossible (only
    tracks==1 is impossible; every other non-negative value is
    reachable)."""
    if tracks == 0:
        return []
    if tracks == 1:
        return None
    n3, rem = divmod(tracks, 3)
    if rem == 0:
        return ["FILL3"] * n3
    if rem == 2:
        return ["FILL3"] * n3 + ["FILL2"]
    # rem == 1
    if n3 >= 1:
        return ["FILL3"] * (n3 - 1) + ["FILL2"] * 2
    return None  # tracks == 1 with no FILL3 to borrow from -- caller must avoid this


def split_rows(instances, widths):
    total_w = sum(widths[typ] for typ, _, _ in instances)
    half = total_w / 2.0
    row1, row2 = [], []
    acc = 0.0
    for typ, name, pins in instances:
        if acc < half:
            row1.append((typ, name, pins))
        else:
            row2.append((typ, name, pins))
        acc += widths[typ]
    return row1, row2


def _gaps_needed(cell_queue, widths):
    """Dry-run the greedy per-gap packing (no placement, just counts how
    many TAP_INTERVAL_TRACKS-wide gaps this row's cells need). Must
    mirror pack_row's per-gap logic exactly (including the leftover==1
    undo rule) or the predicted gap count can be too low."""
    q = deque(cell_queue)
    n_gaps = 0
    while q:
        gap_tracks_left = TAP_INTERVAL_TRACKS
        seg = []
        while q:
            typ, name, pins = q[0]
            w_tracks = round(widths[typ] / TRACK_UM)
            if w_tracks > gap_tracks_left:
                break
            seg.append(q.popleft())
            gap_tracks_left -= w_tracks
        if not seg:
            raise SystemExit("a single cell is wider than one TAP gap -- widen TAP_INTERVAL_TRACKS")
        if gap_tracks_left == 1:
            typ, name, pins = seg.pop()
            q.appendleft((typ, name, pins))
        n_gaps += 1
    return n_gaps


def pack_row(cell_queue, widths, n_gaps):
    """Pack cell_queue into EXACTLY n_gaps consecutive TAP_INTERVAL_TRACKS
    -wide gaps (TAP2 at every boundary, so n_gaps+1 TAP2 columns total),
    FILL2/FILL3 padding any per-gap leftover. n_gaps must be >= what this
    row's own cells need (section 37: both rows are packed to the SAME
    n_gaps -- whichever row naturally needs more -- so their total widths
    come out identical; the other row just gets extra FILL-only gaps or
    more FILL within a gap). Returns (placed_list, total_width_um)."""
    placed = []
    x = 0.0
    tap_idx = 0
    fill_idx = 0

    def place(typ, name, w, pins=None):
        nonlocal x
        placed.append({"name": name, "type": typ, "x": x, "width": w, "pins": pins or {}})
        x += w

    for gap_i in range(n_gaps):
        place(TAP_CELL, f"TAP_{tap_idx}", widths[TAP_CELL])
        tap_idx += 1

        gap_tracks_left = TAP_INTERVAL_TRACKS
        seg = []
        while cell_queue:
            typ, name, pins = cell_queue[0]
            w_tracks = round(widths[typ] / TRACK_UM)
            if w_tracks > gap_tracks_left:
                break
            seg.append(cell_queue.popleft())
            gap_tracks_left -= w_tracks

        if gap_tracks_left == 1 and seg:
            # undo the last placed cell to make the remainder fillable
            # (every real cell is >=3 tracks wide, so this always works)
            typ, name, pins = seg.pop()
            cell_queue.appendleft((typ, name, pins))
            gap_tracks_left += round(widths[typ] / TRACK_UM)

        for typ, name, pins in seg:
            place(typ, name, widths[typ], pins)

        for fill_typ in (fill_combo(gap_tracks_left) or []):
            place(fill_typ, f"FILL_{fill_idx}", widths[fill_typ])
            fill_idx += 1

    place(TAP_CELL, f"TAP_{tap_idx}", widths[TAP_CELL])  # final boundary TAP
    assert not cell_queue, f"{len(cell_queue)} cells left over -- n_gaps too small for this row"

    return placed, x


def main():
    macros = parse_lef()
    net = parse_netlist()
    instances = net["instances"]

    row_h = macros["INV_X1"]["size"][1]
    widths = {name: m["size"][0] for name, m in macros.items()}
    for name, m in macros.items():
        assert m["size"][1] == row_h, f"{name} height {m['size'][1]} != {row_h}"

    row1_cells, row2_cells = split_rows(instances, widths)
    print(f"row1: {len(row1_cells)} cells, row2: {len(row2_cells)} cells "
          f"(natural width {sum(widths[t] for t,_,_ in row1_cells):.1f} / "
          f"{sum(widths[t] for t,_,_ in row2_cells):.1f} um)")

    n_gaps = max(_gaps_needed(row1_cells, widths), _gaps_needed(row2_cells, widths))
    placed1, w1 = pack_row(deque(row1_cells), widths, n_gaps)
    placed2, w2 = pack_row(deque(row2_cells), widths, n_gaps)
    assert abs(w1 - w2) < 1e-6, f"row width mismatch after TAP-aligned packing: {w1} != {w2}"

    def with_pins(placed, row_idx):
        out = []
        for item in placed:
            pins_abs = {}
            for pname, pinfo in item["pins"].items():
                rects = [(layer, item["x"] + x0, y0, item["x"] + x1, y1)
                         for layer, x0, y0, x1, y1 in pinfo["rects"]]
                pins_abs[pname] = {"net": pinfo["net"], "direction": pinfo["direction"],
                                    "use": pinfo["use"], "rects": rects}
            out.append({"name": item["name"], "type": item["type"], "row": row_idx,
                        "x": item["x"], "width": item["width"], "height": row_h,
                        "pins": pins_abs})
        return out

    # pins in `placed` are currently {pname: LEF pin dict}; with_pins() needs the
    # LEF pin dict directly (rects relative to cell origin), so re-derive from
    # netlist pin-map + LEF table rather than reusing pack_row's stored dict.
    def attach_pins(placed_list, cell_list):
        by_name = {name: pins for _typ, name, pins in cell_list}
        for item in placed_list:
            if item["type"] in (TAP_CELL, "FILL2", "FILL3"):
                item["pins"] = {}
                continue
            pinmap = by_name[item["name"]]
            lef_pins = macros[item["type"]]["pins"]
            resolved = {}
            for pname, pinfo in lef_pins.items():
                net_name = pinmap.get(pname)
                if net_name is None:
                    continue
                resolved[pname] = {"net": net_name, "direction": pinfo["direction"],
                                    "use": pinfo["use"], "rects": pinfo["rects"]}
            item["pins"] = resolved
        return placed_list

    placed1 = attach_pins(placed1, row1_cells)
    placed2 = attach_pins(placed2, row2_cells)

    result = {
        "row_height": row_h,
        "row_width": w1,
        "row1": with_pins(placed1, 1),
        "row2": with_pins(placed2, 2),
    }
    with open(OUT_JSON, "w") as f:
        json.dump(result, f, indent=1)

    n_tap1 = sum(1 for p in placed1 if p["type"] == TAP_CELL)
    n_tap2 = sum(1 for p in placed2 if p["type"] == TAP_CELL)
    n_fill1 = sum(1 for p in placed1 if p["type"] in ("FILL2", "FILL3"))
    n_fill2 = sum(1 for p in placed2 if p["type"] in ("FILL2", "FILL3"))
    print(f"wrote {OUT_JSON}")
    print(f"row width (both rows, by construction) = {w1:.1f} um")
    print(f"row1: {len(placed1)} placed ({len(row1_cells)} cells + {n_tap1} TAP2 + {n_fill1} FILL)")
    print(f"row2: {len(placed2)} placed ({len(row2_cells)} cells + {n_tap2} TAP2 + {n_fill2} FILL)")


if __name__ == "__main__":
    main()
