"""
add_power_pins_nrow_fm.py (this session, user request:
"1. TAP のVDD/VSS ピンがついていません" -> clarified via AskUserQuestion as
"BBOXの端にVDD/VSSがPINでありません" -- i.e. at the chip/core BBOX edge,
unlike every signal port, VDD/GND never got a real PIN marker.)

Root cause (confirmed by direct GDS inspection of
layout/step8/v9_step_8_squeezed_top_pins_routed.gds): TAP2's own M2
power-mesh straps (route_channels_nrow_fm.py's draw_tap_power_mesh
step) DO physically reach the BBOX edges (Y=0 and Y=core_h) -- the
per-channel loop there uses y_lo=max(0, ch_y0[c]-margin) and
y_hi=min(core_h, ch_y0[c]+CH_HEIGHTS[c]+margin), so channel 0 (bottom
margin) and the last channel (top margin) both clamp exactly to the
BBOX edge. And the LEF/GDS standard-cell library definition of TAP2/
TAP3 themselves both have fully-specified PIN GND/PIN VDD geometry
(confirmed via direct LEF read and klayout.db layer/text dump this
session). So the metal and the *cell-level* pins are both genuinely
present.

What's missing is a chip-level PIN annotation at the BBOX edge: unlike
route_top_pins_nrow_fm.py's add_m2_pin()/add_m1_pin(), which drops a
real (49,1)/(48,1) M2PIN/M1PIN box + a (49,0)/(48,0) TXM2/TXM1 text
label ("rst_n", "scl", ...) at every signal port's BBOX exit point,
nothing ever did the equivalent for VDD/GND -- confirmed by a direct
query of v9_step_8's TXM2/TXM1 layers: 12 text labels total, all of
them signal port names, zero "VDD"/"GND". (highlight_top_pins_nrow_fm.py's
power markers are on a completely different, diagnostic-only layer,
(260,2)/(260,3) -- never the real (49,1)/(48,1)/(48,0)/(49,0) pin
layers a downstream LVS/extraction flow would actually look at.)

This script is a small, final STEP9: for every TAP column X (4 of
them: local x = 0.0, 534.6, 1069.2, 1609.2 -- confirmed from
placement_nrow_fm_v9.json, identical across all rows by construction)
and for both nets (GND at local offset (1.0,4.4), VDD at (6.4,9.8) --
route_channels_nrow_fm.py's TAP_GND_X_LOCAL/TAP_VDD_X_LOCAL), drop one
M2PIN+TXM2 "GND"/"VDD" pin marker at the bottom BBOX edge (Y=0) and
one at the top BBOX edge (Y=core_h), using the exact same PIN_SIZE_UM
box-centered-on-label convention route_top_pins_nrow_fm.py's
add_m2_pin() uses for signal ports (v48 fix: label dead-center of its
own box, required for KLayout's LVS text-based pin extractor to
resolve a name at all -- see that script's add_m2_pin() docstring).

Input: layout/step8/v9_step_8_squeezed_top_pins_routed.gds (0 shorts,
DRC 0, current final core deliverable).
Output: layout/step8/v9_step_9_power_pins_added.gds -- same content,
plus 4 columns x 2 nets x 2 edges = 16 new VDD/GND pin markers.
"""
import json

import klayout.db as db

TOP_CELL_NAME = "i2c_slave_async_nrow_fm"
M2PIN_LAYER = (49, 1)
TXM2_LAYER = (49, 0)

TAP_GND_X_LOCAL = (1.0, 4.4)
TAP_VDD_X_LOCAL = (6.4, 9.8)
PIN_SIZE_UM = 3.0  # matches route_top_pins_nrow_fm.py's PIN_SIZE_UM

PLACEMENT_JSON = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/placement_nrow_fm_v9.json"
IN_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/layout/step8/v9_step_8_squeezed_top_pins_routed.gds"
OUT_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/layout/step8/v9_step_9_power_pins_added.gds"


def find_tap_x_positions(placement):
    xs = set()
    for inst in placement["rows"][0]:
        if inst["type"] == "TAP2":
            xs.add(round(inst["x"], 3))
    return sorted(xs)


def main(placement_json=PLACEMENT_JSON, in_gds=IN_GDS, out_gds=OUT_GDS, core_h=None):
    placement = json.load(open(placement_json))
    tap_xs = find_tap_x_positions(placement)

    layout = db.Layout()
    layout.read(in_gds)
    dbu = layout.dbu
    top = layout.cell(TOP_CELL_NAME)
    m2pin_idx = layout.layer(*M2PIN_LAYER)
    txm2_idx = layout.layer(*TXM2_LAYER)

    if core_h is None:
        core_h = top.bbox().top * dbu  # actual drawn BBOX top edge

    def um(v):
        return int(round(v / dbu))

    def add_m2_pin(cx, cy, label):
        half = PIN_SIZE_UM / 2.0
        top.shapes(m2pin_idx).insert(db.Box(um(cx - half), um(cy - half), um(cx + half), um(cy + half)))
        top.shapes(txm2_idx).insert(db.Text(label, db.Trans(um(cx), um(cy))))

    n = 0
    for tap_x in tap_xs:
        for label, (lx0, lx1) in (("GND", TAP_GND_X_LOCAL), ("VDD", TAP_VDD_X_LOCAL)):
            cx = tap_x + (lx0 + lx1) / 2.0
            # bottom BBOX edge (Y=0): box spans [0, PIN_SIZE_UM], same
            # convention as route_top_pins_nrow_fm.py's row0 signal pins
            # (y_end=0, box centered at y_end+PIN_SIZE_UM/2).
            add_m2_pin(cx, PIN_SIZE_UM / 2.0, label)
            n += 1
            # top BBOX edge (Y=core_h): box spans [core_h-PIN_SIZE_UM, core_h].
            add_m2_pin(cx, core_h - PIN_SIZE_UM / 2.0, label)
            n += 1

    layout.write(out_gds)
    print(f"wrote {out_gds}")
    print(f"added {n} VDD/GND pin marker(s): {len(tap_xs)} TAP column(s) x 2 net(s) x 2 edge(s)")
    print(f"core_h used: {core_h}")


if __name__ == "__main__":
    main()
