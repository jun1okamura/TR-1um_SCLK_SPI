"""
gen_lvs_spice_v10.py (design_notes.md section 108.44, user request
"LVSの spice の準備をお願いします。MUXDFFRB RSLATCH は LEF/simulation/
の下のspice を使ってください。")

V10 counterpart of gen_lvs_spice_v9.py -- generates the flat, gate-level
structural LVS reference SPICE netlist for the top cell
"i2c_slave_async_nrow_fm" directly from src/i2c_slave_async_net_v10_final.v,
the same mechanical Verilog->SPICE translation v9's script established
(see that script's own docstring for the full rationale of why this is
done directly rather than via xschem, and for the FILL2/FILL3 decap
device handling, which is unchanged here).

Two things are new for V10, both handled below:

1. **MUXDFFRB (19 instances) and RSLATCH (3 instances)**, the two
   compound cells 108.23-108.29 introduced (MUX2+DFFRB and NOR2+NOR2
   respectively), per user instruction use the SPICE bodies exported
   from their real xschem schematics at simulations/MUXDFFRB.spice /
   simulations/RSLATCH.spice (== LEF/simulation/, a symlink to the same
   directory) -- NOT re-derived from the .extracted (GDS-side)
   netlists, and not hand-written here. Both files are xschem's own
   *hierarchical* netlist export: each contains the compound cell's own
   outer `.subckt MUXDFFRB/RSLATCH ... .ends` block, calling DFFRB+MUX2
   / NOR2+NOR2 as SUBCIRCUIT instances (x1/x2 lines), not raw
   transistors.

   **108.45 fix (real LVS run, layout/step10/LVS_error.lvsdb)**: an
   earlier revision of this script embedded that hierarchical form
   directly (`extract_outer_subckt()` -- kept the x1/x2 SUBCKT calls,
   only stripped the file's own duplicate nested DFFRB/MUX2/NOR2 body
   copies). The user's real KLayout LVS run reported RSLATCH NoMatch
   and MUXDFFRB Skipped. Root-caused via `klayout.db.LayoutVsSchematic`/
   `NetlistCrossReference` read directly against the .lvsdb: the
   LAYOUT-side extraction of RSLATCH is a single FLAT set of 8 raw
   MOSFETs with no "NOR2" subcircuit boundary at all (confirmed
   consistent with LEF/RSLATCH.extracted, read independently) --
   because MUXDFFRB/RSLATCH are drawn as one monolithic leaf GDS cell
   in LEF/TR-1um_STDCELL.gds, NOT as an assembly of separately-placed
   NOR2/DFFRB/MUX2 GDS sub-cells. This is the opposite situation from
   RING_OSC (design_notes 103.10/103.11), whose GDS genuinely DOES
   place its sub-cells as their own named hierarchy -- there, keeping
   the reference hierarchical was correct; here, the reference must be
   FLATTENED to match the layout's own flat extraction, or the LVS
   engine has no valid way to align a hierarchical reference circuit
   against a flat layout circuit.

   `flatten_compound_cell()` inlines each x1/x2 call's own transistor
   list (read from the SAME simulation/ directory's DFFRB.spice/
   MUX2.spice/NOR2.spice) directly into MUXDFFRB/RSLATCH's own body,
   renaming each call's purely-internal nodes with a per-call prefix
   (`x1_`/`x2_`) so the two sub-instances' same-named internal nets
   (e.g. both NOR2 copies independently use a local net literally
   called "net1") don't collide once flattened into one namespace --
   the cell's genuinely SHARED internal net (MUXDFFRB's own "net1",
   the MUX2-output/DFFRB-D connection) is left unprefixed since it
   already has a name unique to MUXDFFRB's own scope. Every symbol
   involved (call structure, sub-cell device bodies) still comes
   exclusively from LEF/simulation/'s own files, per the user's
   instruction -- only the CALL is inlined, no device content is
   invented. Verified by hand against LEF/RSLATCH.extracted: identical
   device count (8) and, modulo the harmless drain/source swap KLayout's
   own device comparer already normalizes for a plain symmetric MOSFET,
   identical topology.

2. **MUXDFFRB/RSLATCH's own outer subckt declarations name their ground
   pin "VSS"**, not "GND" (`.subckt MUXDFFRB VDD A Q B CK S QB VSS
   RSTB`, `.subckt RSLATCH VDD S Q R QB VSS`) -- an xschem
   top-cell-vs-library-cell labeling quirk, purely cosmetic (it is still
   positionally the same global ground rail slot). The instantiation-line
   builder below ties BOTH "VDD"/"GND" (existing convention) AND "VSS"
   positions to the project's one real global rail names ("VDD"/"GND")
   unconditionally, exactly like every other cell's power pins.

sda_oe/rx_valid note: unlike v9 (see that script's own note -- v9's
sda_oe connected directly to a DFFRB's QB with no assign-alias needed),
V10's RTL drives both of these output ports via a single-level
`assign sda_oe = _187_;` / `assign rx_valid = _154_;` from anonymous
Yosys nets (see route_top_pins_nrow_fm.py's 108.42 fix for the same
underlying discovery). This script's own general, prefer-biased
alias resolver (_build_alias_resolver, added 2026-09-02 specifically to
survive exactly this kind of drift without a script edit) already
handles it transparently -- confirmed: resolve_net("_187_") ->
"sda_oe", resolve_net("_154_") -> "rx_valid" (port names always win as
canonical over anonymous Yosys names, by construction).
"""
import json
import os
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_XSCHEM_SIM_DIR = Path(os.environ.get("XSCHEM_SIM_DIR", str(Path.home() / ".xschem" / "simulations")))

NET_PATH = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v10_final.v")
OUT_PATH_PROJECT = str(_REPO_ROOT / "schematic" / "i2c_slave_async_nrow_fm_v10.spice")
OUT_PATH_SIM = str(_XSCHEM_SIM_DIR / "i2c_slave_async_nrow_fm.spice")
LIB_DIR = str(_XSCHEM_SIM_DIR)
PLACEMENT_JSON = str(_REPO_ROOT / "LEF" / "placement_nrow_fm_v10.json")

# unchanged from gen_lvs_spice_v9.py (108.-- / 77.47, round-4 fix
# 2026-08-31) -- same FILL2/FILL3 decap modeling, same L/W values (real
# device extraction against LEF/TR-1um_STDCELL.gds, not guessed), V10
# just has different real placed counts (33 FILL2 / 83 FILL3, vs v9's
# 36/92 -- read fresh from placement_nrow_fm_v10.json below, not
# hardcoded).
FILL_DECAP = {
    "FILL2": (3.2, 21.2, 3.2, 13.1),
    "FILL3": (8.6, 21.2, 8.6, 13.1),
}

INV_X1_BODY = """.subckt INV_X1 VDD A Y GND
*.PININFO A:I Y:O GND:B VDD:B
MM7 Y A VDD VDD PMOS w=10.2u l=1u
MM2 Y A GND GND NMOS w=3.4u l=1u
.ends"""

FILL2_SUBCKT_BODY = """.subckt FILL2 VDD GND
*.PININFO GND:B VDD:B
MM7 VDD GND VDD VDD PMOS w=21.2u l=3.2u
MM2 GND VDD GND GND NMOS w=13.1u l=3.2u
.ends"""

TOP_SUBCKT_NAME = "i2c_slave_async_nrow_fm"

# positional SPICE pin order per cell type. Existing types' orders are
# unchanged from v9 (same library, same cells). MUXDFFRB/RSLATCH are new
# this session, read directly from their own simulations/<TYPE>.spice
# outer .subckt line (NOT assumed) -- note both declare their ground pin
# as "VSS" (see module docstring point 2); POWER_PIN_NAMES below is what
# actually decides which positions get force-tied, not the literal
# string "GND".
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
    # V10 new compound cells (108.23-108.29), pin order taken verbatim
    # from simulations/MUXDFFRB.spice / simulations/RSLATCH.spice's own
    # outer .subckt line -- note "VSS" in place of "GND" (cosmetic, see
    # module docstring point 2; POWER_PIN_NAMES handles the tie).
    "MUXDFFRB": ["VDD", "A", "Q", "B", "CK", "S", "QB", "VSS", "RSTB"],
    "RSLATCH":  ["VDD", "S", "Q", "R", "QB", "VSS"],
}

# cell types whose own top-level body must come from a DIFFERENT file
# than {LIB_DIR}/{TYPE}.spice's own top-of-file block, plus only the
# FIRST (outermost) .subckt in that file -- see module docstring point 1.
# For every other type, load_library_bodies() keeps v9's original
# behavior (whole-file read, file assumed to already be exactly one
# block).
MULTI_BLOCK_TYPES = {"MUXDFFRB", "RSLATCH"}

# any pin name in this set gets force-tied to the real global rail
# ("VDD" or "GND" respectively) regardless of what the RTL text says and
# regardless of the cell's own label for that slot ("VSS" for MUXDFFRB/
# RSLATCH, "GND" for everything else).
POWER_PIN_NAMES = {"VDD": "VDD", "GND": "GND", "VSS": "GND"}

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

# hardcoded-name bias for the general resolver below -- forces the
# human-readable port name to win as canonical instead of whatever
# internal _NNN_/rw_bit/addr_ok/rx_data_r name the RTL happens to use,
# matching v9's script exactly (same 3 known aliases still present in
# V10, confirmed via port_net_name() checks this session). sda_oe/
# rx_valid need NO entry here -- the general prefer-biased resolver
# below handles their new (V10-only) _187_/_154_ assign-aliasing
# automatically, same mechanism that already covers "scl_gated" in v9.
SCALAR_ALIAS = {"rw_bit": "rw", "addr_ok": "addr_match"}
BUS_ALIAS_PREFIX = {"rx_data_r": "rx_data"}

_alias_resolve = None


def _build_alias_resolver(text, prefer=frozenset()):
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
            parent[ra] = rb

    for m in re.finditer(r"assign\s+(.+?)\s*=\s*(.+?);", text):
        lhs, rhs = m.group(1).strip(), m.group(2).strip()
        if "{" in lhs or "{" in rhs or "'" in rhs:
            continue
        if re.match(r"^\w+(\[\d+\])?$", lhs) and re.match(r"^\w+(\[\d+\])?$", rhs):
            union(lhs, rhs)

    return find


_LITERAL_RE = re.compile(r"^(\d+)'([bBoOdDhH])([0-9a-fA-Fxz_]+)$")
_LITERAL_BASE = {"b": 2, "o": 8, "d": 10, "h": 16}


def _tie_literal(net):
    """108.48 fix (real LVS run, layout/step10/LVS_error.lvsdb 11:38):
    `u_sda_target`'s MUX2 instance has `.A(1'h1)` in the raw Verilog
    (i2c_slave_async_net_v10_final.v line 1346) -- a Verilog bit-literal
    tie-high constant, not a real net reference. Neither resolve_net()
    nor _build_alias_resolver() had ANY handling for this: the literal
    string "1'h1" passed straight through as if it were a genuine net
    name, so the reference SPICE ended up with an instance argument
    that isn't a real node at all -- corrupting this instance's own
    connectivity, and (since KLayout's netlist comparer does GLOBAL
    graph matching, not a purely local per-instance check) cascading
    into unrelated-looking mismatches nearby in the connectivity graph
    (this MUX2 sits right next to sda_oe/busy in the design, which is
    exactly what showed up as spurious "sda_oe <-> _156_" / "busy <->
    BUSY" / "VDD <-> VDD" mismatches at the top circuit, alongside the
    real, direct fault: this MUX2 instance itself having no layout
    counterpart at all). Only one literal-tie occurrence exists in the
    whole V10 netlist (grep-confirmed) -- always 1-bit-wide in practice
    here, so 1 maps to the real "VDD" net and 0 to "GND"; a wider
    literal (e.g. tying a multi-bit bus pin to a constant) would need
    per-bit expansion this design has never actually used, so that case
    raises rather than silently mis-handling it."""
    m = _LITERAL_RE.match(net)
    if not m:
        return None
    width, base, digits = m.groups()
    width = int(width)
    value = int(digits, _LITERAL_BASE[base.lower()])
    if width != 1:
        raise SystemExit(f"literal constant pin tie {net!r}: only 1-bit constants "
                          f"are supported (this design has never needed wider ones), "
                          f"got width {width}")
    return "VDD" if value else "GND"


def _apply_bus_prefix(net):
    """`assign rx_data = rx_data_r;` (108.47 discovery, line 1374 of
    i2c_slave_async_net_v10_final.v) is a WHOLE-BUS assign -- both sides
    bare, unindexed names -- so `_build_alias_resolver`'s union-find only
    ever links the bare bases "rx_data"<->"rx_data_r" together, never
    any individual indexed pair like "rx_data[6]"<->"rx_data_r[6]" (the
    per-bit equivalence is only true because Verilog broadcasts a bus
    assign bit-for-bit, which the union-find has no way to infer from a
    single unindexed statement). BUS_ALIAS_PREFIX is the deliberate,
    existing mechanism for this exact pattern; this helper is what
    applies it, to WHATEVER string a net resolves to (see resolve_net's
    108.47 note for why applying it only to the ORIGINAL input, as v9's
    version did, isn't enough)."""
    m = re.match(r"^(\w+)(\[\d+\])$", net)
    if m and m.group(1) in BUS_ALIAS_PREFIX:
        return BUS_ALIAS_PREFIX[m.group(1)] + m.group(2)
    return net


def resolve_net(net):
    """108.47 fix (real LVS run, layout/step10/LVS_error.lvsdb 11:24):
    v9's version (and this script's own first revision) only ever ran
    _apply_bus_prefix's equivalent inline check against the RAW INPUT
    net string. That misses anonymous Yosys nets like "_152_" that the
    general union-find resolver (_alias_resolve) itself resolves to an
    ALREADY-INDEXED "rx_data_r[N]"-style token as ITS canonical choice
    (confirmed directly: _alias_resolve("_152_") -> "rx_data_r[6]") --
    since "_152_" itself doesn't match the bus-index regex, the old code
    took the plain scalar branch and returned that intermediate result
    verbatim, without ever re-checking whether IT needed the same
    rx_data_r->rx_data remap. Symptom: MUXDFFRB u_muxdffrb_1's Q pin
    (the real bus output, wired to port rx_data[6] in the real layout)
    had no reference-side counterpart at the TOP circuit, while a
    genuinely different net (u_muxdffrb_1's own B input, its literal
    layout label "RX_DATA_R[6]") was ALSO unmatched -- both invisible to
    the old code's single-pass check. Now the bus-prefix remap is
    applied to the GENERAL resolver's OWN output as the very last step,
    regardless of which branch produced it."""
    net = net.strip()
    lit = _tie_literal(net)
    if lit is not None:
        return lit
    if net in SCALAR_ALIAS:
        return SCALAR_ALIAS[net]
    resolved = _alias_resolve(net) if _alias_resolve is not None else net
    return _apply_bus_prefix(resolved)


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


def load_flat_body_lines(typ):
    """Standalone leaf-cell body (INV_X1's hardcoded constant, or the
    whole simulations/<TYPE>.spice file, which for every plain library
    gate is already exactly one `.subckt ... .ends` block of raw M
    devices) -- returns (decl_order, [device-line strings]) with
    comments/.subckt/.PININFO/.ends stripped."""
    if typ == "INV_X1":
        lines = INV_X1_BODY.splitlines()
    else:
        lines = open(f"{LIB_DIR}/{typ}.spice").read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().startswith(f".subckt {typ} "))
    end = next(i for i in range(start, len(lines)) if lines[i].strip().lower().startswith(".ends"))
    decl_order = lines[start].split()[2:]
    device_lines = [l for l in lines[start + 1:end] if l.strip() and not l.strip().startswith("*")]
    return decl_order, device_lines


def flatten_call(call_line, prefix):
    """Inline-expand one `xN <args...> TYPE` sub-instance call line into
    raw M-device lines: each of TYPE's own declared pins maps
    positionally to the actual net passed at the call site; every OTHER
    node name appearing in TYPE's body is a net purely internal to this
    one sub-instance, so it gets a `<prefix>_` tag to keep it distinct
    from the same-named internal net of any OTHER sub-instance flattened
    into the same enclosing body (e.g. MUX2's own internal "net1" vs
    DFFRB's own internal "net1" vs MUXDFFRB's own shared "net1" pin-level
    net between the two calls -- see module docstring point 1)."""
    toks = call_line.split()
    inst_name, typ = toks[0], toks[-1]
    args = toks[1:-1]
    order = SPICE_PIN_ORDER[typ]
    if len(args) != len(order):
        raise SystemExit(f"{call_line!r}: {len(args)} arg(s) != {typ}'s {len(order)}-pin order {order}")
    mapping = dict(zip(order, args))
    _, device_lines = load_flat_body_lines(typ)
    out = []
    for line in device_lines:
        t = line.split()
        # 108.46 fix: SPICE determines element type from the INSTANCE
        # NAME's first character (M=MOSFET, X=subcircuit call, ...).
        # The original devname/prefix "prefix_origname" (e.g. "x1_MM7")
        # starts with "x" -- klayout's NetlistSpiceReader read that as a
        # subcircuit call to a (nonexistent) circuit named "PMOS"/"NMOS"
        # instead of a MOSFET device, so RSLATCH/MUXDFFRB parsed with 0
        # real devices (confirmed directly: each_device() empty,
        # each_subcircuit() returning 8/38 instead -- the real LVS run's
        # "schematic side is empty" report, layout/step10/LVS_error.lvsdb
        # 11:17). Keeping the leading "M" (devname = "M" + prefix + "_" +
        # origname) preserves the MOSFET type tag while the rest of the
        # name still keeps the per-call prefix for readability/uniqueness.
        devname = f"M{prefix}_{t[0]}"
        nodes = [mapping.get(n, f"{prefix}_{n}") for n in t[1:5]]
        out.append(" ".join([devname] + nodes + t[5:]))
    return out


def flatten_compound_cell(typ):
    """MUXDFFRB/RSLATCH (108.45): both are laid out in
    LEF/TR-1um_STDCELL.gds as a single FLAT leaf standard cell (confirmed
    via LEF/MUXDFFRB.extracted / RSLATCH.extracted -- 38 / 8 raw
    transistors, no nested subcircuit boundary at all), unlike RING_OSC
    (design_notes 103.11) whose GDS genuinely does instantiate its
    sub-cells as separate, named, hierarchical GDS cells. A real LVS run
    against the hierarchical `.subckt MUXDFFRB/RSLATCH ... x1 ... TYPE
    ... .ends` form this script used to embed produced NoMatch/Skipped
    on these two circuits (layout/step10/LVS_error.lvsdb, 108.45) --
    root cause: comparing a hierarchical reference against a flat
    layout extraction is a structural mismatch the LVS engine cannot
    resolve on its own, the same class of issue 103.10/103.11 already
    diagnose (just the opposite conclusion, because these two cells'
    actual physical hierarchy is opposite RING_OSC's). Fix: flatten
    simulations/MUXDFFRB.spice / RSLATCH.spice's own x1/x2 sub-calls
    into raw M-device lines via flatten_call() (still 100% sourced from
    LEF/simulation/'s own files -- MUXDFFRB.spice/RSLATCH.spice for the
    call structure, DFFRB.spice/MUX2.spice/NOR2.spice for the sub-cell
    device bodies -- per the user's instruction; only the CALL structure
    is inlined, no device content is invented). Verified by hand against
    RSLATCH.extracted: identical device count (8) and, modulo the
    harmless drain/source swap KLayout's own device comparer already
    normalizes for a plain symmetric MOSFET, identical topology."""
    lines = open(f"{LIB_DIR}/{typ}.spice").read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().startswith(f".subckt {typ} "))
    end = next(i for i in range(start, len(lines)) if lines[i].strip().lower().startswith(".ends"))
    decl_line = lines[start]
    pininfo_line = next((l for l in lines[start + 1:end] if l.strip().startswith("*.PININFO")), None)
    call_lines = [l.strip() for l in lines[start + 1:end]
                  if l.strip() and not l.strip().startswith("*")]

    body = [decl_line]
    if pininfo_line:
        body.append(pininfo_line)
    for i, call_line in enumerate(call_lines, 1):
        body.extend(flatten_call(call_line, f"x{i}"))
    body.append(".ends")
    return "\n".join(body)


def load_library_bodies(used_types):
    """Read each used cell type's real, transistor-level `.subckt ...
    .ends` body. MUXDFFRB/RSLATCH: flattened via flatten_compound_cell()
    (see its docstring -- 108.45) instead of embedded as a hierarchical
    subcircuit call, to match their flat physical layout."""
    used_types = set(used_types)

    bodies = {}
    for typ in sorted(used_types):
        if typ in MULTI_BLOCK_TYPES:
            body = flatten_compound_cell(typ)
        elif typ == "INV_X1":
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
    return bodies, used_types


def count_fill_instances(placement_json):
    placement = json.load(open(placement_json))
    counts = {"FILL2": 0, "FILL3": 0}
    for row in placement["rows"]:
        for inst in row:
            if inst["type"] in counts:
                counts[inst["type"]] += 1
    return counts


def build_fill_decap_lines(counts):
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
    lib_bodies, all_embedded_types = load_library_bodies(used_types)

    top_ports = build_top_ports()

    lines = []
    lines.append(f"** Generated by script/gen_lvs_spice_v10.py from {NET_PATH.split('/')[-1]}")
    lines.append("** (MUXDFFRB/RSLATCH bodies sourced from simulations/MUXDFFRB.spice and")
    lines.append("** simulations/RSLATCH.spice per user instruction -- see module docstring)")
    lines.extend(wrap_port_line(f".subckt {TOP_SUBCKT_NAME} ", top_ports))
    lines.extend(wrap_port_line("*.PININFO ", [f"{p}:{PORT_DIR[p]}" for p in top_ports]))

    n_forced_pwr = 0
    for typ, name, pins in instances:
        order = SPICE_PIN_ORDER[typ]
        args = []
        for pname in order:
            if pname in POWER_PIN_NAMES:
                args.append(POWER_PIN_NAMES[pname])
                if pname not in pins:
                    n_forced_pwr += 1
            else:
                if pname not in pins:
                    raise SystemExit(f"instance {name} ({typ}) missing pin {pname}: {pins}")
                args.append(pins[pname])
        lines.append(f"x{name} " + " ".join(args) + f" {typ}")

    fill_counts = count_fill_instances(PLACEMENT_JSON)
    fill_lines, n_fill_devices, n_fill2_instances = build_fill_decap_lines(fill_counts)
    lines.extend(fill_lines)

    lines.append(".ends")
    lines.append("")

    lines.append(FILL2_SUBCKT_BODY)
    lines.append("")

    for typ in sorted(all_embedded_types):
        lines.append(lib_bodies[typ])
        lines.append("")

    content = "\n".join(lines)

    for out_path in (OUT_PATH_PROJECT, OUT_PATH_SIM):
        with open(out_path, "w") as f:
            f.write(content)
        print(f"wrote {out_path}")

    print(f"{len(instances)} instance(s), {len(top_ports)} top port(s), "
          f"{n_forced_pwr} power-pin connection(s) forced (not present in RTL text), "
          f"{len(all_embedded_types)} library cell body(ies) embedded ({sorted(all_embedded_types)}), "
          f"{n_fill_devices} FILL3 decap device(s) + {n_fill2_instances} FILL2 subcircuit "
          f"instance(s) added ({fill_counts})")


if __name__ == "__main__":
    main()
