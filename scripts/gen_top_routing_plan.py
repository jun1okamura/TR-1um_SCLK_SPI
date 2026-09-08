#!/usr/bin/env python3
"""gen_top_routing_plan.py -- the chip-level connection map and routing plan.

  -> layout/chip/gio_connections.json     what connects to what, and why
  -> layout/chip/signal_routing_plan.json where each of those endpoints is

Same split the I2C project used: one file states the *logical* pad-ring <-> core
map (with the pad-cell behaviour it depends on spelled out), the other pins that
map to real coordinates for the router.  Both are generated:

  * pad terminal geometry comes from lef/TR-1um_frame_25x25.gds itself
    (scripts/frame_pins.py), not a transcribed table;
  * core pin geometry comes from the routed core's own labels in
    layout/step10/route_step_6_squeezed.gds, shifted by the assembly offset
    scripts/assemble_top.py uses;
  * the core's port list comes from the netlist.

Only PAD_MAP below is written by hand, because the pad assignment is a design
decision -- it is hdl/tr_1um_3wire_SPI.v's own table (design_notes.md section 2).
Every core port is checked off against it, so a port that gains or loses a pad
is an error rather than a silent omission.

PAD CELL BEHAVIOUR (OSS_ESD_5V_DIO, one per pad inside OSS_FRAME_GIO)
--------------------------------------------------------------------
  HIZ<n> = 1  ->  output driver off, pad is Hi-Z / input only
  HIZ<n> = 0  ->  output driver on, pad is driven by OUT<n>
Established by the I2C project from a transistor-level trace of the pad cell,
not from the pin names (schematic/gio_connections.json, pad_cell_behavior).

That polarity is why the DATA pads take their HIZ straight off the DIS pad net:
the core drives DATA when data_oe = ~dis = 1, i.e. exactly when dis = 0, so
HIZ = dis needs no logic at all.  The I2C chip wired its own DIS pad to eight
HIZ pins the same way.

SDIO's own enable follows the same rule: the core emits sdio_oe_n = ~(dis &
~cs_n), active LOW, so it drives HIZ2 directly.  The first cut of this design
had an active-high sdio_oe and would have needed a lone inverter out in the
80 um channel; the core was re-synthesised instead (design_notes.md 16.6).

  usage:  scripts/gen_top_routing_plan.py
"""
import argparse
import json
import os
import re
import sys

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg
import frame_pins

OUT_CONN = os.path.join(_cfg.CHIP, "gio_connections.json")
OUT_PLAN = os.path.join(_cfg.CHIP, "signal_routing_plan.json")

# hdl/tr_1um_3wire_SPI.v's pad table.  "HIZ" is what drives that pad's HIZ pin:
# "VDD"/"GND" are hard ties, anything else is a net name.
PAD_MAP = {
    1:  {"role": "SCLK",    "P": "sclk",        "HIZ": "VDD"},
    2:  {"role": "SDIO",    "P": "sdio_in",     "OUT": "sdio_out", "HIZ": "sdio_oe_n"},  # core port
    3:  {"role": "CS",      "P": "cs_n",        "HIZ": "VDD"},
    4:  {"role": "TEST",    "OUT": "byte_end",  "HIZ": "GND"},
    5:  {"role": "DIS",     "P": "dis",         "HIZ": "VDD"},
    6:  {"role": "DATA[0]", "P": "tx_data[0]",  "OUT": "rx_data[0]", "HIZ": "dis"},
    7:  {"role": "DATA[1]", "P": "tx_data[1]",  "OUT": "rx_data[1]", "HIZ": "dis"},
    9:  {"role": "DATA[2]", "P": "tx_data[2]",  "OUT": "rx_data[2]", "HIZ": "dis"},
    10: {"role": "DATA[3]", "P": "tx_data[3]",  "OUT": "rx_data[3]", "HIZ": "dis"},
    11: {"role": "DATA[4]", "P": "tx_data[4]",  "OUT": "rx_data[4]", "HIZ": "dis"},
    12: {"role": "DATA[5]", "P": "tx_data[5]",  "OUT": "rx_data[5]", "HIZ": "dis"},
    13: {"role": "DATA[6]", "P": "tx_data[6]",  "OUT": "rx_data[6]", "HIZ": "dis"},
    14: {"role": "DATA[7]", "P": "tx_data[7]",  "OUT": "rx_data[7]", "HIZ": "dis"},
    15: {"role": "RSTN",    "P": "rstn",        "HIZ": "VDD"},
}

# Nets that exist at chip level but are not core ports.  Empty: every chip-level
# net is a core port or a hard tie, so the layout is the pad ring plus the core
# and nothing else.
DERIVED_NETS = {}

# Core outputs with no pad.  data_oe is genuinely redundant at chip level: it is
# ~dis, and every pad that would use it takes HIZ off the DIS pad net instead.
EXPECTED_UNCONNECTED = {"data_oe"}

POWER_NETS = {"VDD", "GND"}

PIN_TEXT_LAYER = (49, 0)
PIN_SHAPE_LAYER = (49, 1)


def core_pins(gds, cell):
    """{port: {"x","y","edge","layer","box"}} in the core's own coordinates."""
    ly = db.Layout()
    ly.read(gds)
    u = ly.dbu
    c = ly.cell(cell)
    if c is None:
        raise SystemExit(f"{cell} not found in {gds}")
    bbox = c.bbox()
    shapes = db.Region(c.begin_shapes_rec(ly.layer(*PIN_SHAPE_LAYER)))
    shapes.merge()
    out = {}
    it = c.begin_shapes_rec(ly.layer(*PIN_TEXT_LAYER))
    while not it.at_end():
        s = it.shape()
        if s.is_text():
            name = s.text.string
            if name not in POWER_NETS:
                t = s.text.transformed(it.trans())
                hit = shapes.interacting(db.Region(db.Box(t.x - 1, t.y - 1, t.x + 1, t.y + 1)))
                if hit.is_empty():
                    raise SystemExit(f"core pin label {name} sits on no {PIN_SHAPE_LAYER} shape")
                b = hit.bbox()
                mid_y = (b.bottom + b.top) / 2
                out[name] = {
                    "x": round((b.left + b.right) / 2 * u, 2),
                    "y": round(mid_y * u, 2),
                    "edge": "BOTTOM" if mid_y - bbox.bottom < bbox.top - mid_y else "TOP",
                    "layer": "M2",
                    "box": [round(b.left * u, 2), round(b.bottom * u, 2),
                            round(b.right * u, 2), round(b.top * u, 2)],
                }
        it.next()
    return out


def netlist_ports(path):
    text = open(path).read()
    ports = {}
    for m in re.finditer(
        r"^\s*(input|output|inout)\s+(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+)?([A-Za-z_]\w*)\s*;",
        text, re.M):
        d, hi, lo, name = m.groups()
        if hi is None:
            ports[name] = d
        else:
            for i in range(min(int(hi), int(lo)), max(int(hi), int(lo)) + 1):
                ports[f"{name}[{i}]"] = d
    return ports


def shift(pin, ox, oy):
    return {
        "x": round(pin["x"] + ox, 2), "y": round(pin["y"] + oy, 2),
        "edge": pin["edge"], "layer": pin["layer"],
        "box": [round(pin["box"][0] + ox, 2), round(pin["box"][1] + oy, 2),
                round(pin["box"][2] + ox, 2), round(pin["box"][3] + oy, 2)],
    }


def build(core_gds):
    geom = _cfg.chip_geometry(core_gds)
    ox, oy = geom["core_offset"]
    pads = frame_pins.load()
    cpins = core_pins(core_gds, _cfg.TOP_CELL_NAME)
    ports = netlist_ports(_cfg.NET_PATH)

    missing = sorted(set(ports) - set(cpins))
    if missing:
        raise SystemExit(f"netlist ports with no pin in the routed core: {missing}")

    # ---- who claims each core port -------------------------------------
    claimed, problems = {}, []
    for n, spec in sorted(PAD_MAP.items()):
        for kind in ("P", "OUT"):
            net = spec.get(kind)
            if net is None:
                continue
            if net not in ports:
                problems.append(f"pad {n}'s {kind} names {net!r}, which is not a core port")
            claimed.setdefault(net, []).append(f"{kind}{n}")
        hiz = spec["HIZ"]
        if hiz not in POWER_NETS:
            claimed.setdefault(hiz, []).append(f"HIZ{n}")
    for net, spec in DERIVED_NETS.items():
        claimed.setdefault(spec["from"], []).append(spec["instance"] + ".A")
    for net, where in claimed.items():
        drivers = [w for w in where if w.startswith("P")]
        if net in ports and ports[net] == "input" and len(drivers) > 1:
            problems.append(f"core input {net} is driven from {drivers}")
    unconnected = sorted(p for p in ports if p not in claimed and ports[p] == "output")
    if set(unconnected) != EXPECTED_UNCONNECTED:
        problems.append(f"unconnected core outputs {unconnected} != expected "
                        f"{sorted(EXPECTED_UNCONNECTED)}")

    # ---- nets ------------------------------------------------------------
    nets = {}

    def add_terminal(net, term):
        nets.setdefault(net, {"core": None, "gio": [], "kind": "signal", "notes": []})
        p = pads[term]
        nets[net]["gio"].append({"terminal": term, "x": p["x"], "y": p["y"],
                                 "edge": p["edge"], "layer": p["layer"], "box": p["box"]})

    for n, spec in sorted(PAD_MAP.items()):
        for kind in ("P", "OUT"):
            if spec.get(kind):
                add_terminal(spec[kind], f"{kind}{n}")
        if spec["HIZ"] not in POWER_NETS:
            add_terminal(spec["HIZ"], f"HIZ{n}")

    for net in nets:
        if net in cpins:
            nets[net]["core"] = shift(cpins[net], ox, oy)
            nets[net]["core"]["port"] = net
            nets[net]["core_native"] = cpins[net]
        elif net in DERIVED_NETS:
            src = DERIVED_NETS[net]
            nets[net]["kind"] = "derived"
            nets[net]["driver"] = {"cell": src["cell"], "instance": src["instance"],
                                   "input_net": src["from"], "placement": "NOT PLACED YET"}
            nets[net]["notes"].append(src["why"])
        else:
            problems.append(f"net {net!r} has no core pin and no derived-net entry")

    # the inverter's own input still has to reach it from the core pin
    for net, spec in DERIVED_NETS.items():
        src = spec["from"]
        nets.setdefault(src, {"core": None, "gio": [], "kind": "signal", "notes": []})
        if nets[src]["core"] is None and src in cpins:
            nets[src]["core"] = shift(cpins[src], ox, oy)
            nets[src]["core"]["port"] = src
            nets[src]["core_native"] = cpins[src]
        nets[src]["sinks"] = [{"cell": spec["cell"], "instance": spec["instance"], "pin": "A"}]
        nets[src]["notes"].append(f"drives {spec['instance']}.A, whose Y is {net}")

    ties = {f"HIZ{n}": spec["HIZ"] for n, spec in sorted(PAD_MAP.items())
            if spec["HIZ"] in POWER_NETS}
    # a pad with no OUT net is input-only; its OUT pin is a don't-care
    dontcare = sorted(f"OUT{n}" for n, spec in PAD_MAP.items() if not spec.get("OUT"))

    conn = {
        "_meta": {
            "description": "OSS_FRAME_GIO pad ring <-> spi_slave_sclk_nrow_fm core "
                           "connection map for tr_1um_3wire_SPI.",
            "generated_by": "scripts/gen_top_routing_plan.py -- do not edit by hand",
            "pad_assignment_source": "hdl/tr_1um_3wire_SPI.v (design_notes.md section 2)",
            "pad_geometry_source": os.path.relpath(_cfg.FRAME_GDS, _cfg.ROOT),
            "core_geometry_source": os.path.relpath(core_gds, _cfg.ROOT),
            "pad_cell_behavior": {
                "cell": "OSS_ESD_5V_DIO(VDD, PAD, VSS, HIZ, OUT), one per pad",
                "HIZ": "1 => driver off, pad Hi-Z / input only.  0 => driver on, "
                       "pad driven by OUT.  Established by the I2C project from a "
                       "transistor-level trace of the pad cell, not from pin names.",
                "OUT": "value driven onto the pad while HIZ is low; don't-care otherwise",
            },
            "dis_chain": "The eight DATA pads take HIZ straight off the DIS pad net "
                         "(P5).  The core drives DATA when data_oe = ~dis = 1, i.e. "
                         "when dis = 0, and HIZ = 0 means driven -- so HIZ = dis is "
                         "exactly right and needs no logic.  Same trick the I2C chip "
                         "used for its own DIS pad.",
        },
        "chip_geometry": geom,
        "core_ports": {d: sorted(p for p in ports if ports[p] == d)
                       for d in ("input", "output")},
        "frame_pads": sorted(PAD_MAP),
        "pad_pin_coords": {k: {kk: v[kk] for kk in ("x", "y", "edge", "layer")}
                           for k, v in sorted(pads.items())},
        "connections": {f"P{n}": dict(spec) for n, spec in sorted(PAD_MAP.items())},
        "power_ties": ties,
        "dont_care_pins": dontcare,
        "core_outputs_not_connected_to_any_pad": unconnected,
        "derived_nets": DERIVED_NETS,
        "unresolved": [],
    }

    plan = {
        "_meta": {
            "description": "Physical endpoints for the chip-level router: every net "
                           "that crosses between the core and the GIO ring.",
            "generated_by": "scripts/gen_top_routing_plan.py -- do not edit by hand",
            "core_offset": geom["core_offset"],
            "core_chip_bbox": geom["core_chip_bbox"],
            "channels_to_wall": {s: geom["channel_" + s][0]
                                 for s in ("top", "bottom", "left", "right")},
            "gio_pin_radius": _cfg.GIO_PIN_RADIUS,
            "coordinates": "chip frame, um.  core/core_native are the same pin in "
                           "chip and core-local coordinates.",
        },
        "nets": {k: nets[k] for k in sorted(nets)},
        "power_ties": ties,
    }

    stats = {
        "nets": len(nets),
        "terminals": sum(len(v["gio"]) for v in nets.values()),
        "core_pins_used": sum(1 for v in nets.values() if v["core"]),
        "multi_terminal": {k: [t["terminal"] for t in v["gio"]]
                           for k, v in nets.items() if len(v["gio"]) > 1},
    }
    return conn, plan, stats, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core-gds", default=_cfg.SQUEEZED_GDS)
    args = ap.parse_args()

    conn, plan, stats, problems = build(args.core_gds)
    os.makedirs(_cfg.CHIP, exist_ok=True)
    for path, data in ((OUT_CONN, conn), (OUT_PLAN, plan)):
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        print(f"wrote {path}")

    print(f"\n{stats['nets']} net(s), {stats['terminals']} GIO terminal(s), "
          f"{stats['core_pins_used']} core pin(s)")
    for net, terms in sorted(stats["multi_terminal"].items()):
        print(f"  {net}: {len(terms)} terminals -> {terms}")
    print(f"power ties: {conn['power_ties']}")
    print(f"core outputs with no pad: {conn['core_outputs_not_connected_to_any_pad']}")
    for u in conn["unresolved"]:
        print("\nUNRESOLVED: " + u)
    if problems:
        print()
        for p in problems:
            print("  PROBLEM: " + p)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
