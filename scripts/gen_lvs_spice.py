#!/usr/bin/env python3
"""gen_lvs_spice.py -- generate the LVS reference SPICE netlist for the placed
and routed core cell, mechanically, from the gate-level Verilog.

    layout/spi_slave_sclk_net_pnr.v   (the netlist the P&R flow placed)
  + lef/TR-1um_STDCELL.spice          (transistor bodies, scripts/gen_cell_spice.py)
  + layout/placement_nrow_fm.json     (how many FILL2/FILL3 were placed)
  -> layout/spi_slave_sclk_nrow_fm.spice

The netlist is *generated*, never written by hand: it has to describe exactly
the circuit the layout implements, and the only trustworthy statement of that
is the same Verilog the placer consumed.  This follows the I2C project's
gen_lvs_spice_v9/v10.py (kept unmodified under scripts/i2c_ref/), whose output
went on to pass a real KLayout LVS run; the structure below is theirs.  What is
new here is that nothing about *this* design is hardcoded -- port order, port
directions, net aliases and per-cell SPICE pin order are all derived, so the
script survives a re-synthesis without an edit.  Specifically:

* **Top port list and directions** come from the netlist's own `module` header
  and `input`/`output` declarations, expanded bit by bit for buses, with VDD and
  GND appended.  (The I2C script listed all 24 of its ports as constants.)

* **Per-cell SPICE pin order** comes from each cell's own `.subckt` line in
  lef/TR-1um_STDCELL.spice.  (The I2C script carried a 20-entry table.)  Pins
  are matched to the Verilog by NAME, so only the emitted order depends on this.

* **Net aliases** -- Yosys leaves `assign` statements that make two names the
  same net -- are resolved by union-find over every assign form the netlist
  actually uses: scalar, single-bit, whole-bus (`assign rx_data = rx_data_r;`,
  expanded bit-for-bit from the declared widths, which is what the I2C script's
  hand-maintained BUS_ALIAS_PREFIX table did) and part-select.  Top-level port
  names are biased to win as the canonical name, so the netlist ends up calling
  a net `rx_data[6]` rather than `rx_data_r[6]` -- matching the labels the
  router put in the layout.  An assign this resolver cannot interpret is a hard
  error rather than a silently dropped connection.

* **Constant pin ties** (`.A(1'h1)`) become VDD/GND.  The I2C flow lost a whole
  MUX2 instance to this before it was handled (i2c_ref/gen_lvs_spice_v10.py's
  `_tie_literal` docstring); this design has none today, and the check stays.

FILL / TAP HANDLING (unchanged from the I2C flow)
------------------------------------------------
FILL3's two decoupling devices are emitted INLINE in the top cell, one PMOS +
NMOS pair per placed instance, because the layout extracts them flat.  FILL2 is
emitted as a subcircuit CALL instead -- that is what made the I2C core's LVS
match.  TAP2 contributes nothing (no devices).  The L/W values below come from
the I2C flow and were re-confirmed against this project's own
lef/TR-1um_STDCELL.gds by scripts/check_cell_spice.py.

  usage:  scripts/gen_lvs_spice.py [-o OUT]
"""
import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

CELL_SPICE = os.path.join(_cfg.ROOT, "lef", "TR-1um_STDCELL.spice")
OUT_PATH = os.path.join(_cfg.LAYOUT, _cfg.TOP_CELL_NAME + ".spice")


def _sim_dir():
    """xschem's simulation directory, where the LVS run picks the netlist up.
    layout/step10/simulation is a symlink to it."""
    for cand in (os.environ.get("XSCHEM_SIM_DIR"),
                 os.path.join(_cfg.LAYOUT, "step10", "simulation"),
                 os.path.join(_cfg.ROOT, "lef", "simulation"),
                 os.path.expanduser("~/.xschem/simulations")):
        if cand and os.path.isdir(cand):
            return cand
    return None


SIM_DIR = _sim_dir()

# (L, W) of the PMOS then the NMOS of one fill cell's decap pair.
FILL_DECAP = {
    "FILL2": (3.2, 21.2, 3.2, 13.1),
    "FILL3": (8.6, 21.2, 8.6, 13.1),
}
# fill cells emitted as a subcircuit call rather than inline devices
FILL_AS_SUBCKT = {"FILL2"}
# placed cells with no devices at all
NO_DEVICE_CELLS = {"TAP2"}

# Any cell pin with one of these names is tied to the real global rail no
# matter what the Verilog says.  MUXDFFRB's own subckt calls its ground "VSS".
POWER_PIN_NAMES = {"VDD": "VDD", "GND": "GND", "VSS": "GND"}

_SKIP_TYPES = {"module", "endmodule", "input", "output", "wire", "assign", "reg", "inout"}
_LITERAL_RE = re.compile(r"^(\d+)'([bBoOdDhH])([0-9a-fA-FxzXZ_]+)$")
_LITERAL_BASE = {"b": 2, "o": 8, "d": 10, "h": 16}


# --------------------------------------------------------------------------
# netlist front end
# --------------------------------------------------------------------------
def parse_widths(text):
    """{name: (msb, lsb) or None} for every declared port/wire."""
    widths = {}
    for m in re.finditer(
        r"^\s*(?:input|output|inout|wire|reg)\s+(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+)?"
        r"([A-Za-z_]\w*)\s*;", text, re.M):
        hi, lo, name = m.groups()
        widths[name] = None if hi is None else (int(hi), int(lo))
    return widths


def parse_dirs(text):
    dirs = {}
    for m in re.finditer(
        r"^\s*(input|output|inout)\s+(?:\[\s*\d+\s*:\s*\d+\s*\]\s+)?([A-Za-z_]\w*)\s*;",
        text, re.M):
        dirs[m.group(2)] = {"input": "I", "output": "O", "inout": "B"}[m.group(1)]
    return dirs


def bits(name, width):
    """Bit-expand a declared name, MSB..LSB order like Verilog itself."""
    if width is None:
        return [name]
    hi, lo = width
    step = -1 if hi >= lo else 1
    return [f"{name}[{i}]" for i in range(hi, lo + step, step)]


def module_header_ports(text):
    m = re.search(r"^\s*module\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*;", text, re.M | re.S)
    if not m:
        raise SystemExit("no module header found in the netlist")
    return m.group(1), [p.strip() for p in m.group(2).split(",") if p.strip()]


def build_top_ports(text):
    """Ports in module-header order, buses bit-expanded LSB-first (the order the
    I2C reference netlist used), then the two rails."""
    _, header = module_header_ports(text)
    widths = parse_widths(text)
    dirs = parse_dirs(text)
    ports, port_dir = [], {}
    for name in header:
        if name not in dirs:
            raise SystemExit(f"port {name!r} in the module header has no direction declaration")
        w = widths.get(name)
        if w is None:
            expanded = [name]
        else:
            hi, lo = w
            expanded = [f"{name}[{i}]" for i in range(min(hi, lo), max(hi, lo) + 1)]
        for p in expanded:
            ports.append(p)
            port_dir[p] = dirs[name]
    ports += ["VDD", "GND"]
    port_dir["VDD"] = port_dir["GND"] = "B"
    return ports, port_dir


# --------------------------------------------------------------------------
# assign-alias resolution
# --------------------------------------------------------------------------
class _UF:
    def __init__(self, prefer=frozenset()):
        self.p = {}
        self.prefer = prefer

    def find(self, k):
        self.p.setdefault(k, k)
        while self.p[k] != k:
            self.p[k] = self.p[self.p[k]]
            k = self.p[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if ra in self.prefer and rb not in self.prefer:
            self.p[rb] = ra
        elif rb in self.prefer and ra not in self.prefer:
            self.p[ra] = rb
        else:
            self.p[ra] = rb


_BARE = re.compile(r"^([A-Za-z_]\w*)$")
_INDEXED = re.compile(r"^([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$")
_PART = re.compile(r"^([A-Za-z_]\w*)\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]$")


def _expand_side(expr, widths):
    """A single assign operand -> the list of individual net names it covers,
    MSB..LSB.  None if this script does not understand the form."""
    expr = expr.strip()
    m = _INDEXED.match(expr)
    if m:
        return [f"{m.group(1)}[{m.group(2)}]"]
    m = _PART.match(expr)
    if m:
        base, hi, lo = m.group(1), int(m.group(2)), int(m.group(3))
        step = -1 if hi >= lo else 1
        return [f"{base}[{i}]" for i in range(hi, lo + step, step)]
    m = _BARE.match(expr)
    if m:
        return bits(m.group(1), widths.get(m.group(1)))
    return None


def build_alias_resolver(text, widths, prefer):
    """Union every `assign lhs = rhs;` bit-for-bit."""
    uf = _UF(prefer=set(prefer))
    for m in re.finditer(r"^\s*assign\s+(.+?)\s*=\s*(.+?)\s*;", text, re.M):
        lhs, rhs = m.group(1), m.group(2)
        if _tie_literal(rhs.strip()) is not None:
            continue                       # a constant tie, not an alias
        lb, rb = _expand_side(lhs, widths), _expand_side(rhs, widths)
        if lb is None or rb is None or len(lb) != len(rb):
            raise SystemExit(
                f"assign {lhs} = {rhs}; -- this script cannot bit-expand it "
                f"({lb} vs {rb}).  Extend _expand_side() rather than letting a "
                f"connection go missing from the LVS reference.")
        for a, b in zip(lb, rb):
            uf.union(a, b)
    return uf


def _tie_literal(net):
    m = _LITERAL_RE.match(net)
    if not m:
        return None
    width, base, digits = m.groups()
    value = int(digits.replace("_", ""), _LITERAL_BASE[base.lower()])
    if int(width) != 1:
        raise SystemExit(f"constant pin tie {net!r}: only 1-bit constants are supported")
    return "VDD" if value else "GND"


# --------------------------------------------------------------------------
# cell library
# --------------------------------------------------------------------------
def load_cell_bodies(path):
    """{name: (pin_order, body_text)} from lef/TR-1um_STDCELL.spice."""
    bodies = {}
    cur, buf = None, []
    for line in open(path).read().splitlines():
        s = line.strip()
        if s.lower().startswith(".subckt "):
            cur, buf = s.split()[1], [line]
        elif cur is not None:
            buf.append(line)
            if s.lower().startswith(".ends"):
                bodies[cur] = (buf[0].split()[2:], "\n".join(buf))
                cur = None
    return bodies


# --------------------------------------------------------------------------
def parse_instances(text, resolve):
    instances = []
    for m in re.finditer(
        r"^\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\(\s*(.*?)\)\s*;", text, re.M | re.S):
        typ, name, portlist = m.groups()
        if typ in _SKIP_TYPES:
            continue
        pins = {}
        for pm in re.finditer(r"\.(\w+)\s*\(\s*([^)]*?)\s*\)", portlist):
            pins[pm.group(1)] = resolve(pm.group(2))
        instances.append((typ, name, pins))
    return instances


def count_fill_instances(placement_json):
    counts = {}
    placement = json.load(open(placement_json))
    for row in placement["rows"]:
        for inst in row:
            if inst["type"] in FILL_DECAP:
                counts[inst["type"]] = counts.get(inst["type"], 0) + 1
    return counts


def wrap_port_line(prefix, items, width=110):
    lines, cur = [], prefix
    for p in items:
        add = ("" if cur[-1] in "( " else " ") + p
        if len(cur) + len(add) > width:
            lines.append(cur)
            cur = "+ " + p
        else:
            cur += add
    lines.append(cur)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=OUT_PATH)
    ap.add_argument("--sim-out", default=SIM_DIR,
                    help="also write a copy into xschem's simulation directory "
                         "(where layout/step10/simulation points), so the LVS run "
                         "finds it next to the cell exports")
    ap.add_argument("--no-sim-out", action="store_true")
    args = ap.parse_args()

    text = open(_cfg.NET_PATH).read()
    widths = parse_widths(text)
    top_ports, port_dir = build_top_ports(text)
    uf = build_alias_resolver(text, widths, prefer=top_ports)

    def resolve(net):
        net = net.strip()
        lit = _tie_literal(net)
        return lit if lit is not None else uf.find(net)

    instances = parse_instances(text, resolve)
    bodies = load_cell_bodies(CELL_SPICE)

    used = sorted({t for t, _, _ in instances})
    missing = [t for t in used if t not in bodies]
    if missing:
        raise SystemExit(f"{CELL_SPICE} has no body for {missing}; re-run scripts/gen_cell_spice.py")

    lines = [
        f"** {os.path.basename(args.out)} -- LVS reference netlist for the placed and",
        f"** routed core cell {_cfg.TOP_CELL_NAME}.",
        "** GENERATED by scripts/gen_lvs_spice.py; do not edit by hand.",
        f"**   netlist   : {os.path.relpath(_cfg.NET_PATH, _cfg.ROOT)}",
        f"**   cell bodies: {os.path.relpath(CELL_SPICE, _cfg.ROOT)}",
        f"**   fill counts: {os.path.relpath(_cfg.PLACEMENT_JSON, _cfg.ROOT)}",
    ]
    lines += wrap_port_line(f".subckt {_cfg.TOP_CELL_NAME} ", top_ports)
    lines += wrap_port_line("*.PININFO ", [f"{p}:{port_dir[p]}" for p in top_ports])

    n_forced = 0
    for typ, name, pins in instances:
        order, _ = bodies[typ]
        args_out = []
        for pname in order:
            if pname in POWER_PIN_NAMES:
                args_out.append(POWER_PIN_NAMES[pname])
                if pname not in pins:
                    n_forced += 1
            elif pname in pins:
                args_out.append(pins[pname])
            else:
                raise SystemExit(f"instance {name} ({typ}) has no connection for pin {pname}: {pins}")
        lines.append(f"x{name} " + " ".join(args_out) + f" {typ}")

    fill_counts = count_fill_instances(_cfg.PLACEMENT_JSON)
    lines += [
        "** fill-cell decoupling capacitors -- one PMOS+NMOS pair per placed",
        "** instance, L/W confirmed against lef/TR-1um_STDCELL.gds by",
        "** scripts/check_cell_spice.py.  FILL2 is a subcircuit CALL and FILL3 is",
        "** inline devices, matching how each extracts from the layout.",
    ]
    n_dev = n_sub = 0
    for typ, count in sorted(fill_counts.items()):
        if typ in FILL_AS_SUBCKT:
            # take the call order from the body's own .subckt line: xschem's
            # FILL2 export declares "GND VDD", the I2C .cir "VDD GND".
            order = " ".join(POWER_PIN_NAMES[p] for p in bodies[typ][0])
            for i in range(1, count + 1):
                lines.append(f"x{typ}_{i} {order} {typ}")
                n_sub += 1
            continue
        pl, pw, nl, nw = FILL_DECAP[typ]
        for i in range(1, count + 1):
            lines.append(f"M_{typ}_{i}_p VDD GND VDD VDD PMOS w={pw}u l={pl}u")
            lines.append(f"M_{typ}_{i}_n GND VDD GND GND NMOS w={nw}u l={nl}u")
            n_dev += 2
    lines += [".ends", ""]

    for typ in sorted(set(used) | (set(fill_counts) & FILL_AS_SUBCKT)):
        lines.append(bodies[typ][1])
        lines.append("")

    content = "\n".join(lines)
    outs = [args.out]
    if args.sim_out and not args.no_sim_out:
        outs.append(os.path.join(args.sim_out, _cfg.TOP_CELL_NAME + ".spice"))
    for path in outs:
        header = f"** {os.path.basename(path)} --"
        with open(path, "w") as f:
            f.write(re.sub(r"^\*\* \S+ --", header, content, count=1))
        print(f"wrote {path}")

    print(f"  {len(instances)} cell instance(s), {len(top_ports)} top port(s), "
          f"{len(used)} cell type(s)")
    print(f"  {n_forced} power-pin connection(s) tied to the rails (absent from the Verilog)")
    print(f"  fill: {fill_counts} -> {n_sub} FILL2 subcircuit call(s) + {n_dev} inline device(s)")
    aliased = sorted({k for k in uf.p if uf.find(k) != k})
    if aliased:
        print(f"  {len(aliased)} aliased net name(s) resolved: "
              + ", ".join(f"{k}->{uf.find(k)}" for k in aliased[:6])
              + (" ..." if len(aliased) > 6 else ""))


if __name__ == "__main__":
    main()
