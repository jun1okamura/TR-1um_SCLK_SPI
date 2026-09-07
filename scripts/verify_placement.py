#!/usr/bin/env python3
"""verify_placement.py -- check a placement JSON/GDS before routing.

Checks, in order:
  1  every netlist instance is placed exactly once, and nothing extra is
  2  cells inside a row do not overlap and leave no unfillable gap
  3  every row starts at x=0 and ends at exactly the row width
  4  every cell x is on the 5.4 um track grid
  5  TAP cells sit at the expected fixed positions in every row
  6  rows do not overlap in y and sit inside the core box
  7  the GDS really contains that many references, with the same extent
  8  reports HPWL and row occupancy

  usage:  scripts/verify_placement.py [layout/step4/place_step4_fill.json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlist_util as nu
import explore_rows as ex

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PL = os.path.join(ROOT, "layout", "step4", "place_step4_fill.json")
NET = os.path.join(ROOT, "layout", "spi_slave_sclk_net_pnr.v")
INFO = os.path.join(ROOT, "lef", "cell_info.json")
PITCH = 5.4
EPS = 1e-6


def main(pl_path=PL, net_path=NET, info_path=INFO, gds_path=None, row_w=1620.0):
    d = json.load(open(pl_path))
    rows = d["rows"]
    fails = []
    def chk(ok, msg):
        print(("  OK   " if ok else "  FAIL ") + msg)
        if not ok:
            fails.append(msg)

    print(f"=== {os.path.relpath(pl_path, ROOT)} ===")

    # 1 -- instance coverage
    insts, width, cellof, net_cells, ports = ex.load(net_path, info_path)
    want = {i.name for i in insts}
    got = [e["inst"] for row in rows for e in row if e["inst"]]
    chk(len(got) == len(set(got)), f"no duplicate instances ({len(got)} placed)")
    chk(set(got) == want,
        f"all {len(want)} netlist instances placed"
        + ("" if set(got) == want else f"  missing={sorted(want - set(got))[:5]}"
                                       f" extra={sorted(set(got) - want)[:5]}"))

    # 2/3/4 -- row geometry
    for r, row in enumerate(rows):
        seq = sorted(row, key=lambda e: e["x"])
        ok_ovl = ok_grid = True
        x = 0.0
        for e in seq:
            if abs(e["x"] - x) > EPS:
                ok_ovl = False
            if abs(round(e["x"] / PITCH) * PITCH - e["x"]) > 1e-3:
                ok_grid = False
            x = round(e["x"] + e["w"], 3)
        chk(ok_ovl, f"row {r}: cells abut with no overlap or gap")
        chk(ok_grid, f"row {r}: every x on the {PITCH} um track grid")
        if d["tag"] == "fill":
            chk(abs(x - row_w) < EPS,
                f"row {r}: ends at exactly {row_w} um (got {x})")

    # 5 -- taps
    if "tap_x" in d:
        for r, row in enumerate(rows):
            taps = sorted(e["x"] for e in row if e["cell"].startswith("TAP"))
            chk(taps == [round(t, 3) for t in d["tap_x"]],
                f"row {r}: TAPs at {d['tap_x']}")

    # 6 -- rows in y
    ys = d["row_y"]
    ok = all(ys[i] + d["row_h"] <= ys[i + 1] + EPS for i in range(len(ys) - 1))
    chk(ok, "rows do not overlap in y")
    chk(ys[0] >= -EPS and ys[-1] + d["row_h"] <= d["core_h"] + EPS,
        f"rows inside the core box (0..{d['core_h']} um)")

    # 7 -- GDS cross-check
    gds_path = gds_path or pl_path.replace(".json", ".gds")
    if os.path.exists(gds_path):
        import gdstk
        lib = gdstk.read_gds(gds_path)
        top = [c for c in lib.cells if c.name == d.get("core", "spi_slave_sclk_nrow_fm")]
        top = top[0] if top else lib.top_level()[0]
        nref = len(top.references)
        nplaced = sum(len(row) for row in rows)
        chk(nref == nplaced, f"GDS holds {nref} references (placement has {nplaced})")
        (x0, y0), (x1, y1) = top.bounding_box()
        chk(abs((x1 - x0) - d["core_w"]) < 13.0 and abs((y1 - y0) - d["core_h"]) < 5.0,
            f"GDS extent {x1-x0:.1f} x {y1-y0:.1f} um vs core "
            f"{d['core_w']:.1f} x {d['core_h']:.1f} um (cells overhang "
            f"prBoundary by 12.6/4.0 um by construction)")
    else:
        print(f"  --   GDS {gds_path} not found, skipped")

    # 8 -- report
    print()
    for r, row in enumerate(rows):
        logic = sum(e["w"] for e in row if e["inst"])
        tap = sum(e["w"] for e in row if e["cell"].startswith("TAP"))
        fill = sum(e["w"] for e in row if e["cell"].startswith("FILL"))
        n = sum(1 for e in row if e["inst"])
        print(f"  row {r}: {n:3d} logic cells  {logic:7.1f} um "
              f"({logic/row_w*100:4.1f}%)   TAP {tap:5.1f}   FILL {fill:6.1f}")
    if "hpwl_um" in d:
        print(f"  HPWL  : {d['hpwl_um']:.0f} um")
    if "channel_tracks" in d:
        print(f"  channels: tracks {d['channel_tracks']}  heights "
              + ", ".join(f"{h:.1f}" for h in d["channel_heights"]) + " um")
    print(f"  core  : {d['core_w']:.1f} x {d['core_h']:.1f} um "
          f"= {d['core_w']*d['core_h']/1e6:.4f} mm2")

    print()
    if fails:
        print(f"*** {len(fails)} CHECK(S) FAILED ***")
        return 1
    print("ALL PLACEMENT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("placement", nargs="?", default=PL)
    ap.add_argument("--netlist", default=NET)
    ap.add_argument("--cell-info", default=INFO)
    ap.add_argument("--row-width", type=float, default=1620.0)
    a = ap.parse_args()
    sys.exit(main(a.placement, a.netlist, a.cell_info, row_w=a.row_width))
