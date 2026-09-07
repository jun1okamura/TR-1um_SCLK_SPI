#!/usr/bin/env python3
"""place.py -- nrow standard-cell placement, one GDS per step.

Reproduces the placement convention measured on the TR-1um_Async_I2C chip
(TR-1um_I2C_2026/src/tr_1um_i2c_slave_async.gds):

  * cells abut on their prBoundary (layer 235/0), which is 0..W x 0..64.8 in
    every cell, so a cell is placed simply at (cursor_x, row_y)
  * every reference is rotation 0, no reflection -- rows are NOT mirrored
  * every row starts at x=0 and ends at exactly ROW_W (1620.0 um)
  * TAP2 cells sit at fixed x = 0, 534.6, 1069.2 and ROW_W-10.8 in every row
  * the remainder is packed with FILL3 (16.2) / FILL2 (10.8)

Steps, each leaving its own GDS + JSON under layout/stepN/ :

  step1  row assignment      FM-style partition, cells abutted from x=0
  step2  intra-row ordering  iterative barycentre ordering (HPWL-driven)
  step3  TAP insertion       power taps at the fixed pitch
  step4  FILL insertion      pad every row to exactly ROW_W  <- final

  usage:
    scripts/place.py                        # 2 rows, 1620 um, default netlist
    scripts/place.py --rows 3 --row-width 1620
    scripts/place.py --restarts 1200 --order-passes 60
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlist_util as nu
import explore_rows as ex

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(ROOT, "layout", "spi_slave_sclk_net_pnr.v")
INFO = os.path.join(ROOT, "lef", "cell_info.json")
STDCELL = os.path.join(ROOT, "lef", "TR-1um_STDCELL.gds")
OUTDIR = os.path.join(ROOT, "layout")

ROW_H = 64.8
ROW_W = 1620.0
PITCH = 5.4
TAP_CELL = "TAP2"
TAP_PITCH = 534.6                 # measured on the I2C chip
FILLS = [("FILL3", 16.2), ("FILL2", 10.8)]
PRI_CELL = "FILL2"                # reserved 2-track M2 corridor after each TAP
PRI_W = 10.8
GUARD_TRACKS = 2
MIN_CH_TRACKS = 4
CORE_CELL = "spi_slave_sclk_nrow_fm"


# ---------------------------------------------------------------- geometry
def channel_heights(assign, net_cells, ports, n):
    """channel i sits below row i; the last one is above the top row."""
    cross = [0] * (n + 1)
    for net, cells in net_cells.items():
        rows = sorted({assign[c] for c in cells if c in assign})
        if not rows:
            continue
        if ex.is_port_net(net, ports):
            up, dn = rows[0] + 1, n - rows[-1]
            rng = range(rows[0] + 1) if up <= dn else range(rows[-1] + 1, n + 1)
        else:
            rng = range(rows[0] + 1, rows[-1] + 1)
        for ch in rng:
            cross[ch] += 1
    tracks = [max(t + GUARD_TRACKS, MIN_CH_TRACKS) for t in cross]
    return cross, tracks, [t * PITCH for t in tracks]


def row_y(heights):
    """y of each row's prBoundary bottom."""
    ys, y = [], 0.0
    for i in range(len(heights) - 1):
        y += heights[i]
        ys.append(y)
        y += ROW_H
    return ys, y + heights[-1]


# ---------------------------------------------------------------- ordering
def hpwl(order, width, net_cells, ports, rows_y, assign):
    """half-perimeter wirelength over cell centres (ports excluded: they are
    pulled to the core boundary later, wherever the cell lands)."""
    cx, cy = {}, {}
    for r, seq in enumerate(order):
        x = 0.0
        for c in seq:
            cx[c] = x + width[c] / 2.0
            cy[c] = rows_y[r] + ROW_H / 2.0
            x += width[c]
    total = 0.0
    for net, cells in net_cells.items():
        if ex.is_port_net(net, ports):
            continue
        xs = [cx[c] for c in cells if c in cx]
        ys = [cy[c] for c in cells if c in cy]
        if len(xs) > 1:
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


def order_rows(assign, width, net_cells, ports, rows_y, n, passes=40, seed=1):
    """iterative barycentre ordering: repeatedly sort each row by the mean x
    of everything its cells connect to, keeping the best HPWL seen."""
    rng = random.Random(seed)
    order = [[c for c in assign if assign[c] == r] for r in range(n)]
    for seq in order:
        rng.shuffle(seq)
    best = [list(s) for s in order]
    best_hp = hpwl(order, width, net_cells, ports, rows_y, assign)

    nets_of = defaultdict(list)
    for net, cells in net_cells.items():
        if ex.is_port_net(net, ports):
            continue
        for c in cells:
            if c in assign:
                nets_of[c].append(net)

    for p in range(passes):
        cx = {}
        for r, seq in enumerate(order):
            x = 0.0
            for c in seq:
                cx[c] = x + width[c] / 2.0
                x += width[c]
        for r, seq in enumerate(order):
            key = {}
            for c in seq:
                xs = [cx[o] for net in nets_of[c] for o in net_cells[net]
                      if o in cx and o != c]
                key[c] = sum(xs) / len(xs) if xs else cx[c]
            seq.sort(key=lambda c: (key[c], c))
        hp = hpwl(order, width, net_cells, ports, rows_y, assign)
        if hp < best_hp:
            best_hp, best = hp, [list(s) for s in order]
        elif p % 7 == 6:                      # kick out of a fixed point
            r = rng.randrange(n)
            if len(order[r]) > 2:
                i, j = rng.randrange(len(order[r])), rng.randrange(len(order[r]))
                order[r][i], order[r][j] = order[r][j], order[r][i]
    return best, best_hp


# ------------------------------------------------------- TAP / FILL packing
def tap_positions(row_w):
    xs = []
    x = 0.0
    while x + 10.8 <= row_w - 10.8:
        xs.append(round(x, 3))
        x += TAP_PITCH
    xs.append(round(row_w - 10.8, 3))
    return xs


def pad(width_um):
    """express a gap as FILL3/FILL2 cells; gaps must be 0 or >= 10.8."""
    out, w = [], round(width_um, 3)
    if w < -1e-6:
        raise ValueError(f"negative gap {w}")
    for name, cw in FILLS:
        while w - cw >= -1e-6 and (abs(w - cw) < 1e-6 or w - cw >= 10.8 - 1e-6):
            out.append((name, cw))
            w = round(w - cw, 3)
    if abs(w) > 1e-6:
        raise ValueError(f"cannot fill a {w} um gap with FILL2/FILL3")
    return out


def row_segments(row_w):
    """[(x0, capacity), ...] between the fixed TAP slots."""
    taps = tap_positions(row_w)
    segs, prev = [], None
    for t in taps:
        if prev is not None:
            segs.append((round(prev + 10.8, 3), round(t - prev - 10.8, 3)))
        prev = t
    return taps, segs


def interleave(cells, fills):
    """Spread FILL cells evenly between the logic cells of a segment.

    The I2C placement distributes FILL2/FILL3 through the row rather than
    dumping the slack at the right end; the resulting gaps give the channel
    router somewhere to drop feedthroughs and jogs.
    Both arguments and the result are lists of (cellname, instname, width).
    """
    if not fills:
        return list(cells)
    if not cells:
        return list(fills)
    slots = len(cells) + 1
    per = len(fills) / slots
    out, fi, acc = [], 0, 0.0
    for i in range(slots):
        acc += per
        while fi < len(fills) and fi + 1 <= acc + 1e-9:
            out.append(fills[fi])
            fi += 1
        if i < len(cells):
            out.append(cells[i])
    out.extend(fills[fi:])
    return out


def pack_row(seq, width, row_w, with_tap, with_fill, fill_mode="distributed"):
    """Return ([(cellname, instname_or_None, x, w), ...], end_x).

    Without taps the cells simply abut from x=0 (steps 1 and 2).  With taps
    the row is split into segments between the fixed TAP slots and cells are
    packed into them left to right.  A cell is only accepted if the leftover
    stays fillable -- FILL2/FILL3 can express any multiple of 5.4 um except
    5.4 itself, so a placement that would leave exactly one track is deferred
    to the next segment (its leftover then becomes 5.4 + w >= 16.2, fillable).
    """
    if not with_tap:
        out, x = [], 0.0
        for c in seq:
            out.append((cellinfo_name(c), c, x, width[c]))
            x = round(x + width[c], 3)
        return out, x

    taps, segs = row_segments(row_w)
    out, todo = [], list(seq)
    for si, (x0, cap) in enumerate(segs):
        out.append((TAP_CELL, None, taps[si], 10.8))
        if with_fill:
            # priority M2 corridor: a reserved FILL2 immediately after every
            # TAP, at the same x in every row, which the router picks up as a
            # preferred row-crossing landing column (route_channels_nrow_fm.py
            # auto-detects FILL2 instances named FILLPRI_*).
            out.append(("__PRI__", None, x0, PRI_W))
            x0 = round(x0 + PRI_W, 3)
            cap = round(cap - PRI_W, 3)
        picked, left = [], cap
        while todo:
            w = width[todo[0]]
            rest = round(left - w, 3)
            if rest < -1e-6 or (1e-6 < rest < 10.8 - 1e-6):
                break                      # would not fit, or leaves 5.4 um
            c = todo.pop(0)
            picked.append((cellinfo_name(c), c, w))
            left = rest
        fills = [(fn, None, fw) for fn, fw in
                 (pad(left) if (with_fill and left > 1e-6) else [])]
        items = interleave(picked, fills) if fill_mode == "distributed" \
            else picked + fills
        x = x0
        for name, inst, w in items:
            out.append((name, inst, x, w))
            x = round(x + w, 3)
    out.append((TAP_CELL, None, taps[-1], 10.8))
    if todo:
        raise SystemExit(f"!! {len(todo)} cell(s) did not fit in the row: "
                         f"{todo[:5]}")
    end = round(row_w, 3) if with_fill else max(x + w for _, _, x, w in out)
    out.sort(key=lambda e: e[2])
    return out, end


CELLOF = {}
def cellinfo_name(inst):
    return CELLOF[inst]


# ----------------------------------------------------------------- GDS out
def write_gds(path, rows, rows_y, core_w, core_h, top=CORE_CELL):
    import gdstk
    lib = gdstk.read_gds(STDCELL)
    src = {c.name: c for c in lib.cells}
    out = gdstk.Library(name=top, unit=1e-6, precision=1e-9)
    keep = set()
    for row in rows:
        for cname, _, _, _ in row:
            keep.add(PRI_CELL if cname == "__PRI__" else cname)

    def add_deep(c, seen):
        if c.name in seen:
            return
        seen.add(c.name)
        out.add(c)
        for r in c.references:
            add_deep(r.cell, seen)

    seen = set()
    for n in sorted(keep):
        add_deep(src[n], seen)

    core = out.new_cell(top)
    for r, row in enumerate(rows):
        for cname, _, x, _ in row:
            core.add(gdstk.Reference(
                src[PRI_CELL if cname == "__PRI__" else cname], (x, rows_y[r])))
    # prBoundary of the core itself, so the extent is unambiguous
    core.add(gdstk.rectangle((0, 0), (core_w, core_h), layer=235, datatype=0))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.write_gds(path)
    return path


def dump(step, tag, rows, rows_y, core_w, core_h, extra, verbose=True):
    d = os.path.join(OUTDIR, f"step{step}")
    os.makedirs(d, exist_ok=True)
    gds = os.path.join(d, f"place_step{step}_{tag}.gds")
    write_gds(gds, rows, rows_y, core_w, core_h)
    js = os.path.join(d, f"place_step{step}_{tag}.json")
    data = dict(step=step, tag=tag, core_w=core_w, core_h=core_h,
                row_h=ROW_H, row_y=rows_y,
                rows=[[dict(cell=(PRI_CELL if c == "__PRI__" else c),
                            inst=i, x=x, w=w,
                            pri=(c == "__PRI__")) for c, i, x, w in row]
                      for row in rows], **extra)
    json.dump(data, open(js, "w"), indent=1)
    if verbose:
        used = [sum(w for _, _, _, w in row) for row in rows]
        print(f"  step{step} {tag:9} -> {os.path.relpath(gds, ROOT)}")
        print(f"        rows: " + ", ".join(f"{u:.1f}um" for u in used)
              + f"   core {core_w:.1f} x {core_h:.1f} um")
    return gds


# -------------------------------------------------------------------- main
def main(net_path=NET, info_path=INFO, n=2, row_w=ROW_W,
         restarts=800, order_passes=40, seed=7, fill_mode="distributed"):
    global CELLOF
    insts, width, cellof, net_cells, ports = ex.load(net_path, info_path)
    CELLOF = cellof
    names = [i.name for i in insts]
    total = sum(width.values())
    taps_per_row = len(tap_positions(row_w))
    usable = row_w - taps_per_row * 10.8 - (taps_per_row - 1) * PRI_W
    print(f"placement: {len(insts)} cells, total width {total:.1f} um")
    print(f"  {n} rows x {row_w:.1f} um   (usable {usable:.1f} um/row after "
          f"{taps_per_row} TAPs + {taps_per_row - 1} priority corridors)")
    if total > n * usable:
        raise SystemExit(f"!! does not fit: need {total:.1f} um, "
                         f"have {n * usable:.1f} um")

    # ---- step 1 : row assignment
    res = ex.analyse(n, names, width, net_cells, ports, restarts, seed=seed)
    assign = res["assign"]
    cross, tracks, ch_h = channel_heights(assign, net_cells, ports, n)
    rows_y, core_h = row_y(ch_h)
    print(f"  channels: crossings {cross} -> tracks {tracks} -> "
          + ", ".join(f"{h:.1f}" for h in ch_h) + " um")
    print(f"  core height {core_h:.1f} um")

    order = [[c for c in names if assign[c] == r] for r in range(n)]
    hp1 = hpwl(order, width, net_cells, ports, rows_y, assign)
    rows = [pack_row(seq, width, row_w, False, False)[0] for seq in order]
    dump(1, "rows", rows, rows_y, row_w, core_h,
         dict(assign=assign, channel_crossings=cross, channel_tracks=tracks,
              channel_heights=ch_h, hpwl_um=round(hp1, 1)))

    # ---- step 2 : intra-row ordering
    order, hp2 = order_rows(assign, width, net_cells, ports, rows_y, n,
                            passes=order_passes, seed=seed)
    rows = [pack_row(seq, width, row_w, False, False)[0] for seq in order]
    print(f"        HPWL {hp1:.0f} -> {hp2:.0f} um "
          f"({(hp1 - hp2) / hp1 * 100:.1f}% better)")
    dump(2, "ordered", rows, rows_y, row_w, core_h,
         dict(assign=assign, hpwl_um=round(hp2, 1)))

    # ---- step 3 : TAP insertion (cells packed into the tap segments,
    #               FILL not yet placed so the free space stays visible)
    rows = [pack_row(seq, width, row_w, True, False)[0] for seq in order]
    dump(3, "tap", rows, rows_y, row_w, core_h,
         dict(assign=assign, tap_x=tap_positions(row_w),
              segments=row_segments(row_w)[1]))

    # ---- step 4 : FILL -> final
    packed = [pack_row(seq, width, row_w, True, True, fill_mode)
              for seq in order]
    rows = [p[0] for p in packed]
    ends = [p[1] for p in packed]
    gds = dump(4, "fill", rows, rows_y, row_w, core_h,
               dict(assign=assign, tap_x=tap_positions(row_w),
                    channel_crossings=cross, channel_tracks=tracks,
                    channel_heights=ch_h, hpwl_um=round(hp2, 1),
                    row_end_x=ends))

    # row assignment for scripts/insert_row_buffers.py
    ra = os.path.join(OUTDIR, "row_assignment.json")
    json.dump(assign, open(ra, "w"), indent=1)
    print(f"  wrote {os.path.relpath(ra, ROOT)} "
          f"(feed to insert_row_buffers.py --row-assignment)")
    return gds


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--netlist", default=NET)
    ap.add_argument("--cell-info", default=INFO)
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--row-width", type=float, default=ROW_W)
    ap.add_argument("--restarts", type=int, default=800)
    ap.add_argument("--order-passes", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fill-mode", choices=("distributed", "end"),
                    default="distributed",
                    help="spread FILL through the row (default, matches the "
                         "I2C placement) or push it to the segment end")
    a = ap.parse_args()
    main(a.netlist, a.cell_info, a.rows, a.row_width,
         a.restarts, a.order_passes, a.seed, a.fill_mode)
