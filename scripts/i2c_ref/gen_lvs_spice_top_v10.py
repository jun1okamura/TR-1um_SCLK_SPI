"""
gen_lvs_spice_top_v10.py (this session, user request: "LVS用の spice を
作ってください" -- build the CHIP-LEVEL (core + GIO frame) LVS reference
netlist for V10, now that the V10 core's own LVS-clean netlist exists
(gen_lvs_spice_v10.py, confirmed clean design_notes.md 108.51) and the
V10 chip-level pad reassignment/routing is fixed and DRC/overlap-verified
(108.55-108.57).

V10 counterpart of gen_lvs_spice_top_v9.py. Structurally identical
assembly (x1=OSS_FRAME_GIO, x2=core, 16 real top-level bond-pad pins
P1-P7,VSS,P9-P15,VDD declared as actual subckt ports, same NC_* floating-
net convention for genuinely unconnected pins/ports) -- the only real
difference is WHERE the GIO<->core connection map comes from.

schematic/gio_connections.json's own `connections_per_terminal_detail`
(43 GIO terminals) is still the BASE map, exactly as v9's script uses it
verbatim: it is mostly frame-structural information that does NOT depend
on which core signal ends up on which bit-pair pad -- spare pads (P7/P9/
P10), VDD/VSS ties, the DIS direction-control chain's HIZ lines, the
fixed Hi-Z-tied HIZ1/HIZ7/HIZ15, OUT2's permanent VSS tie, and RING_OSC's
own OUT9/OUT10 driver-input pins are all unchanged between V9 and V10
(same physical frame, same 20 fixed terminal positions reused verbatim by
route_gio_core_v10.py/assign_v10_gio_pads.py -- design_notes.md 108.55/
108.57).

The ONLY terminals that actually differ are the 16 P<n>/OUT<n> positions
belonging to the 8 tx_data[i]/rx_data[i] bit-pair pads (pad numbers 3, 4,
5, 6, 11, 12, 13, 14) plus, for robustness (not because the values
happen to differ), P1/P15 (scl/rst_n) and P2/HIZ2 (sda_in/sda_oe) -- all
20 are overridden from schematic/v10_signal_routing_plan.json's own
per-net "terminal" field (read programmatically -- the whole point of
108.57 was fixing an earlier version of this exact assignment that
WASN'T terminal-type/pad-pairing aware, so this script deliberately does
not assume the override is a no-op anywhere, even where the optimized
V10 result happens to coincide with V9's historical choice, e.g. P1=scl/
P15=rst_n). Everything else in `connections_per_terminal_detail` is left
exactly as gio_connections.json already has it.
"""
import json
import re
from pathlib import Path
import os

BASE = str(Path(__file__).resolve().parent.parent)
_XSCHEM_SIM_DIR = os.environ.get("XSCHEM_SIM_DIR", str(Path.home() / ".xschem" / "simulations"))
GIO_SPICE = _XSCHEM_SIM_DIR + "/OSS_FRAME_GIO.spice"
CORE_SPICE = BASE + "/schematic/i2c_slave_async_nrow_fm_v10.spice"
CONN_JSON = BASE + "/schematic/gio_connections.json"
V10_PLAN_JSON = BASE + "/schematic/v10_signal_routing_plan.json"
OUT_SCHEMATIC = BASE + "/schematic/tr_1um_i2c_slave_async_v10_lvs.spice"
OUT_SIMULATIONS = _XSCHEM_SIM_DIR + "/tr_1um_i2c_slave_async_v10_core_gio_only.spice"

TOP_NAME = "tr_1um_i2c_slave_async"


def read_subckt_port_order(path, subckt_name):
    text = open(path).read()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(f".subckt {subckt_name} ") or line.strip() == f".subckt {subckt_name}":
            tokens = line.split()[2:]
            j = i + 1
            while j < len(lines) and lines[j].startswith("+"):
                tokens += lines[j][1:].split()
                j += 1
            return tokens
    raise RuntimeError(f"'.subckt {subckt_name}' not found in {path}")


def load_body_verbatim(path):
    return open(path).read().rstrip("\n")


def build_v10_terminal_detail(base_detail, v10_plan_path):
    """Deep-copy gio_connections.json's connections_per_terminal_detail
    and override exactly the 20 terminals that v10_signal_routing_plan.
    json's own nets cover, using each net's "terminal" field. See module
    docstring for why this is safe: everything else in the 43-terminal
    map is frame-structural and version-independent."""
    detail = {k: dict(v) if isinstance(v, dict) else v for k, v in base_detail.items()}
    plan = json.load(open(v10_plan_path))
    terminal_to_net = {}
    for net_name, entry in plan["nets"].items():
        terminal = entry["terminal"]
        if terminal in terminal_to_net:
            raise RuntimeError(f"v10_signal_routing_plan.json: terminal {terminal!r} "
                                f"claimed by both {terminal_to_net[terminal]!r} and {net_name!r}")
        terminal_to_net[terminal] = net_name
    print(f"V10 terminal overrides from {v10_plan_path} ({len(terminal_to_net)} terminals):")
    for terminal, net_name in sorted(terminal_to_net.items()):
        old = detail.get(terminal, {}).get("core_signal")
        tag = "unchanged" if old == net_name else f"CHANGED from {old!r}"
        print(f"   {terminal:6} -> core_signal={net_name!r}  ({tag})")
        if terminal not in detail:
            detail[terminal] = {}
        detail[terminal]["core_signal"] = net_name
    return detail


def main():
    conn = json.load(open(CONN_JSON))
    detail = build_v10_terminal_detail(conn["connections_per_terminal_detail"], V10_PLAN_JSON)

    gio_ports = read_subckt_port_order(GIO_SPICE, "OSS_FRAME_GIO")
    core_ports = read_subckt_port_order(CORE_SPICE, "i2c_slave_async_nrow_fm")
    print(f"\nOSS_FRAME_GIO port order ({len(gio_ports)} ports), read from {GIO_SPICE}:")
    print(" ", gio_ports)
    print(f"i2c_slave_async_nrow_fm port order ({len(core_ports)} ports), read from {CORE_SPICE}:")
    print(" ", core_ports)

    # ---- build GIO-side port -> net map (same logic as gen_lvs_spice_top_v9.py) ----
    gio_net = {}
    nc_count = [0]

    def nc(label):
        nc_count[0] += 1
        return f"NC_{label}"

    for pin in gio_ports:
        if pin == "VDD":
            gio_net[pin] = "VDD"
            continue
        if pin == "VSS":
            gio_net[pin] = "VSS"
            continue
        info = detail.get(pin)
        if info is None:
            raise RuntimeError(f"GIO pin {pin!r} has no entry in connections_per_terminal_detail")
        net = info.get("net")
        gio_net[pin] = net if net is not None else nc(pin)

    # ---- build core-side port -> net map ----
    core_net = {}
    core_net["VDD"] = "VDD"
    core_net["GND"] = "VSS"

    signal_to_gio_pin = {}
    for pin, info in detail.items():
        sig = info.get("core_signal") if isinstance(info, dict) else None
        if sig:
            if sig in signal_to_gio_pin:
                raise RuntimeError(f"core_signal {sig!r} claimed by both terminal "
                                    f"{signal_to_gio_pin[sig]!r} and {pin!r}")
            signal_to_gio_pin[sig] = pin

    unconnected_core_ports = []
    for port in core_ports:
        if port in ("VDD", "GND"):
            continue
        if port in signal_to_gio_pin:
            core_net[port] = gio_net[signal_to_gio_pin[port]]
        else:
            core_net[port] = nc(f"CORE_{port}")
            unconnected_core_ports.append(port)

    print("\nCore ports with NO GIO pad connection (net left floating, unique NC_* name):")
    for p in unconnected_core_ports:
        print("  ", p, "->", core_net[p])
    print("\nGIO pins with NO connection (net left floating, unique NC_* name):")
    for pin in gio_ports:
        if gio_net[pin].startswith("NC_"):
            print("  ", pin, "->", gio_net[pin])

    # ---- TOP-LEVEL CHIP PINS ----
    # 108.60 REVERTED (108.61): dropping P9/P10 from TOP_PIN_ORDER was
    # tried per the user's own instruction, but the real re-run
    # (layout/step10/LVS_error.lvsdb, 15:46) showed this made things
    # dramatically WORSE -- the top circuit itself went to NoMatch and
    # EVERY other pin (P1-P15, VSS) started reporting spurious "port
    # mismatch '$NN' vs 'PX'" errors, even though all 27 non-top
    # circuits (OSS_FRAME_GIO, i2c_slave_async_nrow_fm, RING_OSC, every
    # library cell) still matched cleanly. Root cause: the LAYOUT's own
    # extracted top cell genuinely has 16 physical top-level pins/ports
    # (P1-P7, VSS, P9-P15, VDD -- confirmed via real GDS PIN labels,
    # unlike OUT1/OUT7/OUT15 or the HIZ<n> lines, which are NEVER
    # exposed as top-level chip pins at all, only internal-to-GIO nets).
    # KLayout's netlist cross-reference requires the reference subckt's
    # port COUNT to equal the layout-extracted port count for the graph
    # isomorphism to even be attempted; a mismatched count (14 vs 16)
    # doesn't just fail on the 2 missing pins, it invalidates the WHOLE
    # top-level net-correspondence, which is why every other declared
    # pin cascaded into a false mismatch too. So P9/P10 belong back in
    # TOP_PIN_ORDER -- the original "physical connection is not made to
    # the subcircuit" complaint (108.59/108.60) has a different real
    # cause that has NOT yet been found (see design_notes.md 108.61
    # investigation notes) and must be fixed some other way, not by
    # removing them from this list.
    TOP_PIN_ORDER = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "VSS",
                      "P9", "P10", "P11", "P12", "P13", "P14", "P15", "VDD"]
    rename = {}
    for pad in TOP_PIN_ORDER:
        if pad in ("VDD", "VSS"):
            continue
        old_name = gio_net[pad]
        rename[old_name] = pad
    print(f"\nTop-pin net renames ({len(rename)}):")
    for old, new in rename.items():
        print(f"   {old:10} -> {new}")
    for pin in gio_net:
        gio_net[pin] = rename.get(gio_net[pin], gio_net[pin])
    for port in core_net:
        core_net[port] = rename.get(core_net[port], core_net[port])

    assert len(core_net) == len(core_ports), (len(core_net), len(core_ports))
    assert len(gio_net) == len(gio_ports), (len(gio_net), len(gio_ports))
    assert all(p in core_net for p in core_ports)
    assert all(p in gio_net for p in gio_ports)

    # sanity cross-check: every V10-reassigned net must actually have
    # landed on its expected pin (catches a stale/mismatched plan JSON or
    # a core-port-name drift immediately instead of silently mis-wiring).
    plan = json.load(open(V10_PLAN_JSON))
    mismatches = []
    for net_name, entry in plan["nets"].items():
        terminal = entry["terminal"]
        if net_name not in core_net:
            mismatches.append(f"{net_name!r}: not a core port at all")
            continue
        expected = gio_net[terminal]
        if core_net[net_name] != expected:
            mismatches.append(f"{net_name!r}: core_net={core_net[net_name]!r}, expected {expected!r} (terminal {terminal!r})")
    if mismatches:
        raise RuntimeError("V10 plan cross-check failed:\n  " + "\n  ".join(mismatches))
    print(f"\nV10 plan cross-check: all {len(plan['nets'])} reassigned nets land on their expected pin. OK.")

    x1_nets = [gio_net[p] for p in gio_ports]
    x2_nets = [core_net[p] for p in core_ports]

    gio_body = load_body_verbatim(GIO_SPICE)
    core_body = load_body_verbatim(CORE_SPICE)

    def subckt_names(text):
        return set(re.findall(r"^\.subckt\s+(\S+)", text, re.MULTILINE))

    gio_names = subckt_names(gio_body)
    core_names = subckt_names(core_body)
    overlap = gio_names & core_names
    if overlap:
        raise RuntimeError(f"subckt name collision between GIO and core bodies: {overlap}")
    print(f"\nNo subckt-name collisions: GIO defines {sorted(gio_names)}, core defines {len(core_names)} cells "
          f"(library cells + top), disjoint confirmed.")

    def wrap_instance_line(inst_name, nets, subckt_name):
        out = [f"{inst_name} " + " ".join(nets[:8])]
        rest = nets[8:]
        while rest:
            out.append("+ " + " ".join(rest[:8]))
            rest = rest[8:]
        out[-1] += f" {subckt_name}"
        return "\n".join(out)

    x1_line = wrap_instance_line("x1", x1_nets, "OSS_FRAME_GIO")
    x2_line = wrap_instance_line("x2", x2_nets, "i2c_slave_async_nrow_fm")

    header = f"""\
** {TOP_NAME}_v10_lvs.spice -- chip-level LVS reference netlist (V10,
** core+GIO only -- RING_OSC not yet integrated, see
** gen_lvs_spice_ringosc_v10.py for that).
** Generated by script/gen_lvs_spice_top_v10.py from:
**   1) schematic/i2c_slave_async_nrow_fm_v10.spice (core, LVS-clean per
**      design_notes.md 108.51 -- "V10コアセル単体LVSクリーン達成")
**   2) simulations/OSS_FRAME_GIO.spice (GIO pad-ring frame, transistor
**      level, unchanged from V9 -- same physical frame)
**   3) schematic/gio_connections.json (base GIO<->core connection map,
**      derived + cross-verified design_notes.md 78.1/78.4 for V9) with
**      the 20 signal-net terminals OVERRIDDEN from
**      schematic/v10_signal_routing_plan.json's own per-net "terminal"
**      field (V10's pad-pairing-aware reassignment, design_notes.md
**      108.57) -- see this script's own module docstring for exactly
**      which terminals differ from V9's assignment and why the rest of
**      the 43-terminal map is safely reused verbatim.
** x1 = OSS_FRAME_GIO, x2 = i2c_slave_async_nrow_fm. Nets prefixed NC_
** are genuinely unconnected on both sides -- each gets its own unique
** floating net name so LVS sees them as independently floating,
** matching the real chip.
** Top subckt declares its 16 real bond-pad ports explicitly (P1-P7,
** VSS, P9-P15, VDD -- P8 does not exist in this frame), matching v9's
** own top-level-pin fix (design_notes.md 79.13).
"""

    body_parts = [
        header,
        gio_body,
        "",
        core_body,
        "",
        f".subckt {TOP_NAME} " + " ".join(TOP_PIN_ORDER),
        x1_line,
        x2_line,
        ".ends",
        "",
    ]
    out_text = "\n".join(body_parts)

    open(OUT_SCHEMATIC, "w").write(out_text)
    open(OUT_SIMULATIONS, "w").write(out_text)
    print(f"\nwrote {OUT_SCHEMATIC}")
    print(f"wrote {OUT_SIMULATIONS}")
    print(f"\nx1 (OSS_FRAME_GIO) instance: {len(x1_nets)} nets")
    print(f"x2 (i2c_slave_async_nrow_fm) instance: {len(x2_nets)} nets")
    print(f"Total NC_* (floating) nets created: {nc_count[0]}")


if __name__ == "__main__":
    main()
