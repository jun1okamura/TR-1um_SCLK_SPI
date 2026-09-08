#!/usr/bin/env python3
"""add_top_pins.py -- put LVS pins on the bond pads.

    layout/chip/step2_routed.gds  ->  layout/chip/step3_top_pins.gds

Neither side of an LVS run can name a chip-level port that nothing declares.
The reference netlist will list the sixteen bond-pad nets as the top circuit's
pins; this gives the layout the matching declaration, in the convention this
project already uses for the core cell and the I2C chip used for its own top
level (scripts/i2c_ref/add_top_pins_gio_v9.py):

    a 3.0 x 3.0 um box on M2PIN (49,1), and the pin name as text on TXM2
    (49,0) at the BOX CENTRE -- KLayout's text extraction wants the label
    inside the box, and an offset label was a real bug once

The pads and their names are read from lef/TR-1um_frame_25x25.gds rather than
listed here: each OSS_PAD instance gives a centre, and the P<n>/VDD/VSS label
sitting on it gives the name.  The I2C script instead walked the sixteen
centres counter-clockwise from (-200,1040) and assigned names by position,
which is right only as long as nobody renumbers the frame.

Before writing anything, every centre is checked to sit on real M2 in the top
cell -- a pin marker on empty space would extract as its own isolated net and
send the LVS run chasing a phantom.

  usage:  scripts/add_top_pins.py [-o OUT] [--text-size 20]
"""
import argparse
import os
import sys

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg
import pin_list

IN_GDS = os.path.join(_cfg.CHIP, "step2_routed.gds")
OUT_GDS = os.path.join(_cfg.CHIP, "step3_top_pins.gds")

M2PIN_LAYER = (49, 1)
TXM2_LAYER = (49, 0)
M2_LAYER = (20, 0)
PIN_SIZE_UM = 3.0
TEXT_SIZE_UM = 20.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--in-gds", default=IN_GDS)
    ap.add_argument("-o", "--out", default=OUT_GDS)
    ap.add_argument("--text-size", type=float, default=TEXT_SIZE_UM)
    args = ap.parse_args()

    pads = pin_list.bond_pads()

    ly = db.Layout()
    ly.read(args.in_gds)
    dbu = ly.dbu
    top = ly.cell(_cfg.CHIP_TOP_CELL)
    if top is None:
        raise SystemExit(f"{_cfg.CHIP_TOP_CELL} not found in {args.in_gds}")

    m2 = db.Region(top.begin_shapes_rec(ly.layer(*M2_LAYER))).merged()
    existing = {s.text.string for s in top.shapes(ly.layer(*TXM2_LAYER)).each()
                if s.is_text()}

    half = PIN_SIZE_UM / 2.0
    m2pin_li = ly.layer(*M2PIN_LAYER)
    txm2_li = ly.layer(*TXM2_LAYER)
    size_dbu = int(round(args.text_size / dbu))

    problems, added = [], []
    for name in sorted(pads):
        x, y, edge = pads[name]
        probe = db.Region(db.DBox(x - half, y - half, x + half, y + half).to_itype(dbu))
        if (m2 & probe).is_empty():
            problems.append(f"{name} at ({x},{y}): no M2 under the pin box")
            continue
        if name in existing:
            problems.append(f"{name}: the top cell already has a label with that name")
            continue
        top.shapes(m2pin_li).insert(
            db.DBox(x - half, y - half, x + half, y + half).to_itype(dbu))
        t = db.DText(name, x, y).to_itype(dbu)
        t.size = size_dbu
        top.shapes(txm2_li).insert(t)
        added.append((name, x, y, edge))

    for name, x, y, edge in added:
        print(f"  {name:5s} ({x:8.1f}, {y:8.1f})  {edge}")
    print(f"{len(added)} pin(s): {PIN_SIZE_UM} x {PIN_SIZE_UM} um on "
          f"M2PIN {M2PIN_LAYER} + text on TXM2 {TXM2_LAYER} at {args.text_size} um, "
          f"in cell {_cfg.CHIP_TOP_CELL}")

    if problems:
        for p in problems:
            print("  PROBLEM: " + p)
        raise SystemExit(f"{len(problems)} problem(s) -- nothing written")

    ly.write(args.out)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
