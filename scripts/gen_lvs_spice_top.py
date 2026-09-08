#!/usr/bin/env python3
"""gen_lvs_spice_top.py -- the CHIP-level LVS reference netlist.

    layout/spi_slave_sclk_nrow_fm.spice   (core, LVS-clean on its own)
  + lef/OSS_FRAME_GIO.spice               (the pad ring, transistor level)
  + layout/chip/gio_connections.json      (what connects to what)
  -> layout/chip/tr_1um_3wire_SPI.spice   (and a copy beside the cell exports)

Two subcircuit instances and a port list, which is all the chip is:

    .subckt tr_1um_3wire_SPI P1 P2 P3 P4 P5 P6 P7 VSS P9 P10 P11 P12 P13 P14 P15 VDD
    x1  ... OSS_FRAME_GIO
    x2  ... spi_slave_sclk_nrow_fm
    .ends

Sixteen ports because the layout has sixteen (scripts/add_top_pins.py put a
pin on each bond pad).  KLayout's cross-reference needs the reference and the
layout to agree on that COUNT before it will even attempt the graph match --
the I2C project dropped two pins from this list once and watched every other
pin cascade into a spurious mismatch, with all 27 sub-circuits still matching
cleanly underneath.

WHERE EACH NET COMES FROM
-------------------------
Both instances' port orders are read from their own `.subckt` lines, never
assumed.  The nets are then derived from the connection map, not listed here:

  P<n>       the bond pad net, which is a top-level port
  HIZ<n>     whatever drives it -- a rail, the core's sdio_oe_n, or the DIS
             pad net (the eight DATA pads take HIZ straight off P5)
  OUT<n>     the core output feeding that pad's driver; for an input-only pad
             whose OUT would otherwise float, the ground rail, matching the
             tie-off the router drew
  core port  the pad net it reaches, or its own name for a core-to-ring
             internal net (rx_data[i], byte_end, sdio_out, sdio_oe_n)

A port on either side that the map does not account for becomes its own unique
NC_* net, so LVS sees it floating exactly as the silicon has it, rather than
silently shorting several floating pins together.

  usage:  scripts/gen_lvs_spice_top.py [-o OUT]
"""
import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg
import gen_lvs_spice as _core          # for SIM_DIR

CORE_SPICE = os.path.join(_cfg.LAYOUT, _cfg.TOP_CELL_NAME + ".spice")
GIO_SPICE = os.path.join(_cfg.ROOT, "lef", "OSS_FRAME_GIO.spice")
CONN = os.path.join(_cfg.CHIP, "gio_connections.json")
OUT_PATH = os.path.join(_cfg.CHIP, _cfg.CHIP_TOP_CELL + ".spice")

GIO_CELL = "OSS_FRAME_GIO"

# The frame's ground pin is VSS; the core's is GND.  One net, and the chip-level
# name is the frame's.
RAIL = {"VDD": "VDD", "GND": "VSS", "VSS": "VSS"}

# Physical order round the ring, the same list the I2C chip declared.
TOP_PIN_ORDER = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "VSS",
                 "P9", "P10", "P11", "P12", "P13", "P14", "P15", "VDD"]


def subckt_ports(path, name):
    lines = open(path).read().splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(f".subckt {name} "):
            toks = line.split()[2:]
            j = i + 1
            while j < len(lines) and lines[j].startswith("+"):
                toks += lines[j][1:].split()
                j += 1
            return toks
    raise SystemExit(f".subckt {name} not found in {path}")


def gio_source():
    """Prefer the repo's copy; fall back to xschem's export."""
    if os.path.exists(GIO_SPICE):
        return GIO_SPICE
    if _core.SIM_DIR:
        p = os.path.join(_core.SIM_DIR, GIO_CELL + ".spice")
        if os.path.exists(p):
            return p
    raise SystemExit(f"{GIO_CELL}.spice not found -- looked in {GIO_SPICE} "
                     f"and {_core.SIM_DIR}")


def build():
    conn = json.load(open(CONN))
    padmap = {int(k[1:]): v for k, v in conn["connections"].items()}
    gio_path = gio_source()
    gio_ports = subckt_ports(gio_path, GIO_CELL)
    core_ports = subckt_ports(CORE_SPICE, _cfg.TOP_CELL_NAME)

    nc = []

    def floating(label):
        nc.append(f"NC_{label}")
        return nc[-1]

    def net_of_source(src):
        """What the connection map's HIZ/OUT entry names, as a chip-level net."""
        if src in RAIL:
            return RAIL[src]
        # a core port: the DIS pad net is the pad itself, everything else is
        # an internal core-to-ring net that keeps the port's own name
        for n, spec in padmap.items():
            if spec.get("P") == src:
                return f"P{n}"
        return src

    # ---- GIO side ---------------------------------------------------------
    gio_net, problems = {}, []
    for p in gio_ports:
        if p in RAIL:
            gio_net[p] = RAIL[p]
            continue
        m = re.match(r"^(P|HIZ|OUT)(\d+)$", p)
        if not m:
            problems.append(f"unrecognised {GIO_CELL} port {p!r}")
            continue
        kind, n = m.group(1), int(m.group(2))
        spec = padmap.get(n)
        if spec is None:
            problems.append(f"{GIO_CELL} port {p!r} has no pad {n} in the map")
            continue
        if kind == "P":
            gio_net[p] = f"P{n}"
        elif kind == "HIZ":
            gio_net[p] = net_of_source(spec["HIZ"])
        else:
            gio_net[p] = net_of_source(spec["OUT"]) if spec.get("OUT") \
                else (RAIL["GND"] if p in conn["dont_care_pins"] else floating(p))

    # ---- core side --------------------------------------------------------
    core_net, unconnected = {}, []
    claim = {}
    for n, spec in padmap.items():
        if spec.get("P"):
            claim[spec["P"]] = f"P{n}"
        if spec.get("OUT"):
            claim[spec["OUT"]] = spec["OUT"]
        if spec["HIZ"] not in RAIL:
            claim.setdefault(spec["HIZ"], spec["HIZ"])
    for p in core_ports:
        if p in RAIL:
            core_net[p] = RAIL[p]
        elif p in claim:
            core_net[p] = claim[p]
        else:
            core_net[p] = floating(f"CORE_{p}")
            unconnected.append(p)

    expected_unconnected = set(conn["core_outputs_not_connected_to_any_pad"])
    if set(unconnected) != expected_unconnected:
        problems.append(f"core ports left floating {sorted(unconnected)} != "
                        f"the map's own list {sorted(expected_unconnected)}")

    # ---- cross-checks -----------------------------------------------------
    for pad in TOP_PIN_ORDER:
        if pad not in RAIL and gio_net.get(pad) != pad:
            problems.append(f"top pin {pad} carries net {gio_net.get(pad)!r}")
    if len(TOP_PIN_ORDER) != len(padmap) + 2:
        problems.append(f"{len(TOP_PIN_ORDER)} top pins vs {len(padmap)} pads + 2 rails")
    # every net the core drives onto a pad driver must be the same net the
    # frame's OUT pin sees, and vice versa
    for n, spec in sorted(padmap.items()):
        if spec.get("OUT"):
            a, b = core_net.get(spec["OUT"]), gio_net.get(f"OUT{n}")
            if a != b:
                problems.append(f"pad {n}: core {spec['OUT']}={a!r} but OUT{n}={b!r}")
        if spec.get("P"):
            a, b = core_net.get(spec["P"]), gio_net.get(f"P{n}")
            if a != b:
                problems.append(f"pad {n}: core {spec['P']}={a!r} but P{n}={b!r}")
        if spec["HIZ"] not in RAIL:
            a, b = core_net.get(spec["HIZ"]), gio_net.get(f"HIZ{n}")
            if a is not None and a != b:
                problems.append(f"pad {n}: core {spec['HIZ']}={a!r} but HIZ{n}={b!r}")

    gio_body = open(gio_path).read().rstrip("\n")
    core_body = open(CORE_SPICE).read().rstrip("\n")
    names = lambda t: set(re.findall(r"^\.subckt\s+(\S+)", t, re.M))
    clash = names(gio_body) & names(core_body)
    if clash:
        problems.append(f"subckt name collision between the two bodies: {sorted(clash)}")

    return (conn, gio_path, gio_ports, core_ports, gio_net, core_net,
            gio_body, core_body, nc, unconnected, problems)


def wrap(inst, nets, cell, per=8):
    out = [f"{inst} " + " ".join(nets[:per])]
    rest = nets[per:]
    while rest:
        out.append("+ " + " ".join(rest[:per]))
        rest = rest[per:]
    out[-1] += f" {cell}"
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=OUT_PATH)
    ap.add_argument("--no-sim-out", action="store_true")
    args = ap.parse_args()

    (conn, gio_path, gio_ports, core_ports, gio_net, core_net,
     gio_body, core_body, nc, unconnected, problems) = build()

    header = [
        f"** {os.path.basename(args.out)} -- chip-level LVS reference netlist.",
        "** GENERATED by scripts/gen_lvs_spice_top.py; do not edit by hand.",
        f"**   core  : {os.path.relpath(CORE_SPICE, _cfg.ROOT)}",
        f"**   frame : {os.path.relpath(gio_path, _cfg.ROOT) if gio_path.startswith(_cfg.ROOT) else gio_path}",
        f"**   map   : {os.path.relpath(CONN, _cfg.ROOT)}",
        "**",
        "** x1 = OSS_FRAME_GIO, x2 = " + _cfg.TOP_CELL_NAME + ".  A net named NC_*",
        "** is genuinely unconnected on both sides and gets its own unique name,",
        "** so LVS sees each one floating on its own as the silicon has it.",
        "** The top subckt declares all 16 bond-pad ports (P1-P7, VSS, P9-P15,",
        "** VDD -- P8 does not exist in this frame), matching the pin markers",
        "** scripts/add_top_pins.py placed in the layout.  The counts have to",
        "** agree or KLayout will not attempt the match at all.",
    ]
    lines = header + ["", gio_body, "", core_body, "",
                      f".subckt {_cfg.CHIP_TOP_CELL} " + " ".join(TOP_PIN_ORDER),
                      wrap("x1", [gio_net[p] for p in gio_ports], GIO_CELL),
                      wrap("x2", [core_net[p] for p in core_ports], _cfg.TOP_CELL_NAME),
                      ".ends", ""]
    content = "\n".join(lines)

    outs = [args.out]
    if _core.SIM_DIR and not args.no_sim_out:
        outs.append(os.path.join(_core.SIM_DIR, _cfg.CHIP_TOP_CELL + ".spice"))
    for path in outs:
        with open(path, "w") as f:
            f.write(re.sub(r"^\*\* \S+ --", f"** {os.path.basename(path)} --",
                           content, count=1))
        print(f"wrote {path}")

    print(f"\n{GIO_CELL}: {len(gio_ports)} port(s)")
    print(f"{_cfg.TOP_CELL_NAME}: {len(core_ports)} port(s)")
    print(f"top subckt: {len(TOP_PIN_ORDER)} bond-pad port(s)")
    print("\ncore port -> chip net")
    for p in core_ports:
        print(f"  {p:<12} {core_net[p]}")
    print("\nframe pin -> chip net (non-pad only)")
    for p in gio_ports:
        if not re.match(r"^P\d+$", p) and p not in RAIL:
            print(f"  {p:<8} {gio_net[p]}")
    if nc:
        print(f"\n{len(nc)} floating net(s): {nc}")
    if problems:
        print()
        for p in problems:
            print("  PROBLEM: " + p)
        raise SystemExit(f"{len(problems)} problem(s)")


if __name__ == "__main__":
    main()
