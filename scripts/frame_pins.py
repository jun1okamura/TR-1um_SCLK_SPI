#!/usr/bin/env python3
"""frame_pins.py -- read the GIO pad-ring's pin geometry out of
lef/TR-1um_frame_25x25.gds.

The I2C project kept these coordinates in a hand-maintained JSON table copied
from LEF/OSS_FRAME_GIO.lef.  Reading them from the frame GDS this project
actually assembles removes that copy: if the frame is ever replaced, every
consumer sees the new geometry.

Each of the 14 signal pads carries three M2 terminals -- P<n> (the pad itself),
HIZ<n> (0 = output driver enabled, 1 = Hi-Z / input only) and OUT<n> (the value
driven when HIZ is low).  P<n> is labelled twice: once on the inner terminal the
core routes to, once out on the bond pad at radius 1040.  Only the inner one is
returned.

  usage:  scripts/frame_pins.py          # print the table
"""
import os
import re
import sys

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

FRAME_GDS = os.path.join(_cfg.ROOT, "lef", "TR-1um_frame_25x25.gds")
FRAME_CELL = "OSS_FRAME_GIO"

PIN_TEXT_LAYER = (49, 0)
PIN_SHAPE_LAYER = (49, 1)

# radius at which the ring's own routable terminals sit; the bond-pad copies of
# the P<n> labels sit further out and are ignored
INNER_RADIUS_MAX = 950.0

_PIN_RE = re.compile(r"^(P|HIZ|OUT)(\d+)$")


def _edge(x, y):
    return ("RIGHT" if x > 0 else "LEFT") if abs(x) > abs(y) else ("TOP" if y > 0 else "BOTTOM")


def load(frame_gds=FRAME_GDS, cell=FRAME_CELL):
    """{pin: {"x","y","edge","layer","kind","pad","box"}} in chip coordinates."""
    ly = db.Layout()
    ly.read(frame_gds)
    u = ly.dbu
    top = ly.cell(cell)
    if top is None:
        raise SystemExit(f"{cell} not found in {frame_gds}")

    shapes = db.Region(top.begin_shapes_rec(ly.layer(*PIN_SHAPE_LAYER)))
    shapes.merge()

    out = {}
    it = top.begin_shapes_rec(ly.layer(*PIN_TEXT_LAYER))
    while not it.at_end():
        s = it.shape()
        if s.is_text():
            m = _PIN_RE.match(s.text.string)
            if m:
                t = s.text.transformed(it.trans())
                x, y = t.x * u, t.y * u
                if max(abs(x), abs(y)) <= INNER_RADIUS_MAX:
                    hit = shapes.interacting(db.Region(db.Box(t.x - 1, t.y - 1, t.x + 1, t.y + 1)))
                    if hit.is_empty():
                        raise SystemExit(f"label {s.text.string} at ({x},{y}) sits on no "
                                         f"{PIN_SHAPE_LAYER} shape")
                    b = hit.bbox()
                    cx = (b.left + b.right) / 2 * u
                    cy = (b.bottom + b.top) / 2 * u
                    out[s.text.string] = {
                        "x": round(cx, 2), "y": round(cy, 2),
                        "edge": _edge(cx, cy), "layer": "M2",
                        "kind": m.group(1), "pad": int(m.group(2)),
                        "box": [round(b.left * u, 2), round(b.bottom * u, 2),
                                round(b.right * u, 2), round(b.top * u, 2)],
                    }
        it.next()
    return out


def main():
    pins = load()
    order = {"P": 0, "HIZ": 1, "OUT": 2}
    for name in sorted(pins, key=lambda n: (pins[n]["pad"], order[pins[n]["kind"]])):
        p = pins[name]
        print(f"{name:6s} {p['x']:9.2f} {p['y']:9.2f}  {p['edge']:6s} {p['layer']}  "
              f"box {p['box']}")
    print(f"{len(pins)} terminal(s)")


if __name__ == "__main__":
    main()
