#!/usr/bin/env python3
"""verify_port_connectivity.py -- does every top-level pin actually reach its cell?

verify_connectivity_nrow_fm{,_m1m2}.py only checks the nets the CHANNEL router
recorded in pin_map (44 here); the ~30 "stub" nets -- a top-level port feeding
a single cell pin -- are outside its scope.  That blind spot is exactly how the
unrouted scalar ports survived to the final GDS: the BUFTH input nets
sclk/cs_n/sdio_in had a cell pin and a port, no channel route, and nothing
looked at them.

This walks the real geometry instead: it builds connected components over
M1 + M2 joined through V1, then for every top-level port checks that its PIN
marker and every cell pin on that net land in the same component.

  usage:  scripts/verify_port_connectivity.py [GDS]
"""
import argparse
import json
import os
import sys

import klayout.db as db

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spi_config as cfg          # noqa: E402
import netlist_parser            # noqa: E402
import highlight_top_pins_nrow_fm as hp  # noqa: E402

M1, M2, V1 = (13, 0), (20, 0), (19, 0)
PIN_TXT = [((49, 1), (49, 0)), ((48, 1), (48, 0))]


class Union:
    def __init__(self):
        self.p = {}

    def find(self, k):
        self.p.setdefault(k, k)
        while self.p[k] != k:
            self.p[k] = self.p[self.p[k]]
            k = self.p[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def build_components(ly, top):
    """(uf, [(layer_tag, index, dbox)]) -- merged M1/M2 shapes plus the V1
    vias that stitch them together."""
    parts = []
    for tag, (lay, dt) in (("M1", M1), ("M2", M2)):
        reg = db.Region(top.begin_shapes_rec(ly.layer(lay, dt))).merged()
        for i, poly in enumerate(reg.each()):
            b = poly.bbox()
            parts.append((tag, i,
                          db.DBox(b.left * ly.dbu, b.bottom * ly.dbu,
                                  b.right * ly.dbu, b.top * ly.dbu), poly))
    uf = Union()
    for tag, i, _b, _p in parts:
        uf.find((tag, i))
    vias = db.Region(top.begin_shapes_rec(ly.layer(*V1))).merged()
    for v in vias.each():
        b = v.bbox()
        vb = db.DBox(b.left * ly.dbu, b.bottom * ly.dbu,
                     b.right * ly.dbu, b.top * ly.dbu)
        touch = [(tag, i) for tag, i, b, _p in parts if b.overlaps(vb) or b.touches(vb)]
        for k in touch[1:]:
            uf.union(touch[0], k)
    return uf, parts


def owner(parts, uf, box):
    """component id of whatever shape covers `box` (its centre)."""
    cx, cy = (box.left + box.right) / 2.0, (box.bottom + box.top) / 2.0
    pt = db.DPoint(cx, cy)
    for tag, i, b, _p in parts:
        if b.contains(pt):
            return uf.find((tag, i))
    for tag, i, b, _p in parts:          # fall back to any overlap
        if b.overlaps(box):
            return uf.find((tag, i))
    return None


def main(gds=cfg.SQUEEZED_GDS, placement=cfg.PLACEMENT_JSON, net_path=cfg.NET_PATH):
    ly = db.Layout()
    ly.read(gds)
    top = ly.cell(cfg.TOP_CELL_NAME)
    uf, parts = build_components(ly, top)

    pl = json.load(open(placement))
    # The routed layout's row Y offsets come from the GDS, not from the
    # placement estimate (the compaction moves them).  Recover them from the
    # STANDARD CELL instances only -- the top cell is also full of via_1
    # PCell instances scattered through the channels, and taking every
    # instance's Y makes the rows unrecoverable.
    import lef_parser
    macros = set(lef_parser.parse_lef(cfg.LEF_PATH))
    ys = sorted({round(r.trans.disp.y * ly.dbu, 3) for r in top.each_inst()
                 if r.cell.name in macros})
    row_y0 = []
    for yv in ys:
        if not row_y0 or yv - row_y0[-1] > 1.0:
            row_y0.append(yv)
    assert len(row_y0) == len(pl["rows"]), \
        f"found {len(row_y0)} cell rows in the GDS, placement has {len(pl['rows'])}"

    resolver = netlist_parser._build_alias_resolver(open(net_path).read())
    # net -> [cell pin boxes]
    net_pins = {}
    for r, row in enumerate(pl["rows"]):
        for inst in row:
            for pname, pinfo in inst["pins"].items():
                if pinfo["use"] in ("POWER", "GROUND") or not pinfo["net"]:
                    continue
                for lay, x0, y0, x1, y1 in pinfo["rects"]:
                    if lay != "M2":
                        continue
                    net_pins.setdefault(pinfo["net"], []).append(
                        (inst["name"], pname,
                         db.DBox(x0, y0 + row_y0[r], x1, y1 + row_y0[r])))

    # port -> pin marker boxes, by label
    marker = {}
    for (pl_lay, tx_lay) in PIN_TXT:
        boxes = [s.dbbox() for s in top.shapes(ly.layer(*pl_lay)).each()
                 if s.is_box() or s.is_polygon()]
        for t in top.shapes(ly.layer(*tx_lay)).each():
            if not t.is_text():
                continue
            p = t.dtext.position()
            for b in boxes:
                if b.left <= p.x <= b.right and b.bottom <= p.y <= b.top:
                    marker.setdefault(t.dtext.string, []).append(b)
                    break

    ports = list(hp.SCALAR_PORTS)
    for bus, w in hp.BUS_PORTS.items():
        ports += [f"{bus}[{i}]" for i in range(w)]

    print(f"=== {os.path.relpath(gds, cfg.ROOT)} ===")
    print(f"  {len(parts)} merged M1/M2 shapes, "
          f"{len(set(uf.find(k) for k in uf.p))} connected component(s)\n")
    bad = 0
    for port in sorted(ports):
        if "[" in port:
            bus, idx = port[:-1].split("[")
            net = hp.bus_bit_net_name(bus, int(idx), resolver)
        else:
            net = hp.port_net_name(port, resolver)
        pins = net_pins.get(net) or net_pins.get(port) or []
        mks = marker.get(port, [])
        if not mks:
            print(f"  FAIL {port:14} no PIN marker")
            bad += 1
            continue
        if not pins:
            print(f"  --   {port:14} no cell pin on net '{net}' (nothing to reach)")
            continue
        pc = {owner(parts, uf, b) for b in mks} - {None}
        cc = {owner(parts, uf, b) for _i, _p, b in pins} - {None}
        if pc & cc:
            print(f"  OK   {port:14} pin -> {len(pins)} cell pin(s) "
                  f"({pins[0][0]}.{pins[0][1]}{'...' if len(pins) > 1 else ''})")
        else:
            print(f"  FAIL {port:14} PIN marker and cell pin(s) "
                  f"{[f'{i}.{p}' for i, p, _ in pins]} are in different components")
            bad += 1
    print()
    if bad:
        print(f"*** {bad} PORT(S) NOT CONNECTED ***")
        return 1
    print(f"ALL {len(ports)} TOP-LEVEL PORTS CONNECTED TO THEIR CELL PINS")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gds", nargs="?", default=cfg.SQUEEZED_GDS)
    a = ap.parse_args()
    sys.exit(main(a.gds))
