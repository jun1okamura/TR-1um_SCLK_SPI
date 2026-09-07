#!/usr/bin/env python3
"""plot_chip_floorplan.py -- draw the chip-level floorplan for eyeballing.

Not part of the flow.  A GDS viewer shows every polygon in the pad ring, which
is exactly what you do NOT want when checking a placement; this draws only the
things the placement decision is about: the die and the ring's inner wall, the
42 P/HIZ/OUT terminals with their names, the core with its top-level pins, the
PTECT box, and the routing corridors between them.

  usage:  scripts/plot_chip_floorplan.py [-o layout/chip/floorplan.png]
"""
import argparse
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg
import frame_pins

OUT_PNG = os.path.join(_cfg.CHIP, "floorplan.png")
PLAN = os.path.join(_cfg.CHIP, "signal_routing_plan.json")

KIND_STYLE = {"P": ("#1b6ca8", 9), "HIZ": ("#b8860b", 7), "OUT": ("#7a3ea1", 7)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=OUT_PNG)
    args = ap.parse_args()

    geom = _cfg.chip_geometry()
    cl, cb, cr, ct = geom["core_chip_bbox"]
    px0, py0, px1, py1 = geom["ptect_box"]
    w = _cfg.GIO_INNER_WALL
    pads = frame_pins.load()

    fig, ax = plt.subplots(figsize=(11, 11))

    ax.add_patch(Rectangle((-1250, -1250), 2500, 2500, fill=False, ec="#444", lw=1.2))
    ax.add_patch(Rectangle((-w, -w), 2 * w, 2 * w, fill=False, ec="#888", lw=1.0, ls="--"))

    # routing corridors
    for x0, y0, x1, y1, label in (
        (-w, ct, w, w, "T"),
        (-w, -w, w, py0, "B"),
        (-w, -w, cl, w, "L"),
        (cr, -w, w, w, "R"),
        (cl, py1, cr, cb, "U"),
    ):
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0,
                               fc="#cfe8cf", ec="none", alpha=0.55, zorder=0))
        ax.text((x0 + x1) / 2, (y0 + y1) / 2, label, ha="center", va="center",
                color="#2d7a2d", fontsize=13, fontweight="bold", zorder=1)

    ax.add_patch(Rectangle((px0, py0), px1 - px0, py1 - py0,
                           fc="#f2e2c4", ec="#b08040", lw=1.0, zorder=2))
    ax.text((px0 + px1) / 2, (py0 + py1) / 2,
            f"PTECT {_cfg.PTECT_LAYER}\n{px1-px0:.0f} x {py1-py0:.1f} um",
            ha="center", va="center", color="#8a5a20", fontsize=10, zorder=3)

    ax.add_patch(Rectangle((cl, cb), cr - cl, ct - cb,
                           fc="#c8d8ee", ec="#1b4f8a", lw=1.4, zorder=4))
    ax.text((cl + cr) / 2, (cb + ct) / 2,
            f"{_cfg.TOP_CELL_NAME}\n{cr-cl:.1f} x {ct-cb:.1f} um\noffset {geom['core_offset']}",
            ha="center", va="center", fontsize=10, color="#123", zorder=6)

    for name, p in pads.items():
        col, size = KIND_STYLE[p["kind"]]
        ax.plot(p["x"], p["y"], marker="s", ms=4, color=col, zorder=7)
        dx = {"LEFT": -1, "RIGHT": 1}.get(p["edge"], 0)
        dy = {"TOP": 1, "BOTTOM": -1}.get(p["edge"], 0)
        ax.annotate(name, (p["x"], p["y"]), xytext=(p["x"] + dx * 30, p["y"] + dy * 30),
                    ha={"LEFT": "right", "RIGHT": "left"}.get(p["edge"], "center"),
                    va={"TOP": "bottom", "BOTTOM": "top"}.get(p["edge"], "center"),
                    fontsize=size, color=col, rotation=0 if dy else 0, zorder=7)

    if os.path.exists(PLAN):
        plan = json.load(open(PLAN))
        for name, net in plan["nets"].items():
            c = net["core"]
            if not c:
                continue
            ax.plot(c["x"], c["y"], marker="o", ms=3, color="#c0392b", zorder=8)
            ax.annotate(name, (c["x"], c["y"]),
                        xytext=(c["x"], c["y"] + (14 if c["edge"] == "TOP" else -14)),
                        ha="center", va="bottom" if c["edge"] == "TOP" else "top",
                        fontsize=5.5, color="#c0392b", rotation=90, zorder=8)

    ch = geom
    ax.set_title(f"{_cfg.CHIP_TOP_CELL} floorplan -- channels "
                 f"T/B {ch['channel_top'][0]:.0f} um, L/R {ch['channel_left'][0]:.1f} um "
                 f"(to the ring wall at +-{w:.0f})", fontsize=11)
    ax.set_xlim(-1300, 1300)
    ax.set_ylim(-1300, 1300)
    ax.set_aspect("equal")
    ax.set_xlabel("um")
    fig.tight_layout()
    fig.savefig(args.out, dpi=130)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
