#!/usr/bin/env python3
"""check_cell_spice.py -- cross-check the LVS reference netlist against the
actual layout, before handing either to KLayout.

A real LVS run tells you *that* something disagrees; it is much cheaper to
catch the two classes of error this project has already hit, up front:

  1. a cell whose SPICE body and GDS geometry are different circuits.  BUF_X2
     was exactly that: the stale lef/BUF_X2.sch had four transistors (it was a
     copy of BUF_X1's schematic) while the DRC-clean BUF_X2 in
     lef/TR-1um_STDCELL.gds has six.
  2. a netlist that has drifted from the placement -- an instance the router
     placed and the netlist does not mention, or the reverse.

CHECK 1 -- cell bodies (lef/TR-1um_STDCELL.spice vs lef/TR-1um_STDCELL.gds)
    A crude device extractor pulls every poly-over-active gate out of the cell
    geometry and works out each one's W and L.  It deliberately compares
    FOLD-INVARIANT quantities -- device count is *not* one of them: BUFTH draws
    its w=10.2u PMOS and its w=6.8u NMOS as two parallel fingers each, so the
    GDS has 10 gate shapes where the schematic has 8 devices.  Total PMOS width,
    total NMOS width and the set of channel lengths survive folding, so those
    are what get compared.  A mismatch there is real.

CHECK 2 -- instance census (the generated .spice vs the routed GDS)
    Counts cell instances of each type in layout/step10's GDS and compares them
    against the cell calls plus inline fill devices in the netlist.

  usage:  scripts/check_cell_spice.py [--gds layout/step10/route_step_6_squeezed.gds]
"""
import argparse
import collections
import os
import re
import sys

import gdstk

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

CELL_SPICE = os.path.join(_cfg.ROOT, "lef", "TR-1um_STDCELL.spice")
LVS_SPICE = os.path.join(_cfg.LAYOUT, _cfg.TOP_CELL_NAME + ".spice")
DEFAULT_ROUTED_GDS = os.path.join(_cfg.LAYOUT, "step10", "route_step_6_squeezed.gds")

# process layers, same numbering scripts/drc_check_cells.py uses
POLY = (8, 1)
PACT = (3, 1)          # p+ implant: PMOS source/drain and the substrate taps
NACT = (3, 2)          # n+ implant: NMOS source/drain and the n-well taps


def _gate_shapes(cell, actlayer):
    """poly AND active, one polygon per transistor FINGER."""
    poly = [p for p in cell.polygons if (p.layer, p.datatype) == POLY]
    act = [p for p in cell.polygons if (p.layer, p.datatype) == actlayer]
    if not poly or not act:
        return []
    out = []
    for g in gdstk.boolean(poly, act, "and"):
        bb = g.bounding_box()
        out.append((round(bb[1][1] - bb[0][1], 2), round(bb[1][0] - bb[0][0], 2)))  # (W, L)
    return out


def gds_cell_summary(cell):
    """(n_fingers, total W, set of L) per device kind, from geometry."""
    res = {}
    for actlayer, kind in ((PACT, "PMOS"), (NACT, "NMOS")):
        fingers = _gate_shapes(cell, actlayer)
        res[kind] = (len(fingers),
                     round(sum(w for w, _ in fingers), 2),
                     tuple(sorted({l for _, l in fingers})))
    return res


def spice_cell_summary(body):
    res = {"PMOS": [0, 0.0, set()], "NMOS": [0, 0.0, set()]}
    for line in body.splitlines():
        t = line.split()
        if not t or t[0][0] not in "Mm" or len(t) < 6:
            continue
        kind = t[5].upper()
        if kind not in res:
            continue
        w = float(re.search(r"\bw=([\d.]+)u", line).group(1))
        l = float(re.search(r"\bl=([\d.]+)u", line).group(1))
        # `m=N` means N of these in parallel -- BUF_X2's export writes its
        # doubled output stage that way, and the GDS draws the N fingers.
        mm = re.search(r"\bm=(\d+)", line)
        mult = int(mm.group(1)) if mm else 1
        res[kind][0] += mult
        res[kind][1] += w * mult
        res[kind][2].add(round(l, 2))
    return {k: (v[0], round(v[1], 2), tuple(sorted(v[2]))) for k, v in res.items()}


def load_bodies(path):
    bodies, cur, buf = {}, None, []
    for line in open(path).read().splitlines():
        s = line.strip()
        if s.lower().startswith(".subckt "):
            cur, buf = s.split()[1], [line]
        elif cur is not None:
            buf.append(line)
            if s.lower().startswith(".ends"):
                bodies[cur] = "\n".join(buf)
                cur = None
    return bodies


def check_cell_bodies():
    bodies = load_bodies(CELL_SPICE)
    lib = gdstk.read_gds(_cfg.CELL_GDS)
    cells = {c.name: c for c in lib.cells}
    bad = 0
    print(f"cell bodies: {os.path.relpath(CELL_SPICE, _cfg.ROOT)} vs "
          f"{os.path.relpath(_cfg.CELL_GDS, _cfg.ROOT)}")
    for name in sorted(bodies):
        if name not in cells:
            print(f"  FAIL {name}: no such cell in the GDS")
            bad += 1
            continue
        g, s = gds_cell_summary(cells[name]), spice_cell_summary(bodies[name])
        ok = all(g[k][1] == s[k][1] and g[k][2] == s[k][2] for k in ("PMOS", "NMOS"))
        folded = any(g[k][0] != s[k][0] for k in ("PMOS", "NMOS"))
        note = ""
        if ok and folded:
            note = ("  (GDS draws %d/%d fingers for %d/%d devices -- folded)"
                    % (g["PMOS"][0], g["NMOS"][0], s["PMOS"][0], s["NMOS"][0]))
        print(f"  {'ok  ' if ok else 'FAIL'} {name:10s} "
              f"W(P)={s['PMOS'][1]:6.1f}u W(N)={s['NMOS'][1]:6.1f}u "
              f"{s['PMOS'][0] + s['NMOS'][0]:2d} device(s){note}")
        if not ok:
            print(f"       gds  {g}")
            print(f"       cir  {s}")
            bad += 1
    return bad


def check_instance_census(routed_gds):
    print(f"\ninstance census: {os.path.relpath(LVS_SPICE, _cfg.ROOT)} vs "
          f"{os.path.relpath(routed_gds, _cfg.ROOT)}")
    lib = gdstk.read_gds(routed_gds)
    top = next((c for c in lib.cells if c.name == _cfg.TOP_CELL_NAME), None)
    if top is None:
        top = lib.top_level()[0]
    lef_cells = {c.name for c in gdstk.read_gds(_cfg.CELL_GDS).cells}
    gds_counts = collections.Counter()
    for ref in top.references:
        nm = ref.cell.name if ref.cell else ref.cell_name
        if nm in lef_cells:
            rep = ref.repetition
            gds_counts[nm] += rep.size if (rep is not None and rep.size) else 1

    text = open(LVS_SPICE).read()
    body = text.split(".ends", 1)[0]
    net_counts = collections.Counter()
    for m in re.finditer(r"^x\S+\s+.*\s+(\S+)\s*$", body, re.M):
        net_counts[m.group(1)] += 1
    for m in re.finditer(r"^M_(\w+?)_\d+_[pn]\s", body, re.M):
        net_counts[m.group(1)] += 0.5      # two devices per placed fill cell

    bad = 0
    for name in sorted(set(gds_counts) | set(net_counts)):
        g, n = gds_counts.get(name, 0), int(net_counts.get(name, 0))
        if name in ("TAP2",):
            print(f"  ok   {name:10s} gds={g:3d} netlist=  - (no devices, correctly absent)")
            continue
        ok = g == n
        print(f"  {'ok  ' if ok else 'FAIL'} {name:10s} gds={g:3d} netlist={n:3d}")
        if not ok:
            bad += 1
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gds", default=DEFAULT_ROUTED_GDS)
    args = ap.parse_args()

    bad = check_cell_bodies()
    bad += check_instance_census(args.gds)
    print()
    if bad:
        print(f"{bad} problem(s) -- fix these before running LVS")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
