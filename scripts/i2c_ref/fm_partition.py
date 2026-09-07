"""
fm_partition.py

Fiduccia-Mattheyses min-cut hypergraph bipartitioning, used to replace the
section-37 "simple split" (naive cumulative-width bisection in netlist file
order) with a locality-aware row split. Goal: minimize cross-row nets (the
root cause of both the oversized shared middle channel, design_notes.md
37.2, and the long-M2-stub short-collision problem, 37.4), while keeping
the two partitions' total cell width balanced (needed for gen_placement_2row
.py's shared-TAP-grid packing to not waste excessive filler).

Hypergraph: nodes = netlist instances (weight = LEF cell width), hyperedges
= nets (an edge connects every instance with a pin on that net; nets that
touch 0 or 1 instance -- primary I/O stubs -- are dropped, they can't be
cut either way).

Algorithm: classic FM. Start from a seed partition (the old naive
cumulative-width split, already width-balanced -- 2338.2 vs 2284.2um, ~1.2%
apart -- so FM only needs to improve locality, not balance). Each pass:
repeatedly pick the unlocked node with the best gain (hyperedge gain
formula: moving v from side F to T removes a cut for any net where v was
the last node on F, and creates a new cut for any net where v is the first
node placed on T) whose move keeps both sides within `balance_tol` of 50%
by weight; lock it; record cumulative gain. At pass end, roll back to the
best-scoring prefix of the move sequence (standard FM local-optimum
escape). Repeat passes until a pass finds no improving prefix.
"""
import sys
from collections import defaultdict
from pathlib import Path

# 2026-09-02: made portable (was a hardcoded Claude-sandbox absolute
# path, broke the first time this chain was run locally on the user's
# own Mac -- see lef_parser.py's LEF_PATH for the same fix).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lef_parser import parse_lef  # noqa: E402
from netlist_parser import parse_netlist  # noqa: E402


def build_hypergraph(instances, widths):
    """-> (node_weight: {name: w}, nets: {net: [names]}) using only nets
    that touch >=2 instances in this instance set (others can't be cut)."""
    node_weight = {}
    net_members = defaultdict(list)
    for typ, name, pins in instances:
        node_weight[name] = widths[typ]
        for pname, net in pins.items():
            net_members[net].append(name)
    nets = {net: names for net, names in net_members.items() if len(set(names)) >= 2}
    # de-dup (a net can touch the same instance twice via two different
    # pins, e.g. both inputs of a gate tied together -- treat as one edge)
    nets = {net: sorted(set(names)) for net, names in nets.items()}
    return node_weight, nets


def cut_size(part, nets):
    cut = 0
    for net, members in nets.items():
        sides = {part[m] for m in members}
        if len(sides) > 1:
            cut += 1
    return cut


def fm_bipartition(instances, widths, seed_part, balance_tol=0.05, max_passes=60, target_ratio=0.5):
    """target_ratio: fraction of total weight side 0 should end up with
    (default 0.5 = even split). Needed by fm_multiway_partition to support
    non-power-of-2 n_rows, e.g. n_rows=3 splits 1 row vs 2 rows, an
    asymmetric 1/3 vs 2/3 weight target, not a 50/50 one."""
    node_weight, nets = build_hypergraph(instances, widths)
    node_nets = defaultdict(list)  # name -> [net, ...]
    for net, members in nets.items():
        for m in members:
            node_nets[m].append(net)

    part = dict(seed_part)
    total_w = sum(node_weight.values())
    target = total_w * target_ratio
    lo, hi = target * (1 - balance_tol), target * (1 + balance_tol)

    def side_weight(side):
        return sum(node_weight[n] for n, p in part.items() if p == side)

    def net_counts(net):
        c = [0, 0]
        for m in nets[net]:
            c[part[m]] += 1
        return c

    best_cut = cut_size(part, nets)
    print(f"nodes={len(node_weight)} nets={len(nets)} initial cut={best_cut} "
          f"(w0={side_weight(0):.1f}, w1={side_weight(1):.1f}, target={target:.1f})")

    for pass_i in range(max_passes):
        locked = set()
        w = [side_weight(0), side_weight(1)]
        moves = []  # (name, gain_at_time)
        cum_gain = 0
        for _ in range(len(node_weight)):
            best_name, best_gain = None, None
            for name in node_weight:
                if name in locked:
                    continue
                side = part[name]
                other = 1 - side
                new_w0 = w[0] - node_weight[name] if side == 0 else w[0] + node_weight[name]
                if not (lo <= new_w0 <= hi):
                    continue
                gain = 0
                for net in node_nets[name]:
                    c = net_counts(net)
                    f_cnt, t_cnt = (c[0], c[1]) if side == 0 else (c[1], c[0])
                    if f_cnt == 1 and t_cnt > 0:
                        gain += 1
                    if t_cnt == 0 and f_cnt > 1:
                        gain -= 1
                if best_gain is None or gain > best_gain:
                    best_gain, best_name = gain, name
            if best_name is None:
                break  # no legal move keeps balance
            name = best_name
            side = part[name]
            other = 1 - side
            part[name] = other
            w[side] -= node_weight[name]
            w[other] += node_weight[name]
            locked.add(name)
            cum_gain += best_gain
            moves.append((name, cum_gain))

        if not moves:
            break
        best_prefix_gain = max(g for _, g in moves)
        if best_prefix_gain <= 0:
            # revert everything this pass, no improvement found -> converged
            for name, _ in moves:
                part[name] = 1 - part[name]
            break
        cut_idx = max(range(len(moves)), key=lambda i: moves[i][1])
        for name, _ in moves[cut_idx + 1:]:
            part[name] = 1 - part[name]
        best_cut = cut_size(part, nets)
        print(f"  pass {pass_i}: applied {cut_idx + 1}/{len(moves)} moves, "
              f"gain={best_prefix_gain}, cut now={best_cut}")

    final_cut = cut_size(part, nets)
    print(f"final cut={final_cut} (w0={side_weight(0):.1f}, w1={side_weight(1):.1f})")
    return part


def fm_multiway_partition(instances, widths, n_rows, balance_tol=0.05):
    """Recursive-bisection multiway partition into n_rows: split all
    instances into a top group / bottom group by fm_bipartition, then
    recurse on each group independently. Returns {name: row_index},
    row_index in [0, n_rows).

    Generalized (this session, for a 3-row/4-channel area comparison) to
    ANY n_rows>=1, not just powers of 2: the top/bottom split is sized
    n_rows//2 vs n_rows-n_rows//2 rows, and fm_bipartition's weight
    target is scaled to that ratio (target_ratio) instead of always 0.5.
    For n_rows a power of 2 this is byte-for-byte the old behavior (every
    split is still even, ratio 0.5 at every level) -- e.g. n_rows=4 still
    splits 2/2 then 1/1 twice, identical to the original code. n_rows=3
    now splits 1 row vs 2 rows (ratio 1/3), then the 2-row side splits
    1/1 as usual.

    Why recursive bisection instead of direct k-way FM: it naturally
    matches the physical structure (each split is a literal top/bottom
    cut, same as the row stack itself), and reuses fm_bipartition exactly
    as-is -- no new algorithm, just applied recursively. The downside is
    it can't undo an early bad cut, but two seed-guided FM passes should
    track the physical top/bottom split well since the seed already
    starts from a width-ordered guess at each level."""
    assert n_rows >= 1, "n_rows must be >= 1"
    if n_rows == 1:
        return {name: 0 for _typ, name, _pins in instances}

    n_top = n_rows // 2
    n_bottom = n_rows - n_top
    target_ratio = n_top / n_rows

    total_w = sum(widths[typ] for typ, _n, _p in instances)
    target = total_w * target_ratio
    seed = {}
    acc = 0.0
    for typ, name, _pins in instances:
        seed[name] = 0 if acc < target else 1
        acc += widths[typ]
    part2 = fm_bipartition(instances, widths, seed, balance_tol=balance_tol, target_ratio=target_ratio)

    group_a = [(t, n, p) for t, n, p in instances if part2[n] == 0]
    group_b = [(t, n, p) for t, n, p in instances if part2[n] == 1]
    part_a = fm_multiway_partition(group_a, widths, n_top, balance_tol)
    part_b = fm_multiway_partition(group_b, widths, n_bottom, balance_tol)

    result = dict(part_a)
    for name, r in part_b.items():
        result[name] = r + n_top
    return result


def classify_multirow_nets(instances, part, n_rows):
    """-> {"row_only": n, "adjacent_pair": n, "spanning": n} net counts,
    for reporting cut quality of a multiway partition. adjacent_pair =
    touches exactly 2 rows that are next to each other; spanning = touches
    3+ rows, or 2 rows that are NOT adjacent (needs pass-through routing)."""
    from collections import defaultdict
    net_rows = defaultdict(set)
    for typ, name, pins in instances:
        for _pname, net in pins.items():
            net_rows[net].add(part[name])
    counts = {"row_only": 0, "adjacent_pair": 0, "spanning": 0}
    for net, rows in net_rows.items():
        if len(rows) <= 1:
            counts["row_only"] += 1
        elif len(rows) == 2 and max(rows) - min(rows) == 1:
            counts["adjacent_pair"] += 1
        else:
            counts["spanning"] += 1
    return counts


if __name__ == "__main__":
    macros = parse_lef()
    net = parse_netlist()
    instances = net["instances"]
    widths = {name: m["size"][0] for name, m in macros.items()}

    # seed: naive cumulative-width split (matches gen_placement_2row.py's
    # split_rows, so we can directly compare before/after)
    total_w = sum(widths[typ] for typ, _n, _p in instances)
    half = total_w / 2.0
    seed = {}
    acc = 0.0
    for typ, name, _pins in instances:
        seed[name] = 0 if acc < half else 1
        acc += widths[typ]

    fm_bipartition(instances, widths, seed)
