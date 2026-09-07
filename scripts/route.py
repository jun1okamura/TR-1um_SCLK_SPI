#!/usr/bin/env python3
"""route.py -- placement GDS -> routed core, following the I2C pipeline.

The stage order and every routing script are those of
TR-1um_Async_I2C/script/run_v10_pipeline.py; only the paths, the top cell
name and the per-design net sets are this project's (see scripts/PORTING.md).

Stages, each leaving its own GDS:

  step5  placement GDS + channel annotation   gen_placement_gds_nrow_fm.py
  step6  channel routing (5 internal passes,  route_channels_nrow_fm.py
         each checkpointed as its own GDS)
  step7  short rip-up / re-route              ripup_reroute_shorts.py
  step8  top-level pin pull-out               route_top_pins_nrow_fm.py
  step9  VDD/GND chip-level pins              add_power_pins_nrow_fm.py
  step10 channel compaction                   squeeze_channels_nrow_fm.py
         then two coverage checks: every top-level port has a pin marker,
         and every pin marker really reaches its cell pin
         (PIN markers and their labels are protected from the compaction --
          see scripts/port_rules.py)

  usage:
    scripts/route.py                 # all stages
    scripts/route.py --to 6          # stop after channel routing
    scripts/route.py --from 7        # resume
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spi_config as cfg  # noqa: E402

# --- per-design routing hints -------------------------------------------
# Both are empirical on the I2C chip and MUST be re-derived here; start
# empty and add nets only when verify_connectivity reports a short that the
# rip-up pass cannot clear (see run_v10_pipeline.py's own note).
PER_ROW_LOCAL_NETS = {"sclk_buf", "shift_clk", "cs_n_buf"}
FORCE_HIGH_FO_NETS = set()
FORCE_JOG_NETS = set()

# Top-level port directions as seen from the GIO frame.  Derived from the
# netlist's own port declarations by the ported highlight_top_pins module,
# so it cannot drift out of step with the design (see scripts/PORTING.md).
def _port_dir():
    import highlight_top_pins_nrow_fm as h
    return dict(h.PORT_DIR_DERIVED)


def expected_ports():
    """every top-level pin the layout must expose: scalars + bus bits."""
    import highlight_top_pins_nrow_fm as h
    out = list(h.SCALAR_PORTS)
    for bus, w in h.BUS_PORTS.items():
        out += [f"{bus}[{i}]" for i in range(w)]
    return sorted(out)


def check_port_pins(gds, extra=("VDD", "GND")):
    """Fail loudly if a top-level port never made it to the core boundary.

    This is the check that would have caught the ported top-pin router
    still carrying the I2C design's hardcoded port names (design_notes
    14.5): only the buses were pulled out, and the nine scalar ports --
    including the BUFTH input nets sclk/cs_n/sdio_in -- were skipped in
    silence.
    """
    import klayout.db as db
    ly = db.Layout()
    ly.read(gds)
    top = ly.cell(cfg.TOP_CELL_NAME)
    labels = set()
    for lay, dt in ((49, 0), (48, 0)):
        for t in top.shapes(ly.layer(lay, dt)).each():
            if t.is_text():
                labels.add(t.dtext.string)
    want = expected_ports()
    missing = [p for p in want if p not in labels]
    print(f"\n=== top-level pin coverage ({os.path.relpath(gds, cfg.ROOT)}) ===")
    print(f"  ports expected : {len(want)}")
    print(f"  pin labels     : {len(labels)}  ({', '.join(sorted(labels - set(extra))[:6])}...)")
    if missing:
        print(f"  !! MISSING {len(missing)}: {', '.join(missing)}")
        return False
    print("  OK: every top-level port has a pin label")
    for e in extra:
        if e not in labels:
            print(f"  note: no '{e}' pin label (added by step9)")
    return True


def stage5(ch_heights):
    import gen_placement_gds_nrow_fm as g
    os.makedirs(os.path.dirname(cfg.PLACEMENT_GDS), exist_ok=True)
    g.CELL_GDS = cfg.CELL_GDS
    g.TOP_CELL_NAME = cfg.TOP_CELL_NAME
    g.main(placement_json=cfg.PLACEMENT_JSON, out_gds=cfg.PLACEMENT_GDS,
           ch_heights=ch_heights)


def stage6(ch_heights):
    import route_channels_nrow_fm as rc
    d = os.path.dirname(cfg.ROUTED_RAW_GDS)
    os.makedirs(d, exist_ok=True)
    rc.main(placement_json=cfg.PLACEMENT_JSON, in_gds=cfg.PLACEMENT_GDS,
            out_gds=cfg.ROUTED_RAW_GDS, ch_heights=ch_heights,
            force_jog_nets=FORCE_JOG_NETS,
            per_row_local_nets=PER_ROW_LOCAL_NETS,
            force_high_fo_nets=FORCE_HIGH_FO_NETS,
            pin_map_path=cfg.PIN_MAP_JSON,
            net_shapes_path=cfg.NET_SHAPES_JSON,
            channel_usage_path=cfg.CHANNEL_USAGE_JSON,
            force_jog_events_path=cfg.FORCE_JOG_EVENTS_JSON,
            compaction_info_path=cfg.COMPACTION_INFO_JSON,
            checkpoint_dir=d, checkpoint_prefix="route_step_2")


def stage7(ch_heights):
    import ripup_reroute_shorts as rr
    os.makedirs(os.path.dirname(cfg.RIPUP_GDS), exist_ok=True)
    sys.argv = ["ripup_reroute_shorts.py", cfg.ROUTED_RAW_GDS, cfg.PIN_MAP_JSON,
                cfg.NET_SHAPES_JSON, cfg.PLACEMENT_JSON,
                ",".join(str(h) for h in ch_heights), cfg.RIPUP_GDS,
                cfg.PIN_MAP_RR_JSON, cfg.NET_SHAPES_RR_JSON, "60"]
    rr.main()


def stage8(ch_heights):
    import route_top_pins_nrow_fm as rt
    os.makedirs(os.path.dirname(cfg.TOPPINS_GDS), exist_ok=True)
    rt.PORT_DIR = _port_dir()
    rt.main(placement_json=cfg.PLACEMENT_JSON, in_gds=cfg.RIPUP_GDS,
            out_gds=cfg.TOPPINS_GDS, ch_heights=ch_heights,
            net_shapes_json=cfg.NET_SHAPES_RR_JSON, net_file=cfg.NET_PATH)


    if not check_port_pins(cfg.TOPPINS_GDS):
        raise SystemExit("!! top-level ports missing from the layout")


def stage9(ch_heights):
    import add_power_pins_nrow_fm as ap
    os.makedirs(os.path.dirname(cfg.POWERPINS_GDS), exist_ok=True)
    ap.main(placement_json=cfg.PLACEMENT_JSON, in_gds=cfg.TOPPINS_GDS,
            out_gds=cfg.POWERPINS_GDS, core_h=routed_core_h(ch_heights))


def stage10(ch_heights):
    """channel compaction: drop the Y slices no wire actually uses, without
    re-routing (the I2C flow's STEP7)."""
    import squeeze_channels_nrow_fm as sq
    os.makedirs(os.path.dirname(cfg.SQUEEZED_GDS), exist_ok=True)
    sq.main(in_gds=cfg.POWERPINS_GDS,
            compaction_info_path=cfg.COMPACTION_INFO_JSON,
            out_gds=cfg.SQUEEZED_GDS,
            pin_map_in=cfg.PIN_MAP_RR_JSON, pin_map_out=cfg.PIN_MAP_SQ_JSON,
            net_shapes_in=cfg.NET_SHAPES_RR_JSON,
            net_shapes_out=cfg.NET_SHAPES_SQ_JSON)


def routed_core_h(ch):
    p = json.load(open(cfg.PLACEMENT_JSON))
    return sum(ch) + len(p["rows"]) * p["row_height"]


def checks(gds, pin_map, ch, squeezed=False):
    print("\n=== DRC ===")
    subprocess.run([sys.executable, os.path.join(HERE, "drc_check_nrow_fm.py"),
                    gds, cfg.TOP_CELL_NAME])
    print("\n=== connectivity (M1-then-M2 pin search) ===")
    p = json.load(open(cfg.PLACEMENT_JSON))
    # the scan window must cover the ROUTED core, not the placement estimate
    if squeezed:
        import gdstk
        top = [c for c in gdstk.read_gds(gds).cells
               if c.name == cfg.TOP_CELL_NAME][0]
        hi = top.bounding_box()[1][1] + 10
    else:
        hi = routed_core_h(ch) + 10
    subprocess.run([sys.executable,
                    os.path.join(HERE, "verify_connectivity_nrow_fm_m1m2.py"),
                    gds, pin_map, "0", str(hi), str(p["row_width"] + 30)])


STAGES = {5: stage5, 6: stage6, 7: stage7, 8: stage8, 9: stage9,
          10: stage10}


def main(first=5, last=10, ch=None):
    ch = ch or cfg.ROUTE_CH_HEIGHTS
    n_rows = len(json.load(open(cfg.PLACEMENT_JSON))["rows"])
    assert len(ch) == n_rows + 1, f"need {n_rows + 1} channel heights, got {len(ch)}"
    print(f"routing channel budget: {ch}  (placement estimate was "
          f"{json.load(open(cfg.PLACEMENT_JSON))['ch_heights']})")
    for n in range(first, last + 1):
        print(f"\n{'=' * 60}\n=== step{n} ===\n{'=' * 60}")
        STAGES[n](ch)
    if last >= 10:
        checks(cfg.SQUEEZED_GDS, cfg.PIN_MAP_SQ_JSON, ch, squeezed=True)
        if not check_port_pins(cfg.SQUEEZED_GDS):
            raise SystemExit("!! top-level ports missing from the final layout")
        print()
        import verify_port_connectivity as vpc
        if vpc.main(cfg.SQUEEZED_GDS) != 0:
            raise SystemExit("!! a top-level port does not reach its cell pin")
    elif last >= 9:
        checks(cfg.POWERPINS_GDS, cfg.PIN_MAP_RR_JSON, ch)
    elif last >= 8:
        checks(cfg.TOPPINS_GDS, cfg.PIN_MAP_RR_JSON, ch)
    elif last >= 7:
        checks(cfg.RIPUP_GDS, cfg.PIN_MAP_RR_JSON, ch)
    elif last >= 6:
        checks(cfg.ROUTED_RAW_GDS, cfg.PIN_MAP_JSON, ch)


if __name__ == "__main__":
    ap_ = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap_.add_argument("--from", dest="first", type=int, default=5)
    ap_.add_argument("--to", dest="last", type=int, default=10)
    ap_.add_argument("--ch-heights", default=None,
                     help="comma-separated channel budget, bottom margin first")
    a = ap_.parse_args()
    main(a.first, a.last,
         [float(x) for x in a.ch_heights.split(",")] if a.ch_heights else None)
