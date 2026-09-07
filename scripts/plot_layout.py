#!/usr/bin/env python3
"""plot_layout.py -- render a routed GDS to PNG for eyeballing.

Not part of the tapeout flow (KLayout is the real viewer); this just makes
the routing result reviewable inline.  Draws the fabrication layers only:
M1 (13/0), V1 (19/0), M2 (20/0), plus the cell prBoundary for context.

  usage:  scripts/plot_layout.py layout/step10/route_step_6_squeezed.gds
          scripts/plot_layout.py -o out.png --stack GDS1 GDS2 ...
"""
import argparse
import os
import sys

import gdstk
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spi_config as cfg  # noqa: E402

STYLE = {                      # layer: (facecolor, alpha, zorder)
    (235, 0): ("#d8d8d8", 0.55, 1),    # cell prBoundary
    (13, 0):  ("#2e6fb7", 0.75, 3),    # M1
    (20, 0):  ("#d95f02", 0.60, 4),    # M2
    (19, 0):  ("#111111", 0.95, 5),    # V1
    (48, 1):  ("#1b9e77", 0.9, 6),     # M1PIN
    (49, 1):  ("#7570b3", 0.9, 6),     # M2PIN
}
NAMES = {(235, 0): "cell", (13, 0): "M1", (20, 0): "M2", (19, 0): "V1",
         (48, 1): "M1PIN", (49, 1): "M2PIN"}


def draw(ax, path, title):
    lib = gdstk.read_gds(path)
    tops = [c for c in lib.cells if c.name == cfg.TOP_CELL_NAME] or lib.top_level()
    top = tops[0]
    polys = top.get_polygons(depth=None)
    n = 0
    for p in polys:
        st = STYLE.get((p.layer, p.datatype))
        if st is None:
            continue
        fc, a, z = st
        ax.add_patch(Polygon(p.points, closed=True, facecolor=fc, alpha=a,
                             edgecolor="none", zorder=z))
        n += 1
    (x0, y0), (x1, y1) = top.bounding_box()
    ax.set_xlim(x0 - 20, x1 + 20)
    ax.set_ylim(y0 - 10, y1 + 10)
    ax.set_aspect("equal")
    ax.set_title(f"{title}   {x1-x0:.1f} x {y1-y0:.1f} um   ({n} shapes)",
                 fontsize=10, loc="left")
    ax.tick_params(labelsize=7)


def main(paths, out):
    fig, axes = plt.subplots(len(paths), 1, figsize=(14, 3.1 * len(paths)),
                             squeeze=False)
    for ax, p in zip(axes[:, 0], paths):
        draw(ax, p, os.path.relpath(p, cfg.ROOT))
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=STYLE[k][0],
                             alpha=STYLE[k][1]) for k in NAMES]
    fig.legend(handles, list(NAMES.values()), loc="lower center",
               ncol=len(NAMES), fontsize=9, frameon=False)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out, dpi=150)
    print(f"wrote {os.path.relpath(out, cfg.ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gds", nargs="*", default=[cfg.SQUEEZED_GDS])
    ap.add_argument("-o", "--output",
                    default=os.path.join(cfg.LAYOUT, "routing_steps.png"))
    a = ap.parse_args()
    main(a.gds or [cfg.SQUEEZED_GDS], a.output)
