"""
gen_lvs_spice_v9.py (this session, user request "2. LVS用の .spice
ファイルを作成ください")

Generates a flat, gate-level structural SPICE netlist for v9's core
(top cell "i2c_slave_async_nrow_fm", matching the GDS TOP_CELL_NAME
every layout script in this project uses) directly from
`src/i2c_slave_async_net_v9_rowbuf.v`, WITHOUT going through xschem.

Why not xschem (the project's established path, see design_notes.md
71.x/77.x -- a `.sch` is generated, the user opens it in their local
xschem, and manually exports `~/.xschem/simulations/<name>.spice`):
this sandbox has no xschem/klayout CLI (design_notes 76/77.43), and a
flat instance-call SPICE netlist (".subckt ... / x_name pin... TYPE /
.ends") is a fully deterministic, mechanical translation of the
gate-level Verilog netlist plus each library cell's own SPICE pin
order -- there is nothing xschem-specific about its CONTENT, only
about how it's normally produced in this project's workflow. This
script produces byte-for-byte the same kind of file (verified against
the existing v7/v8-era precedent, `simulations/i2c_slave_async_nrow_fm.
spice`, format/PININFO/instance-line style matched exactly) directly
and deterministically.

Important discovered wrinkles (both confirmed by direct inspection of
v9_rowbuf.v, not assumed):

1. **Most instances omit .VDD()/.GND() in the Verilog netlist itself**
   (only BUFTH/BUF_X1/DEL1 and 3 of the 6 INV_X1/NOR2 instances
   include them explicitly -- 138 instances total, only ~13 have
   explicit power connections in the RTL netlist). This is the
   project's own established convention (route_channels_nrow_fm.py /
   gen_placement_nrow_fm.py deliberately skip POWER/GROUND-use pins
   from net-based signal routing and wire every cell's power pins via
   the separate, always-present TAP2 mesh instead -- see
   design_notes.md and route_channels_nrow_fm.py's own module
   docstring, item 5). For LVS purposes every real device instance
   IS physically powered (every std cell's LEF PIN_META defines VDD/
   GND regardless of whether the RTL netlist text spells it out), so
   this script ties EVERY instance's VDD/GND positional SPICE pins to
   the global "VDD"/"GND" nets unconditionally -- never conditional on
   whether the Verilog textually included them.
2. **Output-port aliasing**: `assign rw = rw_bit;`,
   `assign addr_match = addr_ok;`, `assign rx_data = rx_data_r;` (the
   only 3 non-bookkeeping assigns in v9_rowbuf.v, confirmed via direct
   grep) -- resolved so the emitted SPICE always uses the *port* name
   (rw / addr_match / rx_data[i]), matching the existing precedent
   file's convention (confirmed: its net names are "rw"/"addr_match",
   never "rw_bit"/"addr_ok"). sda_oe needed NO alias -- its driving
   DFFRB's QB pin already connects directly to net "sda_oe" in the
   RTL (confirmed via grep), unlike sda_oe_r (a different, only
   internally-used net from the same flip-flop's Q pin).

Per-cell-type positional SPICE pin order was read directly from each
cell's own library file (`simulations/<TYPE>.spice`'s `.subckt` line)
-- NOT assumed from the Verilog's alphabetical/declaration order.

v3 fix (design_notes.md 77.46, user's real KLayout LVS run): the v1/v2
output only INSTANTIATED library cells (x148 ... AND2_X1) but never
DEFINED their `.subckt AND2_X1 ...` bodies anywhere in the file. Real
KLayout LVS failed immediately: "RuntimeError: Not a valid pin name in
circuit 'AND2_X1' in 'equivalent_pins': A" at 05_Compare.lvs:101 --
`05_Compare.lvs` calls `equivalent_pins("AND2_X1","A","B")` etc.
against the netlist read from Sch_file (this script's own output), and
since AND2_X1 was never defined there, KLayout's SPICE reader silently
auto-created a black-box placeholder circuit for it with NO real named
pins, so pin "A" doesn't exist on it. Root cause confirmed by directly
comparing against the untouched precedent
`simulations/i2c_slave_async_net_v7_routed.spice`: it embeds a full
transistor-level `.subckt ... .ends` body for EVERY library cell used,
inline in the very same file (xschem's default hierarchical-flatten
netlisting behavior) -- this script now does the same, reading each
body directly from `simulations/<TYPE>.spice` (verbatim, transistor-
level, byte-for-byte) rather than re-deriving/assuming content.
"""
import json
import os
import re
from pathlib import Path

# 2026-09-02: made portable (was hardcoded to a Claude-sandbox absolute
# path, broke the first time this chain was run locally on the user's
# own Mac -- see lef_parser.py's LEF_PATH for the same fix). NET_PATH/
# OUT_PATH_PROJECT/PLACEMENT_JSON are relative to this repo; OUT_PATH_SIM/
# LIB_DIR are relative to the user's home (~/.xschem/simulations is
# xschem's fixed netlist-export location, not inside this repo at all).
_REPO_ROOT = Path(__file__).resolve().parent.parent
# XSCHEM_SIM_DIR env var override: Claude's own sandbox mounts
# ~/.xschem/simulations at a path that isn't simply Path.home()/".xschem"
# /"simulations" (it's bind-mounted to .../mnt/simulations directly,
# flattening the ~/.xschem/ prefix) -- Path.home() is still the correct,
# portable default for the user's real Mac.
_XSCHEM_SIM_DIR = Path(os.environ.get("XSCHEM_SIM_DIR", str(Path.home() / ".xschem" / "simulations")))

NET_PATH = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v9_rowbuf.v")
OUT_PATH_PROJECT = str(_REPO_ROOT / "schematic" / "i2c_slave_async_nrow_fm_v9.spice")
OUT_PATH_SIM = str(_XSCHEM_SIM_DIR / "i2c_slave_async_nrow_fm.spice")
LIB_DIR = str(_XSCHEM_SIM_DIR)
PLACEMENT_JSON = str(_REPO_ROOT / "LEF" / "placement_nrow_fm_v9.json")

# FILL2/FILL3 decap devices (design_notes.md 77.47): the user's real
# KLayout LVS run flagged exactly 2 net mismatches, VDD and GND, traced
# via the shared .lvsdb (layout/step8/LVS_error.lvsdb) to 4 devices (2
# PMOS + 2 NMOS after combine_devices merge) present on the LAYOUT side
# with no schematic equivalent -- FILL2/FILL3 physically contain a
# poly-over-active MOS decoupling-cap structure (confirmed via direct
# geometry query: both have shapes on GC/poly layer (8,1) overlapping
# active layer (3,1)/(3,2); TAP2/TAP3 have ZERO (8,1) shapes, ruling
# them out despite 77.44's finding that TAP also lacks an xschem .sch/
# .sym -- that gap turned out NOT to be the LVS-relevant one here).
# All 4 terminals of each device are shorted to the SAME rail (S=D=B=
# VDD, G=GND for the PMOS; S=D=B=GND, G=VDD for the NMOS) -- confirmed
# directly from the .lvsdb's own terminal dump for the mismatched VDD/
# GND nets. L/W values were NOT guessed -- they come from actually
# running KLayout's own device extractor (db.LayoutToNetlist +
# DeviceExtractorMOS4Transistor) against LEF/TR-1um_STDCELL.gds's
# FILL2/FILL3 cells, replicating 01_Extract.lvs/02_Device.drc's exact
# layer recipe (MP = AP.interacting(GC) & WN - ESD, etc.) via the
# klayout.db Python API (no klayout/xschem CLI needed for this part).
FILL_DECAP = {
    # cell type -> (PMOS L, PMOS W, NMOS L, NMOS W), all in um
    "FILL2": (3.2, 21.2, 3.2, 13.1),
    "FILL3": (8.6, 21.2, 8.6, 13.1),
}

# INV_X1 has no standalone simulations/INV_X1.spice file in this
# project (confirmed via exhaustive find this session) -- its body is
# taken verbatim from its embedded definition inside the untouched
# precedent i2c_slave_async_net_v7_routed.spice (identical across every
# other precedent file grepped this session: i2c_slave_async_net.spice,
# i2c_slave_async_net_routed.spice, tr_1um_i2c_slave_async.spice, all
# byte-identical).
INV_X1_BODY = """.subckt INV_X1 VDD A Y GND
*.PININFO A:I Y:O GND:B VDD:B
MM7 Y A VDD VDD PMOS w=10.2u l=1u
MM2 Y A GND GND NMOS w=3.4u l=1u
.ends"""

# FILL2 subcircuit body (round-4 fix, 2026-08-31, user request "コアの
# FILL2をサブサーキット化して修正"): a fresh real KLayout LVS run
# (ring_osc/LVS_error.lvsdb) showed the CORE's VDD/GND nets Mismatch --
# root-caused via klayout.db.LayoutVsSchematic net-pair/device dump: the
# LAYOUT side now extracts FILL2 as its own hierarchical subcircuit (36
# "FILL2" instances contributing subcircuit-pin connections to VDD/GND,
# exactly matching the real placed-instance count) rather than as bare
# inline MOSFETs the way 77.47 originally modeled it -- while FILL3 is
# STILL extracted as bare, un-hierarchized devices on the layout side
# (no "FILL3" circuit exists in the .lvsdb at all; confirmed via merged
# device width cross-check, W=1950.4=21.2*92 identical on both sides).
# So only FILL2's schematic-side representation changes here, to a
# subcircuit call -- this body is byte-identical (device names, PININFO,
# L/W) to RING_OSC.spice's own ".subckt FILL2 VDD GND" (confirmed via
# direct diff) so gen_lvs_spice_ringosc_v9.py's existing dedup logic
# (normalize() byte-comparison against the base LVS file) recognizes the
# two as the same cell and skips adding a duplicate definition.
FILL2_SUBCKT_BODY = """.subckt FILL2 VDD GND
*.PININFO GND:B VDD:B
MM7 VDD GND VDD VDD PMOS w=21.2u l=3.2u
MM2 GND VDD GND GND NMOS w=13.1u l=3.2u
.ends"""

TOP_SUBCKT_NAME = "i2c_slave_async_nrow_fm"  # matches every layout script's TOP_CELL_NAME

# positional SPICE pin order per cell type, read directly from each
# library cell's own simulations/<TYPE>.spice ".subckt" line this
# session (not assumed).
SPICE_PIN_ORDER = {
    "INV_X1":  ["VDD", "A", "Y", "GND"],
    "BUF_X1":  ["VDD", "A", "Y", "GND"],
    "BUFTH":   ["VDD", "A", "Y", "GND"],
    "DEL1":    ["VDD", "A", "Y", "GND"],
    "AND2_X1": ["VDD", "Y", "A", "B", "GND"],
    "AND4_X1": ["D", "C", "VDD", "Y", "A", "B", "GND"],
    "NAND2":   ["VDD", "Y", "A", "B", "GND"],
    "NAND3":   ["C", "VDD", "Y", "A", "B", "GND"],
    "NOR2":    ["VDD", "Y", "A", "B", "GND"],
    "NOR3":    ["B", "C", "VDD", "Y", "A", "GND"],
    "NOR4":    ["A", "B", "C", "D", "VDD", "Y", "GND"],
    "OR2":     ["VDD", "Y", "A", "B", "GND"],
    "OR3":     ["C", "VDD", "Y", "A", "B", "GND"],
    "OR4":     ["A", "B", "C", "D", "VDD", "Y", "GND"],
    "XOR2":    ["VDD", "Y", "A", "B", "GND"],
    "XNOR2":   ["VDD", "Y", "A", "B", "GND"],
    "MUX2":    ["A", "B", "S", "VDD", "Y", "GND"],
    "DFFRB":   ["VDD", "QB", "D", "Q", "RSTB", "GND", "CK"],
}

# top-level port list, exact order matching the established precedent
# (simulations/i2c_slave_async_nrow_fm.spice's own .subckt line).
SCALAR_PORTS_ORDERED = ["rst_n", "scl", "sda_in"]
TX_DATA_BITS = 8
SCALAR_PORTS_MID = ["sda_oe"]
RX_DATA_BITS = 8
SCALAR_PORTS_TAIL = ["rx_valid", "addr_match", "rw", "busy"]

PORT_DIR = {
    "rst_n": "I", "scl": "I", "sda_in": "I", "sda_oe": "O",
    "rx_valid": "O", "addr_match": "O", "rw": "O", "busy": "O",
    "VDD": "B", "GND": "B",
}
for _i in range(8):
    PORT_DIR[f"tx_data[{_i}]"] = "I"
    PORT_DIR[f"rx_data[{_i}]"] = "O"

# output-port aliases confirmed via direct grep of v9_rowbuf.v's assign
# statements -- canonicalize to the PORT name (matches precedent).
SCALAR_ALIAS = {"rw_bit": "rw", "addr_ok": "addr_match"}
BUS_ALIAS_PREFIX = {"rx_data_r": "rx_data"}

# 2026-09-02: general "assign A = B;" scalar-alias resolver (union-find,
# same algorithm as netlist_parser.py's _build_alias_resolver -- kept as
# a separate copy here since this script intentionally doesn't share
# netlist_parser.py's instance-parsing, per its own module docstring).
# Added after v5's rst_scl_domain-stretch fix introduced "assign
# scl_gated = _156_;" (a real, load-bearing alias -- scl_gated is the
# literal net every DFFRB's .CK() now names, while _156_ is Yosys's own
# name for the AND gate that actually drives it): the OLD hardcoded-3
# SCALAR_ALIAS/BUS_ALIAS_PREFIX dict silently dropped this connection
# entirely (scl_gated ended up undriven in the emitted SPICE, no error,
# no warning -- confirmed by a real ngspice run's `.measure` results all
# reading ~0V for a scl_gated-clocked flop and by direct grep of the
# generated file showing zero drivers for that net). The old dict's
# comment "the only 3 non-bookkeeping assigns... confirmed via direct
# grep" was a one-time manual audit of the netlist AS IT EXISTED AT THE
# TIME -- it silently goes stale the moment new RTL introduces a new
# alias, exactly netlist_parser.py's own documented alias-resolution
# rationale. This general resolver is now the primary mechanism; the
# hardcoded dict is kept ONLY to force the human-readable port name
# (rw/addr_match/rx_data) to win as canonical instead of whatever
# internal _NNN_ name the union-find happens to pick.
_alias_resolve = None  # set by load_netlist_text() / main() before first resolve_net() call


def _build_alias_resolver(text, prefer=frozenset()):
    """Union-find alias resolver, biased so a name in `prefer` (top-level
    port names) always wins as the canonical/root representative of its
    group, regardless of which side of the "assign LHS = RHS;" it
    appeared on. Without this bias the plain union-find (parent[ra]=rb,
    i.e. whichever side's root was found SECOND wins) can silently
    rename a PORT itself away to some internal Yosys _NNN_ name -- this
    actually happened for "sda_oe" after the v5 resynthesis: the emitted
    SPICE subckt declared "sda_oe" as a port but nothing internally
    connected to it (confirmed by direct grep: the literal string
    "sda_oe" appeared only in the .subckt/PININFO lines, zero instance
    pins), because the general resolver picked some other net as
    canonical for that alias group and rewrote every real connection to
    use THAT name instead -- leaving the port floating and, downstream,
    the DUT's real sda_oe permanently undriven/disconnected from the pad
    ring (see design_notes.md: this produced the "SDA never reaches a
    real HIGH level" symptom, unrelated to the scl_gated fanout fix)."""
    parent = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        a_pref, b_pref = ra in prefer, rb in prefer
        if a_pref and not b_pref:
            parent[rb] = ra
        elif b_pref and not a_pref:
            parent[ra] = rb
        else:
            # neither (or both) preferred -- fall back to the original
            # arbitrary direction; if both are preferred port names this
            # would be a genuine problem (two ports aliased together),
            # but that hasn't occurred in this design.
            parent[ra] = rb

    for m in re.finditer(r"assign\s+(.+?)\s*=\s*(.+?);", text):
        lhs, rhs = m.group(1).strip(), m.group(2).strip()
        if "{" in lhs or "{" in rhs or "'" in rhs:
            continue  # bus-concat / literal assigns -- not simple aliases
        if re.match(r"^\w+(\[\d+\])?$", lhs) and re.match(r"^\w+(\[\d+\])?$", rhs):
            union(lhs, rhs)

    return find


def resolve_net(net):
    net = net.strip()
    m = re.match(r"^(\w+)(\[\d+\])$", net)
    if m:
        base, idx = m.group(1), m.group(2)
        if base in BUS_ALIAS_PREFIX:
            base = BUS_ALIAS_PREFIX[base]
        elif _alias_resolve is not None:
            base = _alias_resolve(base)
        return base + idx
    if net in SCALAR_ALIAS:
        return SCALAR_ALIAS[net]
    if _alias_resolve is not None:
        return _alias_resolve(net)
    return net


_SKIP_TYPES = {"module", "endmodule", "input", "output", "wire", "assign", "reg", "inout"}


def parse_instances(text):
    instances = []
    for m in re.finditer(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(.*?)\)\s*;",
        text, re.M | re.S,
    ):
        typ, name, portlist = m.groups()
        if typ in _SKIP_TYPES:
            continue
        pins = {}
        for pm in re.finditer(r"\.(\w+)\s*\(\s*([^)]*?)\s*\)", portlist):
            pins[pm.group(1)] = resolve_net(pm.group(2))
        instances.append((typ, name, pins))
    return instances


def build_top_ports():
    ports = list(SCALAR_PORTS_ORDERED)
    ports += [f"tx_data[{i}]" for i in range(TX_DATA_BITS)]
    ports += list(SCALAR_PORTS_MID)
    ports += [f"rx_data[{i}]" for i in range(RX_DATA_BITS)]
    ports += list(SCALAR_PORTS_TAIL)
    ports += ["VDD", "GND"]
    return ports


def wrap_port_line(prefix, ports, width=110):
    lines = []
    cur = prefix
    for p in ports:
        add = (" " if cur[-1] not in "( " else "") + p
        if len(cur) + len(add) > width:
            lines.append(cur)
            cur = "+ " + p
        else:
            cur += add
    lines.append(cur)
    return lines


def load_library_bodies(used_types):
    """Read each used cell type's real, transistor-level `.subckt ...
    .ends` body verbatim from its library file (simulations/<TYPE>.spice),
    except INV_X1 (no standalone file -- see INV_X1_BODY above). Also
    sanity-checks that the body's own declared pin order still matches
    SPICE_PIN_ORDER (the source of truth for how this script maps named
    Verilog ports to positional args) -- catches drift instead of
    silently emitting an instance list that no longer matches its own
    definition."""
    bodies = {}
    for typ in sorted(used_types):
        if typ == "INV_X1":
            body = INV_X1_BODY
        else:
            path = f"{LIB_DIR}/{typ}.spice"
            body = open(path).read().strip()
        m = re.search(r"^\.subckt\s+(\S+)\s+(.*)$", body, re.M)
        if not m or m.group(1) != typ:
            raise SystemExit(f"{typ}: body doesn't start with a matching .subckt line")
        decl_order = m.group(2).split()
        if decl_order != SPICE_PIN_ORDER[typ]:
            raise SystemExit(f"{typ}: library body pin order {decl_order} != "
                              f"SPICE_PIN_ORDER {SPICE_PIN_ORDER[typ]}")
        bodies[typ] = body
    return bodies


def count_fill_instances(placement_json):
    """Real placed FILL2/FILL3 instance counts (v9: 36 FILL2, 92 FILL3
    -- confirmed this session from placement_nrow_fm_v9.json). One MOS4
    device pair is emitted PER real instance (not one pre-merged device
    with a scaled W) so this schematic-side netlist mirrors the layout
    at the same granularity KLayout's own `combine_devices` step will
    reduce BOTH sides from -- avoids assuming/guessing how a manually
    pre-merged multiplier parameter would compare against the real
    merge result."""
    placement = json.load(open(placement_json))
    counts = {"FILL2": 0, "FILL3": 0}
    for row in placement["rows"]:
        for inst in row:
            if inst["type"] in counts:
                counts[inst["type"]] += 1
    return counts


def build_fill_decap_lines(counts):
    """FILL3: unchanged from 77.47 -- one bare PMOS+NMOS pair per real
    placed instance (layout still extracts FILL3 as bare devices too).
    FILL2: round-4 fix -- one "xFILL2_i VDD GND FILL2" subcircuit
    instance per real placed instance instead (layout now extracts
    FILL2 as its own hierarchical subcircuit; see FILL2_SUBCKT_BODY's
    comment above for the full root-cause writeup)."""
    lines = ["** FILL3 decoupling-cap devices (design_notes.md 77.47) --",
             "** one PMOS+NMOS pair per real placed instance, L/W from real",
             "** device extraction against LEF/TR-1um_STDCELL.gds.",
             "** FILL2 is instantiated as a subcircuit instead, below",
             "** (design_notes.md round-4 fix, 2026-08-31 -- see",
             "** FILL2_SUBCKT_BODY's comment for why)."]
    n_devices = 0
    n_fill2_instances = 0
    for typ, count in sorted(counts.items()):
        if typ == "FILL2":
            for i in range(1, count + 1):
                lines.append(f"xFILL2_{i} VDD GND FILL2")
                n_fill2_instances += 1
            continue
        pl, pw, nl, nw = FILL_DECAP[typ]
        for i in range(1, count + 1):
            lines.append(f"M_{typ}_{i}_p VDD GND VDD VDD PMOS w={pw}u l={pl}u")
            lines.append(f"M_{typ}_{i}_n GND VDD GND GND NMOS w={nw}u l={nl}u")
            n_devices += 2
    return lines, n_devices, n_fill2_instances


def main():
    global _alias_resolve
    text = open(NET_PATH).read()
    _alias_resolve = _build_alias_resolver(text, prefer=set(build_top_ports()))
    instances = parse_instances(text)

    unknown_types = sorted({t for t, _, _ in instances if t not in SPICE_PIN_ORDER})
    if unknown_types:
        raise SystemExit(f"no SPICE_PIN_ORDER entry for cell type(s): {unknown_types}")

    used_types = {t for t, _, _ in instances}
    lib_bodies = load_library_bodies(used_types)

    top_ports = build_top_ports()

    lines = []
    lines.append(f"** Generated by script/gen_lvs_spice_v9.py from {NET_PATH.split('/')[-1]}")
    lines.append("** (no xschem .sch involved -- see design_notes.md 77.45/77.46 for why/how)")
    lines.extend(wrap_port_line(f".subckt {TOP_SUBCKT_NAME} ", top_ports))
    lines.extend(wrap_port_line("*.PININFO ", [f"{p}:{PORT_DIR[p]}" for p in top_ports]))

    n_forced_pwr = 0
    for typ, name, pins in instances:
        order = SPICE_PIN_ORDER[typ]
        args = []
        for pname in order:
            if pname in ("VDD", "GND"):
                args.append(pname)  # always tie power regardless of RTL text
                if pname not in pins:
                    n_forced_pwr += 1
            else:
                if pname not in pins:
                    raise SystemExit(f"instance {name} ({typ}) missing pin {pname}: {pins}")
                args.append(pins[pname])
        # v2 fix: precedent convention is "x" + name (NOT "x_" + name) --
        # confirmed against the untouched original simulations/
        # i2c_slave_async_nrow_fm.spice before this script's first run
        # overwrote it: instance name "_135_" -> line "x_135_ ..." (one
        # underscore, matching the name's own leading "_", not two).
        # An earlier version of this script used f"x_{name}", which
        # double-prefixed every underscore-led name (e.g. "x__148_").
        lines.append(f"x{name} " + " ".join(args) + f" {typ}")

    fill_counts = count_fill_instances(PLACEMENT_JSON)
    fill_lines, n_fill_devices, n_fill2_instances = build_fill_decap_lines(fill_counts)
    lines.extend(fill_lines)

    lines.append(".ends")
    lines.append("")

    # FILL2 subcircuit body (round-4 fix) -- a separate top-level
    # .subckt block, same as the library cell bodies below.
    lines.append(FILL2_SUBCKT_BODY)
    lines.append("")

    # library cell bodies, embedded inline (matches xschem's own
    # hierarchical-flatten export convention, confirmed against the
    # untouched precedent -- see module docstring v3 fix note above).
    for typ in sorted(used_types):
        lines.append(lib_bodies[typ])
        lines.append("")

    content = "\n".join(lines)

    for out_path in (OUT_PATH_PROJECT, OUT_PATH_SIM):
        with open(out_path, "w") as f:
            f.write(content)
        print(f"wrote {out_path}")

    print(f"{len(instances)} instance(s), {len(top_ports)} top port(s), "
          f"{n_forced_pwr} power-pin connection(s) forced (not present in RTL text), "
          f"{len(used_types)} library cell body(ies) embedded, "
          f"{n_fill_devices} FILL3 decap device(s) + {n_fill2_instances} FILL2 subcircuit "
          f"instance(s) added ({fill_counts})")


if __name__ == "__main__":
    main()
