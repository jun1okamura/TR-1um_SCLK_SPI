#!/usr/bin/env python3
"""place_logo.py -- the OpenSUSI logo, in the space the core does not use.

    layout/chip/step3_top_pins.gds  +  lef/opensusi_logo.txt
 -> layout/chip/step4_final.gds

The core is 314 um tall in an 1840 um opening, so most of the die below it is
empty once PTECT comes out (route_chip.py removes it after the signal routing
that it existed to constrain).  Two copies of the logo go there, stacked.

DRAWN AS ISOLATED DOTS, NOT FILLED BLOCKS
-----------------------------------------
Each ON cell of the 5.0 um grid becomes one 3.0 x 3.0 um M2 square centred in
its cell, so neighbours never merge:

    dot 3.0 um             = M2 minimum width exactly
    orthogonal neighbours  = 5.0 - 3.0 = 2.0 um apart, M2 minimum space exactly
    diagonal neighbours    = sqrt(2) x 2.0 = 2.83 um, comfortably over

Both checks flag STRICTLY less than the minimum, so meeting it exactly is legal
-- the same convention the routing uses.  The I2C project first drew this logo
as filled 5 um blocks and had to patch a diagonal corner touch by hand; dots
have no such case at all (its script/place_opensusi_logo_dots.py, whose output
lef/opensusi_logo.txt was lifted from).

The bitmap is checked on its own before it is placed, and the whole chip is
re-checked afterwards for markers that were not there before -- a logo is not
worth a single new DRC violation.

  usage:  scripts/place_logo.py [--rows 2] [--gap 100] [-o OUT]
"""
import argparse
import os
import sys

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

IN_GDS = os.path.join(_cfg.CHIP, "step3_top_pins.gds")
OUT_GDS = os.path.join(_cfg.CHIP, "step4_final.gds")
BITMAP = os.path.join(_cfg.ROOT, "lef", "opensusi_logo.txt")

M2_LAYER = (20, 0)
M2_WMIN, M2_SMIN = 3.0, 2.0
PITCH = M2_WMIN + M2_SMIN        # 5.0 um
DOT = M2_WMIN                    # 3.0 um
LOGO_CELL = "OPENSUSI_LOGO"

# The clear box: inside the two GND legs at |x| = 838 and between the two power
# buses (the core-tap bus at y = 463 and the bottom bus bar at y = -795).
AREA = (-795.0, -785.0, 795.0, 453.0)


def read_bitmap(path):
    # Comments start with '%', not '#'.  '#' is the ON cell, so a row whose
    # first cell is ON starts with '#' -- reading '#' as a comment silently
    # deletes those rows and shortens the logo (it read 55 rows of 63 once).
    rows = [l.rstrip("\n") for l in open(path)
            if not l.startswith("%") and l.strip()]
    bad = {ch for r in rows for ch in r} - {"#", "."}
    if bad:
        raise SystemExit(f"{path}: unexpected character(s) {sorted(bad)}")
    w = max(len(r) for r in rows)
    return [r.ljust(w, ".") for r in rows], w, len(rows)


def build_logo_cell(layout, rows, w, h):
    cell = layout.create_cell(LOGO_CELL)
    li = layout.layer(*M2_LAYER)
    dbu = layout.dbu
    m = (PITCH - DOT) / 2.0
    n = 0
    for r, line in enumerate(rows):
        y = (h - 1 - r) * PITCH + m
        for c, ch in enumerate(line):
            if ch != ".":
                x = c * PITCH + m
                cell.shapes(li).insert(
                    db.DBox(x, y, x + DOT, y + DOT).to_itype(dbu))
                n += 1
    return cell, n


def drc(region, dbu, label):
    bad = []
    for kind, minv in (("width", M2_WMIN), ("space", M2_SMIN)):
        res = (region.width_check(int(round(minv / dbu))) if kind == "width"
               else region.space_check(int(round(minv / dbu))))
        for e in res.each():
            b = e.bbox()
            bad.append((kind, round((b.left + b.right) / 2 * dbu, 2),
                        round((b.bottom + b.top) / 2 * dbu, 2)))
    print(f"  {'ok  ' if not bad else 'FAIL'} {label}: "
          f"{len(bad)} M2 width/space violation(s)" +
          (f", e.g. {bad[:3]}" if bad else ""))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--in-gds", default=IN_GDS)
    ap.add_argument("-o", "--out", default=OUT_GDS)
    ap.add_argument("-b", "--bitmap", default=BITMAP)
    ap.add_argument("--rows", type=int, default=2, help="how many copies, stacked")
    ap.add_argument("--gap", type=float, default=100.0, help="um between copies")
    args = ap.parse_args()

    rows, w, h = read_bitmap(args.bitmap)
    lw, lh = (w - 1) * PITCH + DOT, (h - 1) * PITCH + DOT
    print(f"bitmap {w} x {h} cell(s) -> {lw:.1f} x {lh:.1f} um per copy")

    ly = db.Layout()
    ly.read(args.in_gds)
    top = ly.cell(_cfg.CHIP_TOP_CELL)
    if top is None:
        raise SystemExit(f"{_cfg.CHIP_TOP_CELL} not found in {args.in_gds}")
    if ly.cell(LOGO_CELL) is not None:
        raise SystemExit(f"{LOGO_CELL} already exists in {args.in_gds}")

    before = db.Region(top.begin_shapes_rec(ly.layer(*M2_LAYER))).merged()
    cell, ndots = build_logo_cell(ly, rows, w, h)
    print(f"{LOGO_CELL}: {ndots} dot(s) of {DOT} x {DOT} um on a {PITCH} um grid")
    print("\nthe logo on its own")
    bad = drc(db.Region(cell.begin_shapes_rec(ly.layer(*M2_LAYER))), ly.dbu,
              LOGO_CELL)

    ax0, ay0, ax1, ay1 = AREA
    stack = args.rows * lh + (args.rows - 1) * args.gap
    if lw > ax1 - ax0 or stack > ay1 - ay0:
        raise SystemExit(f"{args.rows} copy(ies) need {lw:.1f} x {stack:.1f} um "
                         f"but the clear area is only "
                         f"{ax1 - ax0:.1f} x {ay1 - ay0:.1f}")
    cx, cy = (ax0 + ax1) / 2.0, (ay0 + ay1) / 2.0
    x = cx - lw / 2.0
    placed = []
    for i in range(args.rows):
        y = cy + stack / 2.0 - lh - i * (lh + args.gap)
        top.insert(db.CellInstArray(cell.cell_index(),
                                    db.Trans(db.Vector(int(round(x / ly.dbu)),
                                                       int(round(y / ly.dbu))))))
        placed.append((x, y, x + lw, y + lh))

    print(f"\nclear area {AREA}")
    for i, b in enumerate(placed):
        print(f"  copy {i}: ({b[0]:8.1f}, {b[1]:8.1f}) .. ({b[2]:8.1f}, {b[3]:8.1f})")
    margins = (placed[0][3], ay1 - placed[0][3], placed[-1][1] - ay0)
    print(f"  top copy is {margins[1]:.1f} um below the area's top edge, "
          f"bottom copy {margins[2]:.1f} um above its bottom edge")

    # nothing of ours may be sitting where the logo just went
    for name, lay in (("M1", (13, 0)), ("V1", (19, 0))):
        r = db.Region(top.begin_shapes_rec(ly.layer(*lay))).merged()
        for b in placed:
            hit = r & db.Region(db.DBox(*b).to_itype(ly.dbu))
            if not hit.is_empty():
                print(f"  FAIL {name} inside a logo footprint: {hit.count()} shape(s)")
                bad.append((name, b))
    after = db.Region(top.begin_shapes_rec(ly.layer(*M2_LAYER))).merged()
    print("\nthe whole chip, M2")
    b_before = drc(before, ly.dbu, "before")
    b_after = drc(after, ly.dbu, "after")
    new = [v for v in b_after if v not in b_before]
    print(f"  {'ok  ' if not new else 'FAIL'} {len(new)} NEW violation(s)" +
          (f": {new[:3]}" if new else ""))
    bad += new

    if bad:
        raise SystemExit(f"{len(bad)} problem(s) -- nothing written")
    ly.write(args.out)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
