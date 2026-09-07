#!/usr/bin/env python3
"""plot_placement.py -- render the placement steps as a PNG for eyeballing.

Not part of the tapeout flow; the GDS files under layout/stepN/ are the real
artefacts.  This just makes the row structure, TAP pitch and free space
visible at a glance.

  usage:  scripts/plot_placement.py [-o layout/placement_steps.png]
"""
import argparse
import glob
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "layout", "placement_steps.png")

COL = {"seq": "#3b6ea5", "comb": "#7ba7d4", "buf": "#e08a3c",
       "tap": "#4a4a4a", "fill": "#e8e8e8"}
SEQ = ("DFFRB", "MUXDFFRB", "DFFS", "RSLATCH")


def colour(cell):
    if cell.startswith("TAP"):  return COL["tap"]
    if cell.startswith("FILL"): return COL["fill"]
    if cell in SEQ:             return COL["seq"]
    if cell.startswith("BUF"):  return COL["buf"]
    return COL["comb"]


def main(out=OUT):
    steps = sorted(glob.glob(os.path.join(ROOT, "layout", "step*",
                                          "place_step*_*.json")))
    if not steps:
        raise SystemExit("no placement JSON found -- run scripts/place.py")
    fig, axes = plt.subplots(len(steps), 1,
                             figsize=(13, 2.0 * len(steps)), squeeze=False)
    for ax, path in zip(axes[:, 0], steps):
        d = json.load(open(path))
        for r, row in enumerate(d["rows"]):
            y = d["row_y"][r]
            for e in row:
                ax.add_patch(Rectangle((e["x"], y), e["w"], d["row_h"],
                                       facecolor=colour(e["cell"]),
                                       edgecolor="white", linewidth=0.3))
        ax.add_patch(Rectangle((0, 0), d["core_w"], d["core_h"], fill=False,
                               edgecolor="#c0392b", linewidth=1.0, ls="--"))
        ax.set_xlim(-40, d["core_w"] + 40)
        ax.set_ylim(-10, d["core_h"] + 10)
        ax.set_aspect("equal")
        ax.set_yticks([])
        ax.set_xlabel("x [um]", fontsize=8)
        ax.tick_params(labelsize=8)
        ax.set_title(f"step{d['step']}  {d['tag']}   "
                     f"core {d['core_w']:.0f} x {d['core_h']:.1f} um",
                     fontsize=10, loc="left")
    handles = [Rectangle((0, 0), 1, 1, facecolor=c, edgecolor="white")
               for c in (COL["seq"], COL["comb"], COL["buf"],
                         COL["tap"], COL["fill"])]
    fig.legend(handles, ["FF (DFFRB/MUXDFFRB)", "combinational",
                         "BUF/BUFTH", "TAP2", "FILL"],
               loc="lower center", ncol=5, fontsize=9, frameon=False)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out, dpi=150)
    print(f"wrote {os.path.relpath(out, ROOT)}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default=OUT)
    a = ap.parse_args()
    main(a.output)
