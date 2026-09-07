"""detect_loops_jogs.py -- objective audit of net_shapes_nrow_fm_*.json for
(1) closed loops (redundant wiring: more edges than a spanning tree needs)
and (2) short M1 "jog" bridges (via-M1-via detours, as opposed to real
multi-pin trunks), per net.

Loop detection: treat each shape (M1/M2 box) as a graph EDGE between its
two long-axis endpoints (for a mostly-horizontal box: left-mid/right-mid;
for a mostly-vertical box: bottom-mid/top-mid), snapped to a coordinate
grid so coincident endpoints from different shapes merge into one node.
A connected net's shapes should form a TREE (edges = nodes - 1). Any
extra edge beyond that is, by definition, a redundant closed loop.

Jog detection: an M1 run whose length is short (<jog_max_len) is a
candidate "jog" bridge (as opposed to a real trunk, which either spans
wide -- connects 2+ pins directly -- or has 3+ shapes touching it).
Flags short M1 runs that touch exactly 2 other shapes (both M2, i.e. a
pure via-M1-via detour) as jogs.

Usage: python3 detect_loops_jogs.py <net_shapes.json> [jog_max_len]
"""
import json
import sys
from collections import defaultdict

GRID = 0.1  # um, snap tolerance for merging coincident endpoints


def snap(v):
    return round(v / GRID) * GRID


def endpoints(shape):
    _lyr, x0, y0, x1, y1 = shape
    w, h = x1 - x0, y1 - y0
    if w >= h:  # horizontal-ish
        return (snap((x0 + x1) / 2 - w / 2), snap((y0 + y1) / 2)), \
               (snap((x0 + x1) / 2 + w / 2), snap((y0 + y1) / 2))
    else:  # vertical-ish
        return (snap((x0 + x1) / 2), snap((y0 + y1) / 2 - h / 2)), \
               (snap((x0 + x1) / 2), snap((y0 + y1) / 2 + h / 2))


def find(parent, k):
    parent.setdefault(k, k)
    while parent[k] != k:
        parent[k] = parent[parent[k]]
        k = parent[k]
    return k


def union(parent, a, b):
    ra, rb = find(parent, a), find(parent, b)
    if ra != rb:
        parent[ra] = rb
        return True  # merged two components
    return False  # a and b were ALREADY connected -> this edge closes a loop


def analyze(net_shapes, jog_max_len=60.0):
    loop_nets = {}
    jog_nets = defaultdict(list)
    for net, shapes in net_shapes.items():
        parent = {}
        extra_edges = []  # edges that closed a loop
        for shp in shapes:
            a, b = endpoints(shp)
            if a == b:
                continue  # degenerate (via pad or zero-length), not a real edge
            if not union(parent, a, b):
                extra_edges.append(shp)
        if extra_edges:
            loop_nets[net] = extra_edges

        # jog bridges: short M1 runs
        # count how many OTHER shapes of this net touch each endpoint
        touch_count = defaultdict(int)
        for shp in shapes:
            a, b = endpoints(shp)
            touch_count[a] += 1
            touch_count[b] += 1
        for shp in shapes:
            lyr, x0, y0, x1, y1 = shp
            if lyr != "M1":
                continue
            length = max(x1 - x0, y1 - y0)
            if length > jog_max_len:
                continue
            a, b = endpoints(shp)
            # a pure jog: both ends touch exactly this one M1 shape + one
            # other (M2) shape each -- i.e. touch_count==2 at both ends,
            # and it's not a multi-pin trunk (a trunk's ends usually have
            # touch_count>=2 too, but a trunk is typically much longer or
            # has intermediate taps; short+isolated is the jog signature)
            if touch_count[a] <= 2 and touch_count[b] <= 2:
                jog_nets[net].append(shp)
    return loop_nets, jog_nets


def main():
    path = sys.argv[1]
    jog_max_len = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    net_shapes = json.load(open(path))
    loop_nets, jog_nets = analyze(net_shapes, jog_max_len)

    print(f"=== {path} ===")
    print(f"{len(net_shapes)} nets total")
    print(f"\n{len(loop_nets)} net(s) with a CLOSED LOOP (redundant wiring):")
    for net, edges in sorted(loop_nets.items()):
        print(f"  {net}: {len(edges)} extra (loop-closing) edge(s)")
        for e in edges:
            print(f"      {e}")

    print(f"\n{len(jog_nets)} net(s) with candidate short M1 jog bridge(s) "
          f"(<= {jog_max_len}um, isolated):")
    total_jogs = sum(len(v) for v in jog_nets.values())
    print(f"  total candidate jogs: {total_jogs}")
    for net, jogs in sorted(jog_nets.items(), key=lambda kv: -len(kv[1]))[:20]:
        print(f"  {net}: {len(jogs)}")


if __name__ == "__main__":
    main()


def analyze_overlap(net_shapes, dbu=0.001):
    """Per-net self-overlap check via klayout Region: does this net draw
    OVERLAPPING copper with itself (sum of individual box areas > merged
    union area)? A clean tree-shaped net never needs to; any positive
    excess is literally redundant/wasted metal -- catches the
    partial-containment case a pure endpoint-graph misses (e.g. two M2
    runs on the same X whose Y-ranges partially overlap, which a
    same-net short never flags on DRC but is still wasted routing)."""
    import klayout.db as db
    results = {}
    for net, shapes in net_shapes.items():
        boxes_um = [(x0, y0, x1, y1) for _lyr, x0, y0, x1, y1 in shapes]
        if len(boxes_um) < 2:
            continue
        # per-layer, since M1 and M2 overlapping is normal (different layers)
        by_layer = defaultdict(list)
        for lyr, x0, y0, x1, y1 in shapes:
            by_layer[lyr].append(db.Box(round(x0 / dbu), round(y0 / dbu),
                                         round(x1 / dbu), round(y1 / dbu)))
        for lyr, boxes in by_layer.items():
            indiv_area = sum(b.area() for b in boxes)
            merged_area = db.Region(boxes).merged().area()
            excess = indiv_area - merged_area
            if excess > 0:
                results.setdefault(net, {})[lyr] = excess * (dbu ** 2)  # um^2
    return results


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[-1] == "--overlap":
    path = sys.argv[1]
    net_shapes = json.load(open(path))
    overlaps = analyze_overlap(net_shapes)
    print(f"\n{len(overlaps)} net(s) with SELF-OVERLAPPING copper (redundant/wasted metal):")
    for net, per_layer in sorted(overlaps.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(per_layer.values())
        print(f"  {net}: {total:.1f} um^2 excess ({per_layer})")
