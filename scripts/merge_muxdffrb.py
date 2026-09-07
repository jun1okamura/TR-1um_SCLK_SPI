#!/usr/bin/env python3
"""merge_muxdffrb.py -- collapse MUX2 + DFFRB pairs into MUXDFFRB cells.

Yosys/ABC cannot infer a mux-D flip-flop, so a register with a load or an
enable comes out of synthesis as a MUX2 feeding a DFFRB.  The library has a
single merged cell for exactly that pattern; substituting it is what
TR-1um_Async_I2C did by hand for its V10 revision.

The merge is transistor-neutral (DFFRB 26 + MUX2 12 = MUXDFFRB 38) and saves
area (5325 + 3096 = 8421 um2 -> 7554 um2, 10.3% per pair) plus one net and
one placement site per pair.

A pair is merged only when the MUX2 output drives that DFFRB's D pin and
nothing else, so the transform is always safe.

  usage:  scripts/merge_muxdffrb.py IN.v OUT.v [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlist_util as nu

# output pins per cell type -- extend here if the library grows
OUT_PINS = {
    "MUX2": {"Y"}, "DFFRB": {"Q", "QB"}, "MUXDFFRB": {"Q", "QB"},
    "DFFS": {"Q", "QB"}, "RSLATCH": {"Q", "QB"},
}
DEFAULT_OUT = {"Y"}

MUXDFFRB_PINS = ["A", "B", "S", "CK", "RSTB", "Q", "QB"]


def merge(src, mux_cell="MUX2", ff_cell="DFFRB", merged_cell="MUXDFFRB",
          d_pin="D", verbose=True):
    insts = nu.parse(src)
    out_pins = {i.cell: OUT_PINS.get(i.cell, DEFAULT_OUT) for i in insts}
    drv, load = nu.drivers_and_loads(insts, out_pins)

    edits, n = [], 0
    for ff in insts:
        if ff.cell != ff_cell or d_pin not in ff.conns:
            continue
        dnet = ff.conns[d_pin]
        mux = drv.get(dnet)
        if mux is None or mux.cell != mux_cell:
            continue
        if len(load.get(dnet, [])) != 1:          # mux output must be private
            continue
        conns = {
            "A": mux.conns["A"], "B": mux.conns["B"], "S": mux.conns["S"],
            "CK": ff.conns.get("CK"), "RSTB": ff.conns.get("RSTB"),
        }
        for p in ("Q", "QB"):
            if p in ff.conns:
                conns[p] = ff.conns[p]
        conns = {k: v for k, v in conns.items() if v is not None}
        edits.append((ff.span[0], ff.span[1],
                      nu.render(merged_cell, ff.name, conns, MUXDFFRB_PINS)))
        edits.append((mux.span[0], mux.span[1], None))   # drop the MUX2
        n += 1
        if verbose:
            print(f"  merge {mux.cell} {mux.name} + {ff.cell} {ff.name}"
                  f" -> {merged_cell} {ff.name}")
    return nu.replace_spans(src, edits), n


def main(in_path, out_path, dry_run=False, **kw):
    src = open(in_path).read()
    dst, n = merge(src, **kw)
    print(f"{in_path}: merged {n} pair(s)")
    if not dry_run:
        open(out_path, "w").write(dst)
        print(f"wrote {out_path}")
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--mux-cell", default="MUX2")
    ap.add_argument("--ff-cell", default="DFFRB")
    ap.add_argument("--merged-cell", default="MUXDFFRB")
    ap.add_argument("--d-pin", default="D")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    main(a.input, a.output, dry_run=a.dry_run, mux_cell=a.mux_cell,
         ff_cell=a.ff_cell, merged_cell=a.merged_cell, d_pin=a.d_pin)
