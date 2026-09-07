import sys
import klayout.db as db

GDS = sys.argv[1] if len(sys.argv) > 1 else \
    "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_routed.gds"
# BUG FOUND 2026-08-29 (design_notes.md 79.8): TOP_CELL was hardcoded to
# the CORE cell name regardless of which GDS was passed in. Every call
# this session against layout/step8/v9_top_routed.gds (chip-level, top
# cell "tr_1um_i2c_slave_async") was silently checking the CORE cell
# "i2c_slave_async_nrow_fm" instead -- which is nested INSIDE the chip
# cell, not the other way around, so begin_shapes_rec() on it never saw
# any of the chip-level power/signal routing at all. Every "0 violations"
# report for the chip-level GDS this session was checking nothing
# relevant. Real KLayout DRC (run by the user) found 50 real violations
# this script had completely missed as a direct result. Fixed: accept an
# explicit top-cell override (2nd CLI arg), defaulting to the core name
# only for backward compatibility with the earlier core-only checks.
TOP_CELL = sys.argv[2] if len(sys.argv) > 2 else "i2c_slave_async_nrow_fm"

layout = db.Layout()
layout.read(GDS)
dbu = layout.dbu
top = layout.cell(TOP_CELL)


def idx(l):
    return layout.layer(*l)


def check(layer, minw, mins, label):
    r = db.Region(top.begin_shapes_rec(idx(layer))).merged()
    w = r.width_check(int(round(minw / dbu)))
    s = r.space_check(int(round(mins / dbu)))
    print(f"{label}: width viol={w.count()} space viol={s.count()}")


check((13, 0), 1.8, 1.4, 'M1')
check((20, 0), 3.0, 2.0, 'M2')

m1 = db.Region(top.begin_shapes_rec(idx((13, 0)))).merged()
m2 = db.Region(top.begin_shapes_rec(idx((20, 0)))).merged()
gc = db.Region(top.begin_shapes_rec(idx((8, 1)))).merged()
v1 = db.Region(top.begin_shapes_rec(idx((19, 0))))

print('V1 space viol:', v1.space_check(int(round(1.5 / dbu))).count())
print('V1 enclosed by M1<1.0 viol:', v1.enclosed_check(m1, int(round(1.0 / dbu))).count())
print('V1 enclosed by M2<1.0 viol:', v1.enclosed_check(m2, int(round(1.0 / dbu))).count())
print('V1-GC space<1.2 viol:', v1.separation_check(gc, int(round(1.2 / dbu))).count())
