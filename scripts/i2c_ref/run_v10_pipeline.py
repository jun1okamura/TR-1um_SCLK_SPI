"""
run_v10_pipeline.py (design_notes.md section 108.38)

Single, standardized entry point for the full V10 netlist -> placement ->
routing -> short-cleanup pipeline. Runs every stage in the correct order,
starting UNCONDITIONALLY from dedup_gates.py (section 108.37/108.38: a
raw-Yosys-output duplicate-parallel-gate bug, identical in shape to
section 42.1's, was found still present in i2c_slave_async_net_v10.v and
was the true root cause of most of the routing congestion this pipeline
used to need heavy placement-level workarounds for -- see 108.34-108.37).

Stage order (each stage's output feeds the next; canonical file names,
no "_deduped_" suffix -- dedup is simply step 0 of "the" V10 pipeline
now, not a side branch):
  0. dedup_gates.py:              i2c_slave_async_net_v10.v
                                   -> i2c_slave_async_net_v10_deduped.v
  1. merge_muxdffrb_rslatch.py:   -> i2c_slave_async_net_v10_muxdffrb.v
  2. insert_row_buffers.py:       -> i2c_slave_async_net_v10_buffered.v
                                   (+ row_assignment_v10_buffered.json)
                                   using apply_dff_group_constraints.
                                   fm_multiway_partition_grouped with
                                   ROW_WIDTH_CAP_UM (108.36's per-row-pair
                                   asymmetric width cap, NOT the flat
                                   MAX_ROW_WIDTH_UM)
  3. insert_bufth_scl_sda.py:     -> i2c_slave_async_net_v10_final.v
                                   (+ row_assignment_v10_final.json),
                                   existing_part=<stage 2's row
                                   assignment> (108.33: never let this
                                   stage re-run fm_multiway_partition
                                   fresh -- confirmed unstable)
  4. apply_dff_group_constraints.rebalance_final_assignment: final
     individual-instance width rebalance against ROW_WIDTH_CAP_UM,
     applied in place to row_assignment_v10_final.json
  5. gen_placement_nrow_fm.py:    -> placement_nrow_fm_v10.json
  6. gen_placement_gds_nrow_fm.py: -> layout/step10/v10_step_1_placement.gds
  7. route_channels_nrow_fm.py:   -> layout/step10/v10_step_2_routed_raw.gds
                                   per_row_local_nets includes '_016_'/
                                   '_017_' (108.37's post-dedup merged
                                   RSTB nets -- NOT '_143_'/
                                   'rst_scl_domain_held', which dedup
                                   reduced to trivial low-fanout nets)
  8. ripup_reroute_shorts.py:     -> layout/step10/v10_step_3_ripup_reroute.gds
  9. drc_check_nrow_fm.py + verify_connectivity_nrow_fm.py: sanity checks
     (108.40: 0 DRC violations AND 0 shorts is the current, verified,
     reproducible result -- PER_ROW_LOCAL_NETS/FORCE_JOG_NETS above and
     ripup_reroute_shorts.py's SIMPLE_PIN_MAX refinement were all tuned
     to reach this. If a future RTL/GDS change reintroduces a residual
     short, first re-run dedup_gates.py and diff its "found N duplicate
     group(s)" line against 0 -- 108.37 found that duplicate-parallel-
     gate regressions are the single most likely root cause of new
     congestion here, not a routing-policy regression)

Run: python3 script/run_v10_pipeline.py
(All paths are the module-level constants below; edit them directly for
a one-off deviation rather than adding CLI flags -- matching every other
script in this pipeline's convention.)
"""
import functools
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dedup_gates  # noqa: E402
import merge_muxdffrb_rslatch  # noqa: E402
import apply_dff_group_constraints as dg  # noqa: E402
import insert_row_buffers as irb  # noqa: E402
import insert_bufth_scl_sda as ibs  # noqa: E402
import gen_placement_nrow_fm as gp  # noqa: E402
import gen_placement_gds_nrow_fm as gg  # noqa: E402
import route_channels_nrow_fm as rc  # noqa: E402
import ripup_reroute_shorts as rr  # noqa: E402
from lef_parser import parse_lef  # noqa: E402
from netlist_parser import parse_netlist  # noqa: E402

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_SRC = _REPO_ROOT / "src"
_LEF = _REPO_ROOT / "LEF"
_STEP10 = _REPO_ROOT / "layout" / "step10"

RAW_V10 = str(_SRC / "i2c_slave_async_net_v10.v")
DEDUPED = str(_SRC / "i2c_slave_async_net_v10_deduped.v")
MUXDFFRB = str(_SRC / "i2c_slave_async_net_v10_muxdffrb.v")
BUFFERED = str(_SRC / "i2c_slave_async_net_v10_buffered.v")
FINAL = str(_SRC / "i2c_slave_async_net_v10_final.v")

ROW_ASSIGN_BUFFERED = str(_LEF / "row_assignment_v10_buffered.json")
ROW_ASSIGN_FINAL = str(_LEF / "row_assignment_v10_final.json")
PLACEMENT_JSON = str(_LEF / "placement_nrow_fm_v10.json")

PLACEMENT_GDS = str(_STEP10 / "v10_step_1_placement.gds")
ROUTED_RAW_GDS = str(_STEP10 / "v10_step_2_routed_raw.gds")
RIPUP_GDS = str(_STEP10 / "v10_step_3_ripup_reroute.gds")

PIN_MAP_JSON = str(_SCRIPT_DIR / "pin_map_nrow_fm_v10.json")
NET_SHAPES_JSON = str(_SCRIPT_DIR / "net_shapes_nrow_fm_v10.json")
CHANNEL_USAGE_JSON = str(_SCRIPT_DIR / "channel_usage_nrow_fm_v10.json")
FORCE_JOG_EVENTS_JSON = str(_SCRIPT_DIR / "force_jog_events_nrow_fm_v10.json")
PIN_MAP_RR_JSON = str(_SCRIPT_DIR / "pin_map_nrow_fm_v10_rr.json")
NET_SHAPES_RR_JSON = str(_SCRIPT_DIR / "net_shapes_nrow_fm_v10_rr.json")

CH_HEIGHTS = [131.6, 700.0, 1000.0, 700.0, 153.2]
# 108.37: post-dedup, '_143_'/'rst_scl_domain_held' collapsed to trivial
# low-fanout nets (a single NOR2 gate now merges what used to be 24/9
# duplicate gates) -- '_016_'/'_017_' are their SUCCESSOR broad-fanout
# nets (the merged NOR2/AND2_X1's own outputs) and are what now need
# per-row-local trunk treatment, matching what '_143_' used to need.
PER_ROW_LOCAL_NETS = {"scl_buf", "sda_in_buf", "scl_n", "_016_", "_017_", "_156_"}
FORCE_HIGH_FO_NETS = {"scl_n_row2"}
# 108.40: '_194_'/'txreg[5]' (2/3-pin nets) needed pass3's dedicated
# live-checked treatment to clear a full-height channel3 crossing that
# pass2's generic spanning-net handling couldn't resolve -- see
# design_notes.md 108.40 for the full before/after. Net-specific, found
# empirically; a future RTL/GDS change may shift which net(s), if any,
# need this -- re-derive by checking verify_connectivity_nrow_fm.py's
# output after a routing-only run (before ripup_reroute_shorts.py).
FORCE_JOG_NETS = {"txreg[5]", "_194_"}


def main():
    print("=== stage 0: dedup_gates.py ===")
    dedup_gates.main(RAW_V10, DEDUPED)

    print("\n=== stage 1: merge_muxdffrb_rslatch.py ===")
    merge_muxdffrb_rslatch.main(in_path=DEDUPED, out_path=MUXDFFRB)

    print("\n=== stage 2: insert_row_buffers.py (DFF_GROUPS + ROW_WIDTH_CAP_UM) ===")
    pf = functools.partial(dg.fm_multiway_partition_grouped, groups=dg.DFF_GROUPS,
                            balance_tol=dg.GROUPED_BALANCE_TOL, max_row_width=dg.ROW_WIDTH_CAP_UM)
    irb.main(in_path=MUXDFFRB, out_path=BUFFERED, row_assignment_json=ROW_ASSIGN_BUFFERED,
             target_nets=["scl", "scl_n"], partition_fn=pf)

    print("\n=== stage 3: insert_bufth_scl_sda.py (existing_part) ===")
    existing_part = json.load(open(ROW_ASSIGN_BUFFERED))
    ibs.main(in_path=BUFFERED, out_path=FINAL, row_assignment_json=ROW_ASSIGN_FINAL,
              tmp_stage1_path=str(_SRC / ".i2c_slave_async_net_v10_final_stage1_tmp.v"),
              existing_part=existing_part)

    print("\n=== stage 4: rebalance_final_assignment (ROW_WIDTH_CAP_UM) ===")
    macros = parse_lef()
    widths = {name: m["size"][0] for name, m in macros.items()}
    net = parse_netlist(path=FINAL)
    instances = net["instances"]
    part = json.load(open(ROW_ASSIGN_FINAL))
    type_by_name = {n: t for t, n, _p in instances}
    rw = defaultdict(float)
    for name, row in part.items():
        rw[row] += widths[type_by_name[name]]
    print("  before:", {r: round(rw[r], 1) for r in sorted(rw)})
    result = dg.rebalance_final_assignment(part, instances, widths, dg.DFF_GROUPS, dg.N_ROWS,
                                            dg.ROW_WIDTH_CAP_UM)
    rw2 = defaultdict(float)
    for name, row in result.items():
        rw2[row] += widths[type_by_name[name]]
    print("  after: ", {r: round(rw2[r], 1) for r in sorted(rw2)})
    json.dump(result, open(ROW_ASSIGN_FINAL, "w"), indent=1)

    print("\n=== stage 5: gen_placement_nrow_fm.py ===")
    gp.NUDGE_BEFORE = {}  # 108.36: no pinpoint nudge is in standard use -- confirmed unhelpful
    gp.main(net_file=FINAL, out_json=PLACEMENT_JSON, part_json=ROW_ASSIGN_FINAL)

    print("\n=== stage 6: gen_placement_gds_nrow_fm.py ===")
    gg.main(placement_json=PLACEMENT_JSON, out_gds=PLACEMENT_GDS, ch_heights=CH_HEIGHTS)

    print("\n=== stage 7: route_channels_nrow_fm.py ===")
    rc.main(placement_json=PLACEMENT_JSON, in_gds=PLACEMENT_GDS, out_gds=ROUTED_RAW_GDS,
             ch_heights=CH_HEIGHTS, force_jog_nets=FORCE_JOG_NETS, per_row_local_nets=PER_ROW_LOCAL_NETS,
             force_high_fo_nets=FORCE_HIGH_FO_NETS, pin_map_path=PIN_MAP_JSON,
             net_shapes_path=NET_SHAPES_JSON, channel_usage_path=CHANNEL_USAGE_JSON,
             force_jog_events_path=FORCE_JOG_EVENTS_JSON)

    print("\n=== stage 8: ripup_reroute_shorts.py ===")
    # ripup_reroute_shorts.main() takes no params -- it reads sys.argv
    # directly (see its own __main__ block), so drive it the same way
    # here rather than duplicating its argv-parsing logic.
    ch_heights_csv = ",".join(str(h) for h in CH_HEIGHTS)
    sys.argv = ["ripup_reroute_shorts.py", ROUTED_RAW_GDS, PIN_MAP_JSON, NET_SHAPES_JSON,
                PLACEMENT_JSON, ch_heights_csv, RIPUP_GDS, PIN_MAP_RR_JSON, NET_SHAPES_RR_JSON, "60"]
    rr.main()

    print("\n=== stage 9: DRC + connectivity sanity check ===")
    import subprocess
    subprocess.run([sys.executable, str(_SCRIPT_DIR / "drc_check_nrow_fm.py"), RIPUP_GDS])
    subprocess.run([sys.executable, str(_SCRIPT_DIR / "verify_connectivity_nrow_fm.py"),
                     RIPUP_GDS, PIN_MAP_RR_JSON, "0", "2944", "1650"])

    print(f"\n=== pipeline complete: {RIPUP_GDS} ===")


if __name__ == "__main__":
    main()
