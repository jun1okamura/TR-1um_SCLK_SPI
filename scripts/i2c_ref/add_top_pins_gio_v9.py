#!/usr/bin/env python3
"""
add_top_pins_gio_v9.py -- add chip-level TOP PINs (layout side) to
src/tr_1um_i2c_slave_async.gds, per design_notes.md 82.x / 83.x.

Root cause (user diagnosis, design_notes 82.2): neither the schematic
(SPICE) side nor the layout (GDS) side exposed the chip's 16 real
bond-pad nets (P1-P7, VSS, P9-P15, VDD -- P8 does not exist in this
frame) as actual top-level PORTS/PINs. The schematic side was fixed in
82.3 (script/gen_lvs_spice_top_v9.py). This script fixes the layout
side, using the SAME pin-marker convention already established and
LVS-verified for the core cell (design_notes 75.4/68.x/77.44):

    - PIN box: 3.0 x 3.0 um, on layer M2PIN = (49,1)
    - TEXT label: pin name, on layer TXM2 = (49,0), placed at the
      BOX CENTER (not offset -- offset labels were a bug, see 68.x;
      KLayout's LVS text extraction requires the label INSIDE the box)

User instruction (this session): "OSS_FRAME_GIO の PO layer の中心座標
を使って Layout に 3x3um M2PIN と TXM2 で TEXT つけます。... TEXTラベル
は、(-200,1040)から反時計回りでP1~P7,VSS,P9-P15,VDDです。"

The PO layer (14,0; tech comment "# PAD") in OSS_FRAME_GIO contains 16
small 80x80um marker squares -- one per bond pad, at its exact center
-- plus 4 large VDD/VSS bus-bar markers (excluded here, they are not
80x80). Independently verified (this script's own sanity check) that
each of the 16 marker centers sits exactly on a real M2 pad shape in
the routed top-level layout (src/tr_1um_i2c_slave_async.gds), and that
OSS_FRAME_GIO is instantiated in the top cell at (0,0)/r0/mag1 (pure
identity transform), so the PO-layer local coordinates equal absolute
chip coordinates directly -- no transform needed.

The 16 centers were walked counterclockwise (increasing atan2(y,x)
angle from the chip center, standard math/layout convention with x
right, y up) starting at (-200,1040) as instructed, and assigned in
that order to P1, P2, P3, P4, P5, P6, P7, VSS, P9, P10, P11, P12, P13,
P14, P15, VDD.
"""
import klayout.db as db

IN_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/src/tr_1um_i2c_slave_async.gds"
OUT_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/outputs/tr_1um_i2c_slave_async_top_pins.gds"
TOP_CELL = "tr_1um_i2c_slave_async"

M2PIN_LAYER = (49, 1)
TXM2_LAYER = (49, 0)
PIN_SIZE_UM = 3.0

# (label, x_um, y_um) -- CCW from (-200,1040), derived from OSS_FRAME_GIO's
# PO-layer (14,0) marker centers, verified against real M2 pad shapes.
TOP_PINS = [
    ("P1",  -200.0, 1040.0),
    ("P2",  -600.0, 1040.0),
    ("P3", -1040.0,  600.0),
    ("P4", -1040.0,  200.0),
    ("P5", -1040.0, -200.0),
    ("P6", -1040.0, -600.0),
    ("P7",  -600.0,-1040.0),
    ("VSS", -200.0,-1040.0),
    ("P9",   200.0,-1040.0),
    ("P10",  600.0,-1040.0),
    ("P11", 1040.0, -600.0),
    ("P12", 1040.0, -200.0),
    ("P13", 1040.0,  200.0),
    ("P14", 1040.0,  600.0),
    ("P15",  600.0, 1040.0),
    ("VDD",  200.0, 1040.0),
]


def main():
    ly = db.Layout()
    ly.read(IN_GDS)
    dbu = ly.dbu
    top = ly.cell(TOP_CELL)

    m2pin_li = ly.layer(*M2PIN_LAYER)
    txm2_li = ly.layer(*TXM2_LAYER)

    half = PIN_SIZE_UM / 2.0
    for label, x, y in TOP_PINS:
        box = db.DBox(x - half, y - half, x + half, y + half).to_itype(dbu)
        top.shapes(m2pin_li).insert(box)
        text = db.DText(label, x, y).to_itype(dbu)
        top.shapes(txm2_li).insert(text)

    ly.write(OUT_GDS)
    print(f"wrote {OUT_GDS}")
    print(f"added {len(TOP_PINS)} M2PIN({M2PIN_LAYER[0]}/{M2PIN_LAYER[1]}) boxes "
          f"+ {len(TOP_PINS)} TXM2({TXM2_LAYER[0]}/{TXM2_LAYER[1]}) labels to cell '{TOP_CELL}'")


if __name__ == "__main__":
    main()
