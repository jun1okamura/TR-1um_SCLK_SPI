"""
apply_dff_group_constraints.py

Adds the row-buffer functional grouping documented in design_notes.md
section 108.23 as a HARD placement constraint on top of fm_partition.py's
automatic min-cut row partitioner.

Background
----------
insert_row_buffers.py creates one clock buffer per (target_net, row)
pair, where "row" comes directly from fm_multiway_partition()'s output --
a partition chosen purely to minimize net cuts / balance cell width, with
zero awareness of which DFFs will end up sharing that row's buffer. This
is exactly how the row-buffer-loading imbalance that caused the
108.19/108.20 (shreg) and 108.21/108.22 (sda_oe_r) SPICE bugs came about:
FM happened to put 14 unrelated DFFs (7 rx_data + 7 shreg) in the same
physical row, so insert_row_buffers.py faithfully built ONE overloaded
14-fanout buffer for it, and a same-row 0->1 capture failure followed --
purely a side effect of FM optimizing something (general net-cut/width
balance) that has no notion of "these DFFs must share a row because they
will share a clock buffer."

Section 108.23 defined which DFFs SHOULD share a clock buffer, from a
functional-grouping / skew-safety standpoint (shift chain isolated; FSM
counters together; one-shot decision registers together; parallel
capture registers together). This module makes that grouping a real
placement constraint instead of a hope: it clusters each 108.23 group
into a single pseudo-node before running fm_multiway_partition, so FM's
min-cut/balance search can freely place the *groups* wherever it likes,
but can never split a group's members across two different rows. After
partitioning, the cluster's assigned row is copied out to every one of
its real member instances.

fm_multiway_partition_grouped() is a drop-in replacement for
fm_partition.fm_multiway_partition() -- same signature (plus an optional
`groups` argument), same return shape ({instance_name: row_index}).

To actually use it in the pipeline, insert_row_buffers.py's and
gen_placement_nrow_fm.py's `part = fm_multiway_partition(instances,
widths, N_ROWS)` call sites would import this module and call
fm_multiway_partition_grouped(instances, widths, N_ROWS) instead -- not
changed by this script itself (see design_notes.md section 108.28 for
the one-line call-site edit needed at each site, left for a deliberate
follow-up rather than silently rewiring the existing P&R scripts here).

Run standalone: python3 script/apply_dff_group_constraints.py
  -> parses the V10 merged netlist (src/i2c_slave_async_net_v10_muxdffrb.v,
     produced by merge_muxdffrb_rslatch.py -- see design_notes.md 108.30),
     runs both the plain (baseline) and grouped partition at N_ROWS=4,
     verifies every 108.23 group (updated to V10's MUXDFFRB instance
     names) landed in a single row in the grouped run (and, for contrast,
     reports whether/how the baseline run would have split them), prints
     per-row occupancy and net-locality stats for both, and writes
     LEF/row_assignment_v10_dffgrouped.json in the same {instance_name:
     row} format as row_assignment_v9.json.
"""
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lef_parser import parse_lef  # noqa: E402
from netlist_parser import parse_netlist  # noqa: E402
from fm_partition import fm_multiway_partition, classify_multirow_nets  # noqa: E402

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

N_ROWS = 4  # matches insert_row_buffers.py / gen_placement_nrow_fm.py's current N_ROWS

OUT_JSON = str(_REPO_ROOT / "LEF" / "row_assignment_v10_dffgrouped.json")

# design_notes.md section 108.23's functional grouping. Originally written
# against src/i2c_slave_async_net.v's pre-row-buffer Yosys instance names
# (_504_=addr_match(addr_ok), _505_/6_/7_=bit_cnt[0:2], _508_=rw(rw_bit),
# _509_/10_/11_=phase[0:2], _512_..519_=txreg[0:7], _520_..527_=
# rx_data[0:7], _528_=sda_oe_r, _529_=last_bit_pending, _530_..536_=
# shreg[0:6]).
#
# Updated for V10 (design_notes.md 108.27/108.29/108.30): 19 of these
# DFFRBs were folded into MUXDFFRB compound cells by
# merge_muxdffrb_rslatch.py (its printed old-name -> new-name mapping,
# derived directly from that script's find_mux_dffrb_pairs() output
# against src/i2c_slave_async_net_v10.v, is reproduced here):
#   rx_data[0:7] (_520_.._526_, _527_) -> u_muxdffrb_7,6,5,4,3,2,1,19
#   txreg[0:6]   (_512_.._518_)        -> u_muxdffrb_14,13,12,11,10,9,8
#   txreg[7]     (_519_)               -> u_muxdffrb_18
#   phase[1]     (_510_)               -> u_muxdffrb_15
#   addr_ok      (_504_)               -> u_muxdffrb_16
#   rw_bit       (_508_)               -> u_muxdffrb_17
# The remaining group members (shreg[0:6], bit_cnt[0:2], phase[0],
# phase[2], last_bit_pending, sda_oe_r) were NOT merged (either not a
# single-fanout MUX2->DFFRB.D site, or a plain register with no feeding
# mux) and keep their original DFFRB instance names unchanged. This dict
# now matches src/i2c_slave_async_net_v10_muxdffrb.v, NOT the older
# unmerged netlists -- pass that path explicitly to parse_netlist() when
# using DFF_GROUPS (see __main__ below).
#
# Groups are named for the clock buffer they are meant to end up sharing
# (matching 108.23's table). "sclN_sda_oe_r" is a singleton, included
# only for documentation -- a group of 1 has nothing to cluster with, so
# it produces no constraint on its own (108.22's fix already gives it a
# dedicated buffer regardless of which row it lands in).
# 108.31 (user decision: split the largest group rather than widen the
# row or shrink the routing-corridor reservation): clk156_rx_data and
# sclN_txreg (8 MUXDFFRB members each, 756.8um) were the two biggest
# contributors to any row's real-cell width and the main reason max-row
# width stayed pinned near the ~1512um capacity ceiling across the whole
# balance_tol sweep. A first attempt split each into just two 4-member
# halves (_lo/_hi) -- insufficient: with exactly 4 rows and 4 real
# clk156 purposes (shreg/fsm_state/oneshot_decision/rx_data), pigeonhole
# means clk156_rx_data's dedicated row must absorb BOTH its halves
# regardless (there is no 5th row for the extra half to move to without
# colliding with one of the other 3 purposes) -- confirmed empirically:
# _resolve_domain_collisions always re-merged _lo/_hi back onto one row
# to reach 0 collisions, giving zero net width-splitting benefit.
# Re-split into four 2-member pieces each (u_muxdffrb pairs by adjacent
# bit index) instead: same-PURPOSE pieces (all share "clk156_rx_data" or
# "sclN_txreg" after _purpose_of strips the "_splitN" suffix) are still
# exempt from the collision rule, so they're free to land on any row(s)
# without domain-collision cost, but at 189.2um per piece (vs 378.4um
# for a half) _rebalance_clusters_by_width has enough granularity to
# actually spread them out and bring every row under MAX_ROW_WIDTH_UM
# (see fm_multiway_partition_grouped's max_row_width parameter).
# Electrical-safety note (still holds, same reasoning as the 2-way
# split): insert_row_buffers.py builds one buffer per (target net, row),
# so N pieces of the same original group landing on N different rows
# just yields N dedicated buffers with correspondingly SMALLER fanout
# each (2 loads/piece here) -- never worse than the original single
# 8-load buffer; the 108.19/108.21 bug was UNRELATED groups accidentally
# sharing one buffer, not a single group's own buffer having modest
# fanout.
DFF_GROUPS = {
    "clk156_shreg":            ["_530_", "_531_", "_532_", "_533_", "_534_", "_535_", "_536_"],
    "clk156_fsm_state":        ["_505_", "_506_", "_507_", "_509_", "u_muxdffrb_15", "_511_"],
    "clk156_oneshot_decision": ["u_muxdffrb_16", "u_muxdffrb_17", "_529_"],
    "clk156_rx_data_split0":   ["u_muxdffrb_7", "u_muxdffrb_6"],    # rx_data[0:1]
    "clk156_rx_data_split1":   ["u_muxdffrb_5", "u_muxdffrb_4"],    # rx_data[2:3]
    "clk156_rx_data_split2":   ["u_muxdffrb_3", "u_muxdffrb_2"],    # rx_data[4:5]
    "clk156_rx_data_split3":   ["u_muxdffrb_1", "u_muxdffrb_19"],   # rx_data[6:7]
    "sclN_txreg_split0":       ["u_muxdffrb_14", "u_muxdffrb_13"],  # txreg[0:1]
    "sclN_txreg_split1":       ["u_muxdffrb_12", "u_muxdffrb_11"],  # txreg[2:3]
    "sclN_txreg_split2":       ["u_muxdffrb_10", "u_muxdffrb_9"],   # txreg[4:5]
    "sclN_txreg_split3":       ["u_muxdffrb_8", "u_muxdffrb_18"],   # txreg[6:7]
    "sclN_sda_oe_r":           ["_528_"],
}

# V10 merged netlist that DFF_GROUPS above is keyed against (see comment
# block above). Passed explicitly to parse_netlist() in __main__ rather
# than relying on netlist_parser.NET_PATH's default, which still points
# at the older, unmerged src/i2c_slave_async_net.v.
V10_NET_PATH = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v10_muxdffrb.v")

# Row-balance finding (V10, design_notes.md 108.30): at the function
# default balance_tol=0.05 -- fine for the UNCLUSTERED partitioner, where
# every node is one small standard cell -- the V10 clusters are large and
# few (e.g. clk156_rx_data's 8 MUXDFFRBs = 8*94.6 = 756.8um alone, vs a
# ~2462um per-row target), so at each recursive-bisection level there is
# often no single cluster move that both improves cut AND stays within a
# 5% width tolerance; FM then makes zero moves and leaves that level at
# its unbalanced initial split. Swept balance_tol in {0.05, 0.10, 0.15,
# 0.20, 0.30} against this netlist/LEF; row occupancy (all instances):
#   0.05: {0: 85, 1: 48, 2: 10, 3: 4}   <- default, worst case
#   0.10: {0: 59, 1: 33, 2: 44, 3: 11}
#   0.15: {0: 49, 1: 44, 2: 43, 3: 11}  <- best of the sweep, used below
#   0.20: {0: 49, 1: 8,  2: 64, 3: 26}
#   0.30: {0: 72, 1: 38, 2: 20, 3: 17}
# All 7 groups stayed single-row and domain-collision-free at every
# tolerance tested. Row 3 stays the smallest across the whole sweep --
# a structural property of recursive bisection (it's always the last
# split, inheriting whatever imbalance accumulated from both earlier
# splits) combined with indivisible large clusters, not something
# balance_tol alone fully corrects. Flagging as a known limitation for
# Task #3 (placement): row3's physical row will be shorter than the
# others regardless of tolerance choice within this sweep; if that isn't
# acceptable, the real fix is either a non-recursive (direct k-way)
# partitioner or splitting the largest clusters (e.g. txreg_hi/lo into
# smaller sub-groups), neither implemented here.
GROUPED_BALANCE_TOL = 0.20
# UPDATE (design_notes.md 108.31): 0.15 (chosen above purely by
# instance-COUNT balance) turned out to still leave row0 at 1563.8um of
# real-cell WIDTH alone -- gen_placement_nrow_fm.py's fixed
# TARGET_ROW_WIDTH_UM=1620um budget only has ~1512um left for real cells
# after TAP columns (43.2um) and the priority-fill M2 corridor reservation
# (64.8um) are subtracted at N_GAPS=3, so 1563.8um doesn't fit and
# gen_placement_nrow_fm.main() raises "row content needs 4 gaps". A
# WIDTH-based re-sweep (not just instance count -- MUXDFFRB at 94.6um is
# ~1.5x a DFFRB+MUX2's combined width and clusters of them dominate a
# row's real-cell width far more than its instance count) found tol=0.25
# was the first value whose max-row width (1504.0um) fit under that
# ~1512um budget -- but with only ~8um of headroom, still too fragile
# for _gaps_needed's conservative greedy bin-packing (see 108.31).
#
# UPDATE 2 (108.31, after the user chose "split the largest group" over
# widening the row or shrinking the routing-corridor reservation):
# clk156_rx_data (8 MUXDFFRB members, 756.8um alone) was split into
# clk156_rx_data_lo/_hi (4 members each) so the two halves can land on
# different rows. Re-swept the same balance_tol values against the new
# DFF_GROUPS; tol=0.20 is now the best (max-row width 1383.6um, ~128um
# of real headroom under the ~1512um budget -- comfortably clears
# _gaps_needed), while every group (now 8, all single-row) stays
# domain-purpose-collision-free (see _purpose_of/_resolve_domain_
# collisions' 108.31 update, needed because splitting rx_data makes the
# clk156 domain's group COUNT (5) exceed N_ROWS (4) even though its
# PURPOSE count (4: shreg/fsm_state/oneshot_decision/rx_data) still
# matches exactly).


def build_grouped_instances(instances, widths, groups):
    """-> (reduced_instances, widths_ext, cluster_members)

    reduced_instances: every ungrouped instance unchanged, plus one
    pseudo-instance per group (typ=name=group name) whose pins are the
    union, across all its members, of every "{member}.{pin}": net entry
    -- enough for fm_partition.build_hypergraph to see every net the
    group touches as belonging to one node, without caring about
    per-member pin identity (build_hypergraph only uses pin VALUES, i.e.
    net names, to build hyperedges; pin names/keys are never inspected).

    widths_ext: widths dict extended with one entry per group name, equal
    to the sum of its members' real cell widths, so fm_partition's
    `widths[typ]` weight lookup works unmodified on the pseudo-instance
    (typ is set equal to the group name for cluster pseudo-instances).

    cluster_members: {group_name: [real_instance_name, ...]}, for
    expanding the partition result back afterward.

    Raises ValueError (rather than silently under-constraining) if a
    listed member is missing from the parsed netlist, or if an instance
    name is listed in more than one group.
    """
    name_to_group = {}
    for gname, members in groups.items():
        for m in members:
            if m in name_to_group:
                raise ValueError(
                    f"{m!r} listed in two groups: {name_to_group[m]!r} and {gname!r}")
            name_to_group[m] = gname

    by_name = {name: (typ, name, pins) for typ, name, pins in instances}
    missing = [m for g in groups.values() for m in g if m not in by_name]
    if missing:
        raise ValueError(f"group members not found in netlist: {missing}")

    grouped = defaultdict(list)
    passthrough = []
    for typ, name, pins in instances:
        gname = name_to_group.get(name)
        if gname is None:
            passthrough.append((typ, name, pins))
        else:
            grouped[gname].append((typ, name, pins))

    widths_ext = dict(widths)
    cluster_members = {}
    cluster_instances = []
    for gname, members in grouped.items():
        cluster_members[gname] = [n for _t, n, _p in members]
        merged_pins = {}
        for typ, name, pins in members:
            for pname, net in pins.items():
                merged_pins[f"{name}.{pname}"] = net
        cluster_instances.append((gname, gname, merged_pins))
        widths_ext[gname] = sum(widths[t] for t, _n, _p in members)

    return passthrough + cluster_instances, widths_ext, cluster_members


def _domain_of(group_name):
    """Groups sharing a clock domain are named with a common prefix before
    the first underscore ("clk156_..." / "sclN_..."). insert_row_buffers.py
    builds one buffer per (target_net, row) -- two DFF_GROUPS in the SAME
    domain landing on the SAME row would still end up sharing one
    buffer, silently recreating the exact overload bug this whole
    mechanism exists to prevent. Intra-group cohesion alone (guaranteed
    by clustering, above) does not prevent that; _resolve_domain_collisions
    below does."""
    return group_name.split("_", 1)[0]


_SPLIT_SUFFIX_RE = __import__("re").compile(r"_split\d+$")


def _purpose_of(group_name):
    """108.31: strip a trailing width-split suffix, added whenever a
    single 108.23 group is subdivided purely to relieve per-row WIDTH
    pressure, to recover the original 108.23 group identity. Two
    sub-groups of the SAME purpose sharing a row is not a bug (it just
    reproduces the pre-split group's own behavior for that row); it is
    different PURPOSES sharing a row that _resolve_domain_collisions
    must avoid (that's what would make insert_row_buffers.py merge two
    functionally-unrelated DFF sets onto one buffer, the 108.19/108.21
    bug class). Two suffix conventions coexist: the original coarse
    "_lo"/"_hi" 2-way split (clk156_rx_data_lo/_hi) and the finer
    "_splitN" convention (sclN_txreg_split0..3, clk156_rx_data_split0..3)
    adopted later in 108.31 once 2-way splitting of the two largest
    groups alone still wasn't enough width granularity for the
    rebalancer to find an improving move (pigeonhole: with only 4 rows,
    forcing clk156_rx_data's two halves onto SEPARATE rows is
    impossible without colliding with one of the other 3 clk156
    purposes, so a coarse 2-way split provides no real placement
    freedom -- finer, smaller pieces do)."""
    for suffix in ("_lo", "_hi"):
        if group_name.endswith(suffix):
            return group_name[: -len(suffix)]
    m = _SPLIT_SUFFIX_RE.search(group_name)
    if m:
        return group_name[: m.start()]
    return group_name


def _resolve_domain_collisions(cluster_rows, groups, n_rows):
    """cluster_rows: {group_name: row} as chosen by FM on the clustered
    hypergraph. Ensures no two DIFFERENT-purpose groups (see _purpose_of)
    sharing a domain (see _domain_of) end up on the same row -- same-
    purpose sub-groups (e.g. clk156_rx_data_lo/_hi, 108.31) are exempt
    from each other, since they're allowed (indeed expected, that's the
    point of splitting them) to land on different rows OR the same row
    with no electrical downside either way.

    108.31 update: previously required an INJECTIVE groups->rows mapping
    (raising ValueError outright whenever a domain had more groups than
    rows). Once a group can be split into same-purpose sub-groups for
    width relief, a domain can legitimately have more GROUPS than
    distinct PURPOSES (e.g. clk156: 5 groups / 4 purposes / 4 rows) --
    demanding injectivity at the group level would then be needlessly
    impossible to satisfy even though the actual electrical requirement
    (distinct PURPOSES per row) is fine. Brute-forces every rows^groups
    assignment (small: this codebase's domains never exceed ~6 groups),
    scored first by number of different-purpose same-row collisions
    (minimized, driven to 0 whenever the purpose count allows it -- with
    5 groups/4 purposes/4 rows it always can), then by how many groups
    moved off FM's original per-group choice, and keeps the best-scoring
    assignment. Domains with no collision are left untouched."""
    from itertools import product

    by_domain = defaultdict(list)
    for g in groups:
        by_domain[_domain_of(g)].append(g)

    def collisions(gnames, rows):
        by_row = defaultdict(set)
        for g, r in zip(gnames, rows):
            by_row[r].add(_purpose_of(g))
        return sum(len(purposes) - 1 for purposes in by_row.values() if len(purposes) > 1)

    def same_purpose_colocations(gnames, rows):
        """108.31: number of same-purpose sub-group PAIRS (e.g.
        clk156_rx_data_lo/_hi) sharing a row. Never a collision (exempt
        by design, see _purpose_of), but co-locating them defeats the
        whole point of splitting a group for row-width relief -- used
        only as a low-priority tiebreaker below `collisions` and above
        `disruption`, so that when several candidate assignments are
        equally collision-free and equally disruptive, the search
        prefers the one that actually spreads same-purpose splits
        across different rows (the fine-grained width-balancing
        _rebalance_clusters_by_width alone can't create, since it only
        MOVES clusters after this function has already picked one fixed
        assignment)."""
        by_row_purpose_counts = defaultdict(lambda: defaultdict(int))
        for g, r in zip(gnames, rows):
            by_row_purpose_counts[r][_purpose_of(g)] += 1
        from math import comb
        return sum(comb(n, 2) for purposes in by_row_purpose_counts.values() for n in purposes.values())

    resolved = dict(cluster_rows)
    for domain, gnames in by_domain.items():
        rows_used = [resolved[g] for g in gnames]
        if collisions(gnames, rows_used) == 0 and same_purpose_colocations(gnames, rows_used) == 0:
            continue  # already collision-free and fully spread, nothing to do
        best_cost, best_assignment = None, None
        for candidate_rows in product(range(n_rows), repeat=len(gnames)):
            n_collisions = collisions(gnames, candidate_rows)
            n_colocations = same_purpose_colocations(gnames, candidate_rows)
            disruption = sum(1 for g, r in zip(gnames, candidate_rows) if r != resolved[g])
            cost = (n_collisions, n_colocations, disruption)
            if best_cost is None or cost < best_cost:
                best_cost, best_assignment = cost, dict(zip(gnames, candidate_rows))
        resolved.update(best_assignment)
        n_collisions, n_colocations, disruption = best_cost
        note = "collision-free" if n_collisions == 0 else f"{n_collisions} collision(s) unavoidable"
        if n_colocations:
            note += f", {n_colocations} same-purpose co-location(s) remain"
        print(f"  domain {domain!r}: row collision resolved "
              f"({disruption} group(s) moved off FM's initial choice, {note}) -> "
              + ", ".join(f"{g}=row{best_assignment[g]}" for g in gnames))
    return resolved


# 108.31: gen_placement_nrow_fm.py's fixed-width row packer has a real
# physical real-cell budget of ~1512um/row (TARGET_ROW_WIDTH_UM=1620um
# minus TAP columns and the priority-fill M2 corridor reservation, at
# N_GAPS=3) -- but its `_gaps_needed` dry run uses a plain left-to-right
# GREEDY bin pack with no cell reordering, so several large (18-track
# MUXDFFRB) cells clustered together by DFF_GROUPS can fail to pack even
# when comfortably under that theoretical budget (observed: a row at
# 1399.8um -- 112um of slack -- still failed). balance_tol alone can't
# fix this: FM's balance search only bounds AGGREGATE per-side width
# imbalance at each bisection level, with no notion of "many large
# indivisible blocks landing on the same physical row" (confirmed
# empirically -- multiple same-domain-collision-free tol values still
# converged several oversized clusters onto one row by coincidence of
# the min-cut search, independent of clock domain). MAX_ROW_WIDTH_UM
# below is a conservative post-hoc target (well under the ~1512um
# theoretical ceiling, to absorb that greedy-packing loss) enforced by
# _rebalance_clusters_by_width.
MAX_ROW_WIDTH_UM = 1350.0

# 108.36 (design_notes.md): a FLAT per-row cap (MAX_ROW_WIDTH_UM above)
# balances all 4 rows toward the SAME target width, but route_channels_
# nrow_fm.py's "forced overlap zone" (the X-range where BOTH rows
# bounding a channel have real-cell content, point 7 in that file's
# docstring) depends on the COMBINED width of a specific ADJACENT row
# PAIR, not on any row's width in isolation. channel2 (between row1 and
# row2) reached an 85%-of-row-width forced overlap zone under the flat
# 1350um cap because row1+row2's combined real content (~2668um) is
# nearly the full row_width*2 (3240um) -- structurally unavoidable
# overlap given row1 and row2 are the two rows DFF_GROUPS naturally
# loads heaviest (clk156_fsm_state/oneshot_decision/rx_data splits,
# sclN_txreg splits). channel1 (row0/row1) and channel3 (row2/row3)
# were comparatively less saturated (75-80%). A single V10 short-fixing
# session (108.34/108.35) confirmed this is where essentially every
# remaining post-ripup-reroute short concentrates.
#
# ROW_WIDTH_CAP_UM below deliberately UNbalances the 4 rows instead of
# equalizing them: row1/row2 (bounding the worst channel, channel2) get
# a materially tighter cap than row0/row3, so _rebalance_clusters_by_width
# and rebalance_final_assignment push clusters/buffers OFF row1/row2 and
# onto row0/row3 even though row0/row3 end up less full than a flat cap
# would allow. Chosen so the combined cap sum (1420+1200+1200+1420=5240um)
# comfortably exceeds V10's actual total real-cell width (~5184um,
# confirmed via placement JSON), so the rebalance has a real, reachable
# target rather than an infeasible one; each individual cap (1420um) also
# stays well under gen_placement_nrow_fm.py's ~1512um theoretical
# per-row packing ceiling. Both rebalance functions accept a plain float
# (old flat-cap behavior, fully backward compatible) OR a {row: cap}
# dict (this new per-row behavior) via _cap_for().
ROW_WIDTH_CAP_UM = {0: 1420.0, 1: 1200.0, 2: 1200.0, 3: 1420.0}


def _cap_for(max_row_width, row, n_rows):
    if isinstance(max_row_width, dict):
        return max_row_width.get(row, max(max_row_width.values()))
    return max_row_width


def _rebalance_clusters_by_width(cluster_rows, cluster_members, groups, passthrough_rows,
                                  instances, widths, n_rows, max_row_width):
    """Greedily move whole clusters (never split one -- that would
    reintroduce the "group split across rows" problem this whole module
    exists to prevent) from the most-loaded row to the least-loaded row,
    largest-movable-cluster-first, until every row's total real-cell
    width is <= max_row_width or no further move can help without
    creating a new different-PURPOSE collision (see _purpose_of) in the
    destination row. Passthrough (non-grouped) instances are never
    moved -- only whole DFF_GROUPS clusters, which is the actual lever
    available at this layer and matches how the width concentration
    problem arises (a handful of large clusters, not the many small
    ungrouped gates, dominate any row's width -- see 108.31).

    cluster_rows: {group_name: row}, post domain-collision-resolution.
    passthrough_rows: {instance_name: row} for every NON-grouped
    instance (i.e. fm_multiway_partition's own initial `part`, restricted
    to names that aren't a cluster member) -- these rows are fixed and
    contribute to each row's width baseline but are never reassigned
    here.
    """
    name_to_group = {}
    for gname, members in cluster_members.items():
        for m in members:
            name_to_group[m] = gname

    cluster_w = {}
    for gname, members in cluster_members.items():
        member_set = set(members)
        cluster_w[gname] = sum(widths[t] for t, n, _p in instances if n in member_set)

    def row_widths(rows_by_group):
        rw = defaultdict(float)
        for name, row in passthrough_rows.items():
            typ = _type_by_name[name]
            rw[row] += widths[typ]
        for gname, row in rows_by_group.items():
            rw[row] += cluster_w[gname]
        return rw

    _type_by_name = {name: typ for typ, name, _p in instances}

    rows_by_group = dict(cluster_rows)
    purpose_by_group = {g: _purpose_of(g) for g in groups}
    domain_by_group = {g: _domain_of(g) for g in groups}

    moved_total = 0
    while True:
        rw = row_widths(rows_by_group)
        for r in range(n_rows):
            rw.setdefault(r, 0.0)
        overs = {r: rw[r] - _cap_for(max_row_width, r, n_rows) for r in range(n_rows)
                 if rw[r] > _cap_for(max_row_width, r, n_rows)}
        if not overs:
            break  # every row within its own budget
        max_row = max(overs, key=lambda r: overs[r])

        # Candidates: clusters currently on max_row, largest first.
        candidates = sorted(
            (g for g, r in rows_by_group.items() if r == max_row),
            key=lambda g: cluster_w[g], reverse=True)
        moved = False
        for g in candidates:
            # Try destination rows lightest-first; skip max_row itself
            # and any row that would create a SAME-DOMAIN,
            # DIFFERENT-PURPOSE collision at the destination (the actual
            # electrical risk -- insert_row_buffers.py only merges
            # sinks sharing both a target net (== domain here) AND a
            # row; cross-DOMAIN sharing a row is never a buffering
            # conflict since it's a different net entirely, and
            # same-purpose sharing is exempt per _purpose_of).
            for dest in sorted(range(n_rows), key=lambda r: rw[r]):
                if dest == max_row:
                    continue
                conflict = any(
                    domain_by_group[g2] == domain_by_group[g]
                    and purpose_by_group[g2] != purpose_by_group[g]
                    for g2, r2 in rows_by_group.items() if r2 == dest and g2 != g)
                if conflict:
                    continue
                if rw[dest] + cluster_w[g] > _cap_for(max_row_width, dest, n_rows):
                    continue  # would push the destination over ITS OWN cap
                if rw[dest] + cluster_w[g] >= rw[max_row]:
                    continue  # would not actually reduce the max
                rows_by_group[g] = dest
                moved_total += 1
                moved = True
                break
            if moved:
                break
        if not moved:
            print(f"  WARNING: row{max_row} at {rw[max_row]:.1f}um exceeds its "
                  f"max_row_width cap ({_cap_for(max_row_width, max_row, n_rows):.1f}um) and no "
                  f"further width-reducing cluster move is available without a cross-purpose "
                  f"collision or exceeding another row's own cap -- leaving as-is")
            break

    if moved_total:
        rw = row_widths(rows_by_group)
        print(f"  width rebalance: moved {moved_total} cluster(s) -> widths now "
              + ", ".join(f"row{r}={rw.get(r, 0.0):.1f}um" for r in range(n_rows)))
    return rows_by_group


def fm_multiway_partition_grouped(instances, widths, n_rows, groups=DFF_GROUPS, balance_tol=0.05,
                                   max_row_width=None):
    """Drop-in replacement for fm_partition.fm_multiway_partition() that
    guarantees (a) every `groups` member set lands in a single row, AND
    (b) no two DIFFERENT-purpose groups sharing a clock domain (see
    _domain_of/_purpose_of) land on the SAME row -- both needed for the
    resulting row assignment to actually prevent 108.19-/108.21-style
    buffer overload once insert_row_buffers.py runs on it.

    max_row_width (108.31): if given (in the same units as `widths`,
    i.e. um), a post-processing pass (_rebalance_clusters_by_width)
    greedily moves whole clusters between rows until every row's total
    real-cell width is at or under this budget, or no further
    collision-safe move can help -- see that function's docstring for
    why balance_tol alone doesn't guarantee this (FM's own width-balance
    objective has no notion of the physical per-row track budget
    gen_placement_nrow_fm.py enforces). Defaults to None (disabled, the
    original behavior) since not every caller places on a fixed-width
    row grid.

    Groups of size 1 pass straight through the clustering machinery
    unchanged (a 1-element cluster behaves identically to not clustering
    it at all), so it is safe to list singleton groups purely for
    documentation, as DFF_GROUPS does for sclN_sda_oe_r.
    """
    reduced, widths_ext, cluster_members = build_grouped_instances(instances, widths, groups)
    part = fm_multiway_partition(reduced, widths_ext, n_rows, balance_tol=balance_tol)

    cluster_rows = {g: part[g] for g in groups}
    cluster_rows = _resolve_domain_collisions(cluster_rows, groups, n_rows)

    if max_row_width is not None:
        passthrough_rows = {name: row for name, row in part.items() if name not in cluster_members}
        cluster_rows = _rebalance_clusters_by_width(
            cluster_rows, cluster_members, groups, passthrough_rows,
            instances, widths, n_rows, max_row_width)

    full_part = {}
    for name, row in part.items():
        if name in cluster_members:
            continue  # replaced below using the (possibly collision-resolved) cluster_rows
        full_part[name] = row
    for gname, members in cluster_members.items():
        for member in members:
            full_part[member] = cluster_rows[gname]
    return full_part


def rebalance_final_assignment(part, instances, widths, groups, n_rows, max_row_width):
    """108.33: fm_multiway_partition_grouped's max_row_width rebalance
    (_rebalance_clusters_by_width) only sees the netlist AT THE POINT
    IT'S CALLED -- the pre-row-buffer, pre-BUFTH netlist. Every later
    pipeline stage that adds a handful more instances to specific rows
    (insert_row_buffers.py's BUF_X1 buffers, insert_bufth_scl_sda.py's
    BUFTH + sda_in row buffers) does so via its own lightest-row or
    reference-partition-row heuristic, with NO awareness of
    MAX_ROW_WIDTH_UM -- so a row that was comfortably under budget right
    after DFF_GROUPS clustering can still end up over budget by the time
    the FINAL netlist (with every downstream buffer stage's insertions)
    is assembled. Confirmed happening on V10: row1 was 1334.0um right
    after the DFF_GROUPS-stage rebalance (under the 1350um budget), but
    1387.8um after row-buffer + BUFTH/sda_in-buffer insertion (accreted
    ~54um from several small BUF_X1/BUFTH additions, none individually
    budget-checked) -- and that 37.8um overage was enough to genuinely
    exhaust row1's free (FILL-only) crossing space: only 189.0um (11.7%)
    of row1 was FILL vs 20-25% for every other row, and
    route_channels_nrow_fm.py's pass 2 hard-crashed unable to find ANY
    clear X for a spanning net's row1 crossing.

    Call this as a FINAL pass on the complete post-BUFTH row assignment
    (part), right before gen_placement_nrow_fm.py consumes it. Unlike
    _rebalance_clusters_by_width (which only moves whole DFF_GROUPS
    clusters), this moves INDIVIDUAL non-grouped instances (row buffers,
    BUFTH cells, and any other passthrough logic) -- they carry no
    domain/purpose collision risk at all (only DFF_GROUPS members do),
    so this is a simple greedy largest-movable-instance-first rebalance
    with no collision constraint to respect."""
    grouped_names = {m for members in groups.values() for m in members}
    type_by_name = {name: typ for typ, name, _p in instances}
    movable = [name for name in part if name not in grouped_names]

    result = dict(part)
    moved_total = 0
    while True:
        rw = defaultdict(float)
        for name, row in result.items():
            rw[row] += widths[type_by_name[name]]
        for r in range(n_rows):
            rw.setdefault(r, 0.0)
        overs = {r: rw[r] - _cap_for(max_row_width, r, n_rows) for r in range(n_rows)
                 if rw[r] > _cap_for(max_row_width, r, n_rows)}
        if not overs:
            break
        max_row = max(overs, key=lambda r: overs[r])
        candidates = sorted(
            (n for n in movable if result[n] == max_row),
            key=lambda n: widths[type_by_name[n]], reverse=True)
        moved = False
        for n in candidates:
            for dest in sorted(range(n_rows), key=lambda r: rw[r]):
                if dest == max_row:
                    continue
                w = widths[type_by_name[n]]
                if rw[dest] + w > _cap_for(max_row_width, dest, n_rows):
                    continue  # would push the destination over ITS OWN cap
                if rw[dest] + w >= rw[max_row]:
                    continue
                result[n] = dest
                moved_total += 1
                moved = True
                break
            if moved:
                break
        if not moved:
            print(f"  WARNING: row{max_row} at {rw[max_row]:.1f}um exceeds its max_row_width "
                  f"cap ({_cap_for(max_row_width, max_row, n_rows):.1f}um) and no further "
                  f"width-reducing instance move is available -- leaving as-is")
            break

    if moved_total:
        rw = defaultdict(float)
        for name, row in result.items():
            rw[row] += widths[type_by_name[name]]
        print(f"  final-assignment width rebalance: moved {moved_total} instance(s) -> "
              "widths now " + ", ".join(f"row{r}={rw.get(r, 0.0):.1f}um" for r in range(n_rows)))
    return result


def _report_groups(part, groups, label):
    print(f"108.23 group check ({label}):")
    all_ok = True
    group_row = {}
    for gname, members in groups.items():
        rows = sorted({part[m] for m in members})
        ok = len(rows) == 1
        all_ok = all_ok and ok
        marker = "OK" if ok else "*** SPLIT ACROSS ROWS ***"
        print(f"  {gname:<26} members={len(members):<2} row(s)={rows}  {marker}")
        if ok:
            group_row[gname] = rows[0]

    by_domain = defaultdict(list)
    for gname, row in group_row.items():
        by_domain[_domain_of(gname)].append((gname, row))
    for domain, entries in by_domain.items():
        by_row = defaultdict(set)
        for gname, row in entries:
            by_row[row].add(_purpose_of(gname))
        colliding_rows = {r: purposes for r, purposes in by_row.items() if len(purposes) > 1}
        if colliding_rows:
            all_ok = False
            print(f"  *** DOMAIN COLLISION *** {domain!r}: "
                  + ", ".join(f"row{r}={sorted(purposes)}" for r, purposes in colliding_rows.items()))
    return all_ok


if __name__ == "__main__":
    macros = parse_lef()
    widths = {name: m["size"][0] for name, m in macros.items()}
    net = parse_netlist(path=V10_NET_PATH)
    instances = net["instances"]
    print(f"parsed {len(instances)} instances from {V10_NET_PATH}")

    print(f"\n=== baseline: plain fm_multiway_partition (N_ROWS={N_ROWS}) ===")
    part_baseline = fm_multiway_partition(instances, widths, N_ROWS)
    _report_groups(part_baseline, DFF_GROUPS, "baseline, no constraint")

    print(f"\n=== constrained: fm_multiway_partition_grouped "
          f"(N_ROWS={N_ROWS}, balance_tol={GROUPED_BALANCE_TOL}, "
          f"max_row_width={ROW_WIDTH_CAP_UM}) ===")
    part_grouped = fm_multiway_partition_grouped(instances, widths, N_ROWS,
                                                  balance_tol=GROUPED_BALANCE_TOL,
                                                  max_row_width=ROW_WIDTH_CAP_UM)
    all_ok = _report_groups(part_grouped, DFF_GROUPS, "grouped, must all be OK")
    if not all_ok:
        print("ERROR: a group was split even after clustering -- this should not happen",
              file=sys.stderr)
        sys.exit(1)

    row_counts = defaultdict(int)
    for r in part_grouped.values():
        row_counts[r] += 1
    print("\nrow occupancy (all instances, grouped run):")
    for r in sorted(row_counts):
        print(f"  row{r}: {row_counts[r]} instances")

    cut_before = classify_multirow_nets(instances, part_baseline, N_ROWS)
    cut_after = classify_multirow_nets(instances, part_grouped, N_ROWS)
    print(f"\nnet locality, baseline:   {cut_before}")
    print(f"net locality, grouped:    {cut_after}")

    with open(OUT_JSON, "w") as f:
        json.dump(part_grouped, f, indent=1, sort_keys=True)
    print(f"\nwrote {OUT_JSON} ({len(part_grouped)} instances)")
