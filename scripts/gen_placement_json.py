#!/usr/bin/env python3
"""gen_placement_json.py -- placement JSON in the schema the I2C router reads.

scripts/place.py owns the placement itself; this converts its step4 output
into the exact schema route_channels_nrow_fm.py (and the rest of the ported
I2C chain) expects, so those scripts run unmodified:

  {"row_height", "row_width", "ch_heights",
   "rows": [[{"type", "name", "x", "width",
              "pins": {PIN: {"net", "use", "direction",
                             "rects": [[layer, x0, y0, x1, y1], ...]}}}]]}

Pin rectangles come from the LEF and are shifted to ABSOLUTE x (the router
adds only the row's y offset); nets come from the gate-level netlist with
Yosys `assign` aliases already resolved by netlist_parser.

TAP/FILL instances get synthetic names.  The FILL2 reserved right after each
TAP is named FILLPRI_* -- the router auto-detects those as preferred
row-crossing corridors.

  usage:  scripts/gen_placement_json.py [-o layout/placement_nrow_fm.json]
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spi_config as cfg          # noqa: E402
import lef_parser                 # noqa: E402
import netlist_parser             # noqa: E402

PLACE = os.path.join(cfg.LAYOUT, "step4", "place_step4_fill.json")


def main(place_json=PLACE, out_json=cfg.PLACEMENT_JSON,
         net_path=cfg.NET_PATH, lef_path=cfg.LEF_PATH):
    pl = json.load(open(place_json))
    macros = lef_parser.parse_lef(lef_path)
    net = netlist_parser.parse_netlist(net_path)
    pins_of = {name: pins for _t, name, pins in net["instances"]}
    type_of = {name: t for t, name, _p in net["instances"]}

    rows, npri, nsig = [], 0, 0
    for r, row in enumerate(pl["rows"]):
        out = []
        for k, e in enumerate(row):
            cell, inst = e["cell"], e["inst"]
            if inst:
                name = inst
                assert type_of[inst] == cell, \
                    f"{inst}: netlist says {type_of[inst]}, placement says {cell}"
            elif e.get("pri"):
                name = f"FILLPRI_r{r}_{npri}"
                npri += 1
            elif cell.startswith("TAP"):
                name = f"TAP_r{r}_{k}"
            else:
                name = f"FILL_r{r}_{k}"

            mp = macros[cell]["pins"]
            pins = {}
            for pname, pinfo in mp.items():
                rects = [[lay, round(e["x"] + x0, 4), y0,
                          round(e["x"] + x1, 4), y1]
                         for lay, x0, y0, x1, y1 in pinfo["rects"]]
                netname = None
                if pinfo["use"] not in ("POWER", "GROUND"):
                    netname = pins_of.get(inst, {}).get(pname) if inst else None
                    if netname:
                        nsig += 1
                pins[pname] = {"net": netname, "use": pinfo["use"],
                               "direction": pinfo["direction"], "rects": rects}
            out.append({"type": cell, "name": name, "row": r, "x": e["x"],
                        "width": e["w"], "pins": pins})
        w = round(out[-1]["x"] + out[-1]["width"], 3)
        assert abs(w - pl["core_w"]) < 1e-6, f"row {r} ends at {w}, not {pl['core_w']}"
        rows.append(out)

    data = {"row_height": pl["row_h"], "row_width": pl["core_w"],
            "ch_heights": pl["channel_heights"], "core_h": pl["core_h"],
            "top_cell": cfg.TOP_CELL_NAME, "rows": rows}
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    json.dump(data, open(out_json, "w"), indent=1)
    print(f"wrote {os.path.relpath(out_json, cfg.ROOT)}")
    print(f"  rows {[len(r) for r in rows]}   row_width {data['row_width']}"
          f"   ch_heights {data['ch_heights']}")
    print(f"  {npri} priority corridor(s), {nsig} signal pin(s) with a net")
    return out_json


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--placement", default=PLACE)
    ap.add_argument("-o", "--output", default=cfg.PLACEMENT_JSON)
    ap.add_argument("--netlist", default=cfg.NET_PATH)
    ap.add_argument("--lef", default=cfg.LEF_PATH)
    a = ap.parse_args()
    main(a.placement, a.output, a.netlist, a.lef)
