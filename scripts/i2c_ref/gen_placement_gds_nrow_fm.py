"""
gen_placement_gds_nrow_fm.py

Section 38: N-row placement GDS builder (generalizes
gen_placement_gds_2row_fm.py to N rows / N+1 channel bands: bottom
margin, then alternating row/channel, ending in a top margin). Reads
LEF/placement_nrow_fm.json (schema: {"rows": [[...row0...], ...]}).
"""
import json
import sys

import klayout.db as db

sys.path.insert(0, "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/script")
from lef_parser import parse_lef  # noqa: E402

PLACEMENT_JSON = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/placement_nrow_fm.json"
CELL_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/TR-1um_STDCELL.gds"
OUT_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_placement.gds"
TOP_CELL_NAME = "i2c_slave_async_nrow_fm"

# CH_HEIGHTS[i] = height (um) of channel i, i = 0..n_rows (n_rows+1 channels
# total: channel 0 = bottom margin, channel n_rows = top margin, channel i
# (1<=i<=n_rows-1) = shared channel between row(i-1) and row(i)).
# v3 update: route_channels_nrow_fm.py's via_1-based jog mechanism claims a
# FRESH track per row-crossing (section 38 v3 docstring), which turned out
# to need one for essentially every crossing pin (33 jogs / 33 crossings in
# the first v3 run) -- channel2/channel3 overflowed their old budgets
# (43/32 and 44/34 tracks used). Raised with ~25-30% headroom on top of
# that measured usage (not the coarse estimate below, which doesn't model
# jog overhead at all).
CH_HEIGHTS = [90.0, 260.0, 240.0, 224.0, 100.0]


def main(placement_json=PLACEMENT_JSON, out_gds=OUT_GDS, ch_heights=None):
    global CH_HEIGHTS
    if ch_heights is not None:
        CH_HEIGHTS = ch_heights
    macros = parse_lef()
    placement = json.load(open(placement_json))
    row_h = placement["row_height"]
    row_w = placement["row_width"]
    rows = placement["rows"]
    n_rows = len(rows)
    assert len(CH_HEIGHTS) == n_rows + 1, f"need {n_rows + 1} channel heights, got {len(CH_HEIGHTS)}"

    # y-offset of each row's origin, and each channel's [y0, y0+h) band
    row_y0 = []
    ch_y0 = []
    y = 0.0
    for i in range(n_rows):
        ch_y0.append(y)
        y += CH_HEIGHTS[i]
        row_y0.append(y)
        y += row_h
    ch_y0.append(y)  # top margin channel
    y += CH_HEIGHTS[n_rows]
    core_h = y

    layout = db.Layout()
    layout.read(CELL_GDS)
    dbu = layout.dbu
    top = layout.create_cell(TOP_CELL_NAME)

    missing = set()
    for r, row_insts in enumerate(rows):
        y_off = row_y0[r]
        for inst in row_insts:
            gds_name = macros[inst["type"]]["foreign"]
            src = layout.cell(gds_name)
            if src is None:
                missing.add(gds_name)
                continue
            x_dbu = int(round(inst["x"] / dbu))
            y_dbu = int(round(y_off / dbu))
            top.insert(db.CellInstArray(src.cell_index(), db.Trans(db.Vector(x_dbu, y_dbu))))
    if missing:
        raise SystemExit(f"cells missing from {CELL_GDS}: {missing}")

    ann = layout.layer(250, 0)

    def box(x0, y0, x1, y1):
        return db.Box(int(round(x0 / dbu)), int(round(y0 / dbu)), int(round(x1 / dbu)), int(round(y1 / dbu)))

    top.shapes(ann).insert(box(0, 0, row_w, core_h))
    for i in range(n_rows + 1):
        top.shapes(ann).insert(box(0, ch_y0[i], row_w, ch_y0[i] + CH_HEIGHTS[i]))

    layout.write(out_gds)
    print(f"wrote {out_gds}")
    print(f"core bbox: (0,0)-({row_w:.1f},{core_h:.1f})")
    for i in range(n_rows + 1):
        label = "bottom margin" if i == 0 else ("top margin" if i == n_rows else f"channel {i}")
        print(f"  {label:16s} {ch_y0[i]:.1f} - {ch_y0[i] + CH_HEIGHTS[i]:.1f}")
        if i < n_rows:
            print(f"  {'row' + str(i):16s} {row_y0[i]:.1f} - {row_y0[i] + row_h:.1f}")
    print(f"instances per row: {[len(r) for r in rows]}")


if __name__ == "__main__":
    _args = sys.argv[1:]
    _pj = _args[0] if len(_args) > 0 and _args[0] != "-" else PLACEMENT_JSON
    _og = _args[1] if len(_args) > 1 and _args[1] != "-" else OUT_GDS
    main(placement_json=_pj, out_gds=_og)
