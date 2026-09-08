#!/usr/bin/env python3
"""check_chip.py -- check the chip-level routing.

Three questions, all asked against the real geometry rather than the router's
own bookkeeping:

  1. DRC, DIFFERENTIALLY.  The pad ring arrives with a handful of width and
     spacing markers of its own (it is a fixed, fabricated block and its
     internal geometry is not this project's to argue with), so a raw count
     says nothing.  Every rule is run twice -- once on the assembled but
     unrouted GDS, once on the routed one -- and only markers that are NEW are
     reported, with coordinates.

  2. PTECT.  Layer 63/1 is a keep-out: the PDK deck (01_Basics.drc) makes any
     M1, M2, V1, poly or active inside it an error.  Checked separately because
     it is the constraint that shapes this chip's whole routing topology.

  3. CONNECTIVITY.  M1 and M2 joined through V1 are walked into connected
     components, and every net in the routing plan is checked: all of its
     endpoints -- the core pin and each GIO terminal -- must land in ONE
     component, and no component may hold endpoints of two different nets.
     That is the check that catches both an open and a short, and it does not
     trust anything the router recorded.

  usage:  scripts/check_chip.py [--routed GDS] [--baseline GDS]
"""
import argparse
import collections
import json
import os
import sys

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

# The latest stage that exists: the logo last, then the bond-pad pins, then the
# routing.  Checking an earlier one because a later one was not regenerated is
# exactly how a stale result gets believed.
ROUTED = next(p for p in (os.path.join(_cfg.CHIP, "step4_final.gds"),
                          os.path.join(_cfg.CHIP, "step3_top_pins.gds"),
                          os.path.join(_cfg.CHIP, "step2_routed.gds"))
              if os.path.exists(p))
BASELINE = os.path.join(_cfg.CHIP, "step1_assembled.gds")
PLAN = os.path.join(_cfg.CHIP, "signal_routing_plan.json")

M1, M2, V1, GC = (13, 0), (20, 0), (19, 0), (8, 1)
PTECT = (63, 1)
KEEPOUT_LAYERS = [M1, M2, V1, GC, (3, 1), (3, 2), (11, 0)]

RULES = [(M1, 1.8, 1.4, "M1"), (M2, 3.0, 2.0, "M2")]


def regions(gds, cell):
    ly = db.Layout()
    ly.read(gds)
    c = ly.cell(cell)
    if c is None:
        raise SystemExit(f"{cell} not found in {gds}")
    return ly, c


def markers(gds, cell):
    """{label: [(x, y) of each violation]}"""
    ly, c = regions(gds, cell)
    u = ly.dbu
    out = {}
    for layer, minw, mins, label in RULES:
        r = db.Region(c.begin_shapes_rec(ly.layer(*layer))).merged()
        for kind, res in (("width", r.width_check(int(round(minw / u)))),
                          ("space", r.space_check(int(round(mins / u))))):
            pts = []
            for e in res.each():
                b = e.bbox()
                pts.append((round((b.left + b.right) / 2 * u, 2),
                            round((b.bottom + b.top) / 2 * u, 2)))
            out[f"{label} {kind}"] = sorted(pts)
    v1 = db.Region(c.begin_shapes_rec(ly.layer(*V1)))
    out["V1 space"] = [(round((e.bbox().left + e.bbox().right) / 2 * u, 2),
                        round((e.bbox().bottom + e.bbox().top) / 2 * u, 2))
                       for e in v1.merged().space_check(int(round(1.5 / u))).each()]
    for name, lay, enc in (("V1 in M1", M1, 1.0), ("V1 in M2", M2, 1.0)):
        m = db.Region(c.begin_shapes_rec(ly.layer(*lay))).merged()
        bad = v1.merged().not_inside(m.sized(-int(round(enc / u))))
        out[name] = [(round((p.bbox().left + p.bbox().right) / 2 * u, 2),
                      round((p.bbox().bottom + p.bbox().top) / 2 * u, 2))
                     for p in bad.each()]
    return out


def ptect_violations(gds, cell, box, net_shapes):
    """Did the SIGNAL routing respect the keep-out?

    route_chip.py deletes PTECT once every signal net is placed -- the layer is
    this project's own marker, not something that gets fabricated, and the GND
    bottom bus bar and the logo both live in the space it claimed.  So the layer
    is gone by the time anyone checks, and checking the layout against a layer
    that is not there would silently pass.

    Instead: the box comes from the geometry JSON, and each net's own recorded
    shapes are tested against it.  VDD/GND are exempt by name -- crossing it is
    the point -- and everything else has to be outside, which is the guarantee
    the U corridor exists to provide.
    """
    ly, c = regions(gds, cell)
    u = ly.dbu
    out = {}
    x0, y0, x1, y1 = box
    ours = db.DBox(x0, y0, x1, y1).to_itype(u)
    keep = db.Region(c.begin_shapes_rec(ly.layer(*PTECT))).merged()
    mine = keep & db.Region(ours)
    if not mine.is_empty():
        out["this project's own PTECT marker is still in the layout"] = \
            [f"{mine.count()} shape(s) on {PTECT} inside {box}"]
    # Whatever PTECT the FRAME itself brings -- three die-corner boxes -- is a
    # real keep-out that nothing may enter, before or after ours is removed.
    for lay in KEEPOUT_LAYERS:
        hit = db.Region(c.begin_shapes_rec(ly.layer(*lay))).merged() & keep
        if not hit.is_empty():
            out[f"frame PTECT, layer {lay}"] = \
                [(round(p.bbox().left * u, 2), round(p.bbox().bottom * u, 2),
                  round(p.bbox().right * u, 2), round(p.bbox().top * u, 2))
                 for p in hit.each()]
    for net, shapes in sorted(net_shapes.items()):
        if net in ("VDD", "GND"):
            continue
        for lay, sx0, sy0, sx1, sy1 in shapes:
            if sx1 > x0 and sx0 < x1 and sy1 > y0 and sy0 < y1:
                out.setdefault(net, []).append((lay, sx0, sy0, sx1, sy1))
    return out


def extract(gds, cell):
    """KLayout's own connectivity extraction: M1 and M2 joined through V1.

    Hand-rolling this over 12,500 vias and 1,400 merged polygons was both slow
    and wrong; LayoutToNetlist is the engine built for it, and probe_net gives
    a net back for a point, which is exactly the question being asked here.
    """
    ly = db.Layout()
    ly.read(gds)
    c = ly.cell(cell)
    if c is None:
        raise SystemExit(f"{cell} not found in {gds}")
    l2n = db.LayoutToNetlist(db.RecursiveShapeIterator(ly, c, []))
    rm1 = l2n.make_polygon_layer(ly.layer(*M1), "M1")
    rm2 = l2n.make_polygon_layer(ly.layer(*M2), "M2")
    rv1 = l2n.make_polygon_layer(ly.layer(*V1), "V1")
    for r in (rm1, rm2, rv1):
        l2n.connect(r)
    l2n.connect(rm1, rv1)
    l2n.connect(rv1, rm2)
    l2n.extract_netlist()
    return l2n, rm1, rm2


def probe(l2n, layers, x, y):
    """The net at a point, or None.  Layer order matters where two nets legally
    overlap: the ring's VDD pin is M1 and its VSS pin is M2, and at the top edge
    they sit on top of each other by design."""
    for r in layers:
        n = l2n.probe_net(r, db.DPoint(x, y))
        if n is not None:
            return n
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routed", default=ROUTED)
    ap.add_argument("--baseline", default=BASELINE)
    args = ap.parse_args()
    cell = _cfg.CHIP_TOP_CELL
    bad = 0

    print(f"DRC, new markers only ({os.path.basename(args.routed)} vs "
          f"{os.path.basename(args.baseline)})")
    before, after = markers(args.baseline, cell), markers(args.routed, cell)
    for label in sorted(after):
        new = [p for p in after[label] if p not in before.get(label, [])]
        old = len(before.get(label, []))
        flag = "ok  " if not new else "FAIL"
        print(f"  {flag} {label:12s} {len(after[label]):3d} total, {old:3d} pre-existing, "
              f"{len(new):3d} new" + (f"  at {new[:4]}" if new else ""))
        bad += len(new)

    geom = json.load(open(os.path.join(_cfg.CHIP, "gio_connections.json")))["chip_geometry"]
    shapes_json = os.path.join(_cfg.CHIP, "step2_routed_net_shapes.json")
    net_shapes = json.load(open(shapes_json))
    print(f"\nPTECT keep-out {tuple(geom['ptect_box'])} -- signal nets only "
          f"(the layer itself is removed by route_chip.py; VDD/GND cross it "
          f"by design)")
    pv = ptect_violations(args.routed, cell, geom["ptect_box"], net_shapes)
    if pv:
        for net, hits in sorted(pv.items()):
            print(f"  FAIL {net}: {len(hits)} shape(s) inside, e.g. {hits[0]}")
            bad += len(hits)
    else:
        print(f"  ok   {len(net_shapes) - 2} signal/tie net(s) stay out of it, "
              f"our marker is gone, and nothing is inside the frame's own "
              f"corner PTECT boxes")

    print("\nconnectivity")
    plan = json.load(open(PLAN))
    l2n, rm1, rm2 = extract(args.routed, cell)
    circuit = l2n.netlist().circuit_by_name(cell)
    print(f"  {len(list(circuit.each_net()))} net(s) extracted from the geometry")
    owner = {}
    for name, net in sorted(plan["nets"].items()):
        pts = []
        if net["core"]:
            pts.append(("core", net["core"]["x"], net["core"]["y"]))
        for g in net["gio"]:
            pts.append((g["terminal"], g["x"], g["y"]))
        roots = {}
        for label, x, y in pts:
            n = probe(l2n, (rm2, rm1), x, y)
            roots[label] = None if n is None else n.expanded_name()
        uniq = set(roots.values())
        if None in uniq:
            print(f"  FAIL {name:<12} no metal at {[k for k, v in roots.items() if v is None]}")
            bad += 1
        elif len(uniq) > 1:
            print(f"  FAIL {name:<12} OPEN -- {len(uniq)} separate pieces: {roots}")
            bad += 1
        else:
            root = uniq.pop()
            if root in owner and owner[root] != name:
                print(f"  FAIL {name:<12} SHORT to {owner[root]}")
                bad += 1
            else:
                owner[root] = name
                print(f"  ok   {name:<12} {len(pts)} endpoint(s) in one component")

    # ---- power and tie-offs ----------------------------------------------
    print("\npower and tie-offs")
    import route_chip as rc
    geom = _cfg.chip_geometry()
    cl, cb, cr, ct = geom["core_chip_bbox"]
    pads = json.load(open(os.path.join(_cfg.CHIP, "gio_connections.json")))["pad_pin_coords"]
    conn = json.load(open(os.path.join(_cfg.CHIP, "gio_connections.json")))
    rails = {
        "VDD": [(f"core tap {x}", x, ct - 1.5) for x in (-801.9, -267.3, 267.3, 807.3)]
               + [("ring VDD pin (M1)", rc.GIO_VDD_PIN[0], rc.GIO_VDD_PIN[1], "M1")]
               + [(p, pads[p]["x"], pads[p]["y"])
                  for p, r in conn["power_ties"].items() if r == "VDD"],
        "GND": [(f"core tap {x}", x, cb + 1.5) for x in (-807.3, -272.7, 261.9, 801.9)]
               + [(f"bottom bus leg x={x}", x, rc.GND_BOT_BUS_Y) for x in rc.GND_LEG_X]
               + [(f"VSS strip {i}", rc.VSS_STRIP_X + dx, rc.GIO_VSS_PIN_Y)
                  for i, dx in enumerate(rc.VSS_STRIP_DX)]
               + [("VSS bond pad", -200.0, -1040.0)]
               + [(p, pads[p]["x"], pads[p]["y"])
                  for p, r in conn["power_ties"].items() if r == "GND"]
               + [(p, pads[p]["x"], pads[p]["y"]) for p in conn["dont_care_pins"]],
    }
    rail_net = {}
    for rail, pts in rails.items():
        pts = [p if len(p) == 4 else p + ("M2",) for p in pts]
        roots = {}
        for label, x, y, lay in pts:
            n = probe(l2n, (rm1, rm2) if lay == "M1" else (rm2, rm1), x, y)
            roots[label] = None if n is None else n.expanded_name()
        uniq = set(roots.values())
        if None in uniq or len(uniq) > 1:
            print(f"  FAIL {rail}: {len(uniq)} piece(s) -- {roots}")
            bad += 1
        else:
            rail_net[rail] = uniq.pop()
            print(f"  ok   {rail}: all {len(pts)} point(s) in one net "
                  f"({', '.join(p[0] for p in pts[:3])}, ...)")
    if len(set(rail_net.values())) < len(rail_net):
        print("  FAIL VDD and GND are the same net")
        bad += 1
    for rail, net in rail_net.items():
        if net in owner:
            print(f"  FAIL {rail} is shorted to signal {owner[net]}")
            bad += 1
    if len(rail_net) == 2 and len(set(rail_net.values())) == 2 \
            and not any(n in owner for n in rail_net.values()):
        print("  ok   VDD and GND are separate, and clear of every signal")

    # ---- bond pads: does each LVS pin sit on the net its name claims? -----
    print("\nbond pads (LVS pins)")
    import pin_list
    pads_xy = pin_list.bond_pads()
    ly_chk = db.Layout()
    ly_chk.read(args.routed)
    tc = ly_chk.cell(cell)
    labels = {s.text.string: (s.text.x * ly_chk.dbu, s.text.y * ly_chk.dbu)
              for s in tc.shapes(ly_chk.layer(49, 0)).each() if s.is_text()}
    padnet = {}
    for name in sorted(pads_xy):
        x, y, _ = pads_xy[name]
        if name not in labels:
            print(f"  FAIL {name:5s} no TXM2 label in the top cell")
            bad += 1
            continue
        n = probe(l2n, (rm2, rm1), x, y)
        padnet[name] = None if n is None else n.expanded_name()
    # What each pad SHOULD be, from the connection map.  A pad with a "P" entry
    # carries that core net.  An output-only pad has no "P": nothing routes to
    # it, because the pad cell drives its own pad node from OUT<n> internally --
    # so the right answer there is a net of its own, shared with no signal and
    # neither rail.
    want = {f"P{n}": spec.get("P") for n, spec in
            ((int(k[1:]), v) for k, v in conn["connections"].items())}
    want["VDD"], want["VSS"] = "VDD", "GND"
    for name in sorted(padnet):
        got = padnet[name]
        expect = want.get(name)
        if got is None:
            print(f"  FAIL {name:5s} pin is on no extracted net")
            bad += 1
            continue
        if expect in rail_net:
            ok, shown = got == rail_net[expect], expect
        elif expect is not None:
            ok, shown = owner.get(got) == expect, expect
        else:
            ok = got not in owner and got not in rail_net.values()
            shown = "pad node only (driven inside the pad cell)"
        print(f"  {'ok  ' if ok else 'FAIL'} {name:5s} -> "
              f"{owner.get(got, got)}  (expected {shown})")
        bad += 0 if ok else 1

    # ---- the chip-level reference netlist, against this same geometry -----
    ref = os.path.join(_cfg.CHIP, _cfg.CHIP_TOP_CELL + ".spice")
    if os.path.exists(ref):
        print(f"\nreference netlist ({os.path.basename(ref)}) vs the layout")
        import gen_lvs_spice_top as gt
        (rconn, _gp, gio_ports, core_ports, gio_net, core_net,
         _gb, _cb, _nc, _un, rproblems) = gt.build()
        for p_ in rproblems:
            print("  PROBLEM: " + p_)
            bad += 1
        terms = pin_list.frame_pins.load()
        # every probe-able point the reference names, grouped by its net
        group = collections.defaultdict(list)
        for pin, net in gio_net.items():
            if pin in terms:
                group[net].append((f"frame {pin}", terms[pin]["x"], terms[pin]["y"]))
        for port, net in core_net.items():
            e = plan["nets"].get(port)
            if e and e["core"]:
                group[net].append((f"core {port}", e["core"]["x"], e["core"]["y"]))
        checked = 0
        seen = {}
        for net, pts in sorted(group.items()):
            if net.startswith("NC_"):
                continue
            got = {}
            for label, x, y in pts:
                n = probe(l2n, (rm2, rm1), x, y)
                got[label] = None if n is None else n.expanded_name()
            uniq = set(got.values())
            if None in uniq:
                print(f"  FAIL {net:<12} no metal at "
                      f"{[k for k, v in got.items() if v is None]}")
                bad += 1
                continue
            if len(uniq) > 1:
                print(f"  FAIL {net:<12} the reference ties these together, the "
                      f"layout does not: {got}")
                bad += 1
                continue
            ex = uniq.pop()
            if ex in seen and seen[ex] != net:
                print(f"  FAIL {net:<12} shares an extracted net with {seen[ex]}")
                bad += 1
                continue
            seen[ex] = net
            checked += 1
        print(f"  ok   {checked} net(s) with a probe point agree with the layout, "
              f"and no two of them are the same extracted net")
    else:
        print(f"\nreference netlist not generated yet ({ref})")

    print()
    if bad:
        print(f"{bad} problem(s)")
        raise SystemExit(1)
    print("all chip-level checks passed")


if __name__ == "__main__":
    main()
