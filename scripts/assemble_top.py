#!/usr/bin/env python3
"""assemble_top.py -- drop the routed core into the GIO pad ring.

    layout/step10/route_step_6_squeezed.gds   (core, DRC + LVS clean)
  + lef/TR-1um_frame_25x25.gds                (OSS_FRAME_GIO, 16 pads)
  -> layout/chip/step1_assembled.gds          (cell tr_1um_3wire_SPI)

Placement only -- no routing.  That is the I2C flow's own convention
(scripts/i2c_ref/... assemble_top_v10.py stops here too): the offsets are worth
looking at in KLayout before any wire is drawn, because everything downstream
is measured from them.

WHERE THE CORE SITS
-------------------
X is not a choice.  The core's native bbox spans -6.3..1626.3 um, so the offset
-810.0 lands it at +-816.3 -- symmetric about the chip axis.  That is why this
project fixed its row width at 1620 um to match the I2C chip.

Y follows from the channel width.  The ring's innermost geometry is a wall at
+-920.0 (re-measured every run, see check below) with the routable P/HIZ/OUT
terminals just outside it at +-921.7.  Asking for an 80 um top channel puts the
core's top edge at 840.0, hence the offset 515.1.

The core is only 324.9 um tall against an 1840 um opening, so almost all of the
space below it is unused.  A PTECT box (layer 63/1) claims it, stopping 80 um
short of the wall so the bottom channel matches the top one -- the same rule
the I2C chip used, where "about 120 um of channel" meant a PTECT edge 121.7 um
inside the terminal ring.

  usage:  scripts/assemble_top.py [-o OUT]
"""
import argparse
import os
import sys

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

OUT_GDS = os.path.join(_cfg.CHIP, "step1_assembled.gds")


def measured_inner_wall(layout, cell, x0, x1):
    """Innermost |y| of real frame geometry across the core's own x-span."""
    r = db.Region()
    for L in ((13, 0), (20, 0), (11, 0), (19, 0), (8, 1), (3, 1), (3, 2), (48, 1), (49, 1)):
        li = layout.layer(*L)
        r += db.Region(cell.begin_shapes_rec(li))
    r.merge()
    u = layout.dbu
    strip = db.Region(db.Box(int(x0 / u), int(-1250 / u), int(x1 / u), int(1250 / u)))
    ys = [p.bbox() for p in (r & strip).each()]
    above = min((b.bottom * u for b in ys if b.bottom * u > 0), default=None)
    below = max((b.top * u for b in ys if b.top * u < 0), default=None)
    return above, below


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=OUT_GDS)
    ap.add_argument("--core-gds", default=_cfg.SQUEEZED_GDS)
    args = ap.parse_args()

    geom = _cfg.chip_geometry(args.core_gds)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    layout = db.Layout()
    layout.dbu = 0.001
    um = lambda v: int(round(v / layout.dbu))
    top = layout.create_cell(_cfg.CHIP_TOP_CELL)

    # ---- core ----
    layout.read(args.core_gds)
    core = layout.cell(_cfg.TOP_CELL_NAME)
    if core is None:
        raise SystemExit(f"{_cfg.TOP_CELL_NAME} not found in {args.core_gds}")
    ox, oy = geom["core_offset"]
    top.insert(db.CellInstArray(core.cell_index(), db.Trans(db.Vector(um(ox), um(oy)))))

    # ---- GIO ring ----
    layout.read(_cfg.FRAME_GDS)
    gio = layout.cell(_cfg.FRAME_CELL)
    if gio is None:
        raise SystemExit(f"{_cfg.FRAME_CELL} not found in {_cfg.FRAME_GDS}")
    top.insert(db.CellInstArray(gio.cell_index(), db.Trans(db.Vector(0, 0))))

    # ---- PTECT ----
    px0, py0, px1, py1 = geom["ptect_box"]
    top.shapes(layout.layer(*_cfg.PTECT_LAYER)).insert(
        db.Box(um(px0), um(py0), um(px1), um(py1)))

    # ---- checks ----
    cl, cb, cr, ct = geom["core_chip_bbox"]
    above, below = measured_inner_wall(layout, gio, cl, cr)
    problems = []
    if above is None or below is None:
        problems.append("could not measure the ring's inner wall")
    else:
        if abs(above - _cfg.GIO_INNER_WALL) > 0.01 or abs(below + _cfg.GIO_INNER_WALL) > 0.01:
            problems.append(f"inner wall measures {below} / {above}, "
                            f"spi_config.GIO_INNER_WALL says +-{_cfg.GIO_INNER_WALL}")
        if ct > above or cb < below:
            problems.append(f"core ({cb} .. {ct}) runs into the ring wall ({below} .. {above})")
    if py1 > cb:
        problems.append(f"PTECT top {py1} overlaps the core bottom {cb}")

    layout.write(args.out)

    print(f"core   {geom['core_native_bbox']}  offset {geom['core_offset']}")
    print(f"       -> chip bbox ({cl}, {cb}) .. ({cr}, {ct})")
    print(f"PTECT  ({px0}, {py0}) .. ({px1}, {py1})   "
          f"[{px1 - px0:.1f} x {py1 - py0:.1f}]")
    print(f"ring   wall measured at {below} / {above}, terminals at +-{_cfg.GIO_PIN_RADIUS}")
    print("channels (to wall / to terminals):")
    for side in ("top", "bottom", "left", "right"):
        w, t = geom["channel_" + side]
        print(f"  {side:6s} {w:7.1f} / {t:.1f} um")
    print(f"\nwrote {args.out}")
    if problems:
        for p in problems:
            print("  PROBLEM: " + p)
        raise SystemExit(1)
    print("placement only -- no routing yet.")


if __name__ == "__main__":
    main()
