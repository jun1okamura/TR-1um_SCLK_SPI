#!/usr/bin/env python3
"""explore_rows.py -- how many placement rows should this design use?

Partitions the gate-level netlist into N rows the way gen_placement_nrow_fm.py
does (balanced by cell WIDTH, minimising the number of nets that have to leave
a row), then reports what each choice of N costs geometrically, using the
TR-1um_Async_I2C flow's own numbers:

    row height   64.8 um        (LEF MACRO SIZE)
    track pitch   5.4 um        (compaction_info_nrow_fm_v10_empirical.json)
    row width  <= 1620 um       (row_width_um in the same file)
    channels    = N + 1         (one above and below every row)

A net whose cells all land in one row needs no channel track at all -- that is
the whole point of the row-aware flow -- so the channel budget is driven by the
nets that span rows.  Top-level ports are counted separately: they have to
reach the core boundary whatever the partition does.

The channel heights printed here are the PRE-compaction budget (tracks x
pitch).  The real flow then removes genuinely unused tracks, which on the I2C
chip cut core height substantially, so treat these as an upper bound and use
them to COMPARE N, not as an absolute prediction.

  usage:  scripts/explore_rows.py [--rows 2 3 4 5 6] [--restarts 400]
"""
import argparse
import json
import os
import random
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlist_util as nu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(ROOT, "layout", "spi_slave_sclk_net_pnr.v")
INFO = os.path.join(ROOT, "lef", "cell_info.json")

ROW_H = 64.8
PITCH = 5.4
MAX_ROW_W = 1620.0
SUPPLY = {"VDD", "VSS", "GND", "vdd", "vss", "gnd", "1'b0", "1'b1",
          "1'h0", "1'h1"}


def load(net_path, info_path):
    src = open(net_path).read()
    insts = nu.parse(src)
    info = json.load(open(info_path))
    width = {i.name: info[i.cell]["width_um"] for i in insts}
    cellof = {i.name: i.cell for i in insts}

    m = re.search(r"module\s+\w+\s*\((.*?)\)\s*;", src, re.DOTALL)
    ports = set()
    if m:
        for p in m.group(1).split(","):
            p = p.strip()
            if p:
                ports.add(p)
    # bus ports appear as name[i] on instances; keep the base names too
    net_cells = defaultdict(set)
    for i in insts:
        for pin, n in i.conns.items():
            n = n.strip()
            if n in SUPPLY or not n:
                continue
            net_cells[n].add(i.name)
    return insts, width, cellof, net_cells, ports


def is_port_net(net, ports):
    base = net.split("[")[0]
    return base in ports or net in ports


def cut_cost(assign, net_cells):
    """number of channel crossings summed over all nets (rows spanned - 1)."""
    c = 0
    for cells in net_cells.values():
        rows = {assign[x] for x in cells if x in assign}
        if len(rows) > 1:
            c += max(rows) - min(rows)
    return c


def balanced_init(names, width, n, rng, cap=None):
    order = names[:]
    rng.shuffle(order)
    target = cap if cap else sum(width.values()) / n
    assign, cur, r = {}, 0.0, 0
    for x in order:
        if cur + width[x] > target and r < n - 1:
            r += 1
            cur = 0.0
        assign[x] = r
        cur += width[x]
    return assign


def row_widths(assign, width, n):
    w = [0.0] * n
    for x, r in assign.items():
        w[r] += width[x]
    return w


def refine(assign, width, net_cells, n, rng, tol=0.15, passes=60, row_cap=None):
    total = sum(width.values())
    cap = row_cap if row_cap else total / n * (1 + tol)
    best = cut_cost(assign, net_cells)
    for _ in range(passes):
        improved = False
        w = row_widths(assign, width, n)
        for x in list(assign):
            r0 = assign[x]
            for r1 in range(n):
                if r1 == r0 or w[r1] + width[x] > cap:
                    continue
                assign[x] = r1
                c = cut_cost(assign, net_cells)
                if c < best:
                    best = c
                    w[r0] -= width[x]
                    w[r1] += width[x]
                    improved = True
                    break
                assign[x] = r0
        if not improved:
            break
    return best


def analyse(n, names, width, net_cells, ports, restarts, seed=0, row_cap=None):
    rng = random.Random(seed)
    best_a, best_c = None, None
    for _ in range(restarts):
        a = balanced_init(names, width, n, rng, cap=row_cap)
        c = refine(a, width, net_cells, n, rng, row_cap=row_cap)
        if best_c is None or c < best_c:
            best_a, best_c = dict(a), c
    assign = best_a

    cross = [0] * (n + 1)          # channel i sits above row i
    local = port = 0
    for net, cells in net_cells.items():
        rows = sorted({assign[x] for x in cells if x in assign})
        if is_port_net(net, ports):
            port += 1
            # a port net leaves through the nearest core edge (the real flow
            # pulls top-level pins out to the core BBOX on all four sides)
            up = rows[0] + 1                  # channels crossed going up
            dn = n - rows[-1]                 # channels crossed going down
            if up <= dn:
                for ch in range(rows[0] + 1):
                    cross[ch] += 1
            else:
                for ch in range(rows[-1] + 1, n + 1):
                    cross[ch] += 1
            continue
        if len(rows) == 1:
            local += 1
            continue
        for ch in range(rows[0] + 1, rows[-1] + 1):
            cross[ch] += 1

    heights = [t * PITCH for t in cross]
    w = row_widths(assign, width, n)
    core_w = row_cap if row_cap else max(w)
    return dict(n=n, assign=assign, cross=cross, heights=heights,
                row_w=w, max_row_w=max(w), core_w=core_w,
                core_h=n * ROW_H + sum(heights),
                fill=n * core_w - sum(w),
                local=local, port=port, spanning=len(net_cells) - local - port)


def main(net_path=NET, info_path=INFO, rows=(2, 3, 4, 5, 6), restarts=300,
         row_cap=None):
    insts, width, cellof, net_cells, ports = load(net_path, info_path)
    names = [i.name for i in insts]
    total_w = sum(width.values())
    print(f"netlist : {os.path.relpath(net_path, ROOT)}")
    print(f"cells   : {len(insts)}   total cell width {total_w:.1f} um")
    print(f"nets    : {len(net_cells)}  (ports {sum(1 for x in net_cells if is_port_net(x, ports))})")
    print(f"limits  : row width <= {MAX_ROW_W} um, row height {ROW_H} um, "
          f"track pitch {PITCH} um\n")

    hdr = (f"{'rows':>4} {'row w(um)':>10} {'fit?':>5} {'local':>6} {'span':>5} "
           f"{'max ch tracks':>14} {'core WxH (um)':>20} {'area(mm2)':>10}")
    print(hdr)
    print("-" * len(hdr))
    res = []
    for n in rows:
        r = analyse(n, names, width, net_cells, ports, restarts, seed=n,
                    row_cap=row_cap)
        fit = "OK" if r["max_row_w"] <= (row_cap or MAX_ROW_W) else "NO"
        area = r["core_w"] * r["core_h"] / 1e6
        print(f"{n:4d} {r['max_row_w']:10.1f} {fit:>5} {r['local']:6d} "
              f"{r['spanning']:5d} {max(r['cross']):14d} "
              f"{r['core_w']:8.0f} x {r['core_h']:7.0f} {area:10.3f}")
        res.append(r)

    for r in res:
        print(f"\n--- {r['n']} rows ---")
        print(f"  row widths      : "
              + ", ".join(f"{w:.1f}" for w in r["row_w"]))
        print(f"  channel tracks  : {r['cross']}")
        print(f"  channel heights : "
              + ", ".join(f"{h:.0f}" for h in r["heights"]) + " um")
        print(f"  nets local/span/port : {r['local']}/{r['spanning']}/{r['port']}")
        print(f"  row occupancy   : "
              + ", ".join(f"{w/r['core_w']*100:.0f}%" for w in r["row_w"])
              + f"   (FILL needed {r['fill']:.0f} um = "
              f"{r['fill']*ROW_H/1e3:.1f} x10^3 um2)")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--netlist", default=NET)
    ap.add_argument("--cell-info", default=INFO)
    ap.add_argument("--rows", type=int, nargs="+", default=[2, 3, 4, 5, 6])
    ap.add_argument("--restarts", type=int, default=300)
    ap.add_argument("--row-width", type=float, default=None,
                    help="fix the row width (um) instead of balancing rows; "
                         "rows then only have to FIT, so the partitioner is "
                         "free to put whatever minimises the cut in each row")
    a = ap.parse_args()
    main(a.netlist, a.cell_info, tuple(a.rows), a.restarts, a.row_width)
