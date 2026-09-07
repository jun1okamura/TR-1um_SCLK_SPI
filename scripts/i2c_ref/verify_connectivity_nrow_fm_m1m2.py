"""
verify_connectivity_nrow_fm_m1m2.py

Same layer-aware Union-Find connectivity check as verify_connectivity_
nrow_fm.py, but locate() tries BOTH M1 and M2 for each pin coordinate
(falling back to M2 if no M1 hit) -- needed for STEP6's top-level port
pins, which route_top_pins_nrow_fm.py drops as bare M2PIN markers with
no M1 landing at all (verify_connectivity_nrow_fm.py's locate_m1() alone
reports every one of them as "PIN NOT FOUND ON M1").

Usage:
    python3 verify_connectivity_nrow_fm_m1m2.py <gds> <pin_map.json> <scan_y_lo> <scan_y_hi> [<scan_w>]
"""
import json
import sys
import klayout.db as db

M1_LAYER = (13, 0)
V1_LAYER = (19, 0)
M2_LAYER = (20, 0)
TOP_CELL = "i2c_slave_async_nrow_fm"


def main():
    gds = sys.argv[1]
    pin_map_json = sys.argv[2]
    scan_y_lo = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    scan_y_hi = float(sys.argv[4]) if len(sys.argv) > 4 else 900.0
    scan_w = float(sys.argv[5]) if len(sys.argv) > 5 else 1500.0

    pin_map = json.load(open(pin_map_json))

    layout = db.Layout()
    layout.read(gds)
    dbu = layout.dbu
    top = layout.cell(TOP_CELL)
    m1_idx = layout.layer(*M1_LAYER)
    v1_idx = layout.layer(*V1_LAYER)
    m2_idx = layout.layer(*M2_LAYER)

    scan = db.Box(0, int(round(scan_y_lo / dbu)), int(round(scan_w / dbu)), int(round(scan_y_hi / dbu)))

    m1_region = db.Region(top.begin_shapes_rec_touching(m1_idx, scan)).merged()
    m2_region = db.Region(top.begin_shapes_rec_touching(m2_idx, scan)).merged()
    v1_region = db.Region(top.begin_shapes_rec_touching(v1_idx, scan)).merged()

    m1_polys = list(m1_region.each())
    m2_polys = list(m2_region.each())
    print(f"{len(m1_polys)} M1 components, {len(m2_polys)} M2 components, {v1_region.count()} V1 vias (in scan area)")

    parent = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for via in v1_region.each():
        vbox = via.bbox()
        vreg = db.Region(via)
        m1_hits = [i for i, p in enumerate(m1_polys) if p.bbox().overlaps(vbox) or p.bbox().touches(vbox)]
        m1_hits = [i for i in m1_hits if vreg.interacting(db.Region(m1_polys[i])).count() > 0]
        m2_hits = [i for i, p in enumerate(m2_polys) if p.bbox().overlaps(vbox) or p.bbox().touches(vbox)]
        m2_hits = [i for i in m2_hits if vreg.interacting(db.Region(m2_polys[i])).count() > 0]
        for i in m1_hits:
            for j in m2_hits:
                union(("M1", i), ("M2", j))
        for i in m1_hits[1:]:
            union(("M1", m1_hits[0]), ("M1", i))
        for j in m2_hits[1:]:
            union(("M2", m2_hits[0]), ("M2", j))

    def locate(layer_name, polys, x_um, y_um):
        xi, yi = int(round(x_um / dbu)), int(round(y_um / dbu))
        probe = db.Region(db.Box(xi - 2, yi - 2, xi + 2, yi + 2))
        for i, p in enumerate(polys):
            if p.bbox().left - 5 <= xi <= p.bbox().right + 5 and p.bbox().bottom - 5 <= yi <= p.bbox().top + 5:
                if probe.interacting(db.Region(p)).count() > 0:
                    return (layer_name, i)
        return None

    problems = []
    root_to_net = {}
    for net, pins in pin_map.items():
        roots_seen = set()
        for instname, pinname, vx, vy in pins:
            loc = locate("M1", m1_polys, vx, vy)
            if loc is None:
                loc = locate("M2", m2_polys, vx, vy)
            if loc is None:
                problems.append(f"PIN NOT FOUND ON M1 OR M2: net={net} {instname}.{pinname} at ({vx:.2f},{vy:.2f})")
                continue
            root = find(loc)
            roots_seen.add(root)
            if root in root_to_net and root_to_net[root] != net:
                problems.append(f"SHORT SUSPECTED: net={net} pin {instname}.{pinname} "
                                 f"shares a component with net={root_to_net[root]}")
            root_to_net[root] = net
        if len(roots_seen) > 1:
            problems.append(f"NET SPLIT (not fully connected): net={net} resolves to {len(roots_seen)} "
                             f"separate components: {roots_seen}")

    print(f"\nChecked {len(pin_map)} nets, {sum(len(v) for v in pin_map.values())} pins total.")
    if problems:
        print(f"\n{len(problems)} PROBLEM(S) FOUND:")
        for p in problems:
            print(" -", p)
    else:
        print("\nALL NETS FULLY CONNECTED, NO SHORTS DETECTED. Connectivity verification PASSED.")


if __name__ == "__main__":
    main()
