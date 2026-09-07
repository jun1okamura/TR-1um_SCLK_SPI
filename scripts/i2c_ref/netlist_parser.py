"""
netlist_parser.py

Minimal structural-Verilog reader for src/i2c_slave_async_net.v (Yosys
output, named-port instantiation only -- ".PIN(net)" style). Returns
instance list (in file order) and, per instance, its {pin: net} map.
Also returns top-level module ports (for identifying primary I/O nets
that don't come from any cell instance).

IMPORTANT: Yosys emits large numbers of "assign A = B ;" net aliases
(design_notes.md section 9 -- e.g. "assign rw = rw_bit;",
"assign _005_ = _169_;"), including primary-port renames. A pin
connected to "_005_" and a pin connected to "_169_" are the SAME
electrical net if such an alias exists. Skipping this resolution (as
an earlier version of this script did) silently drops real
connections -- e.g. a gate's input wired to "_005_" would fail to be
recognized as sharing a net with whatever else uses "_169_", making
it look like a single-fanin stub when it is not. This module resolves
simple scalar aliases via union-find, the same skip rule
(brace-concat / literal-RHS assigns are skipped, matching
gen_schematic.py) used and validated earlier in this project. Pin nets
returned by parse_netlist() are already canonicalized.
"""
import re
from pathlib import Path

# 2026-09-02: made portable (was a hardcoded Claude-sandbox absolute
# path, broke the first time this chain was run locally on the user's
# own Mac -- see lef_parser.py's LEF_PATH for the same fix).
NET_PATH = str(Path(__file__).resolve().parent.parent / "src" / "i2c_slave_async_net.v")

_SKIP_TYPES = {"module", "endmodule", "input", "output", "wire", "assign", "reg", "inout"}


def _build_alias_resolver(text):
    parent = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for m in re.finditer(r"assign\s+(.+?)\s*=\s*(.+?);", text):
        lhs, rhs = m.group(1).strip(), m.group(2).strip()
        if "{" in lhs or "{" in rhs or "'" in rhs:
            continue  # bus-concat / literal assigns -- not simple aliases
        if re.match(r"^\w+(\[\d+\])?$", lhs) and re.match(r"^\w+(\[\d+\])?$", rhs):
            union(lhs, rhs)

    return find


def parse_netlist(path=NET_PATH):
    text = open(path).read()
    resolve = _build_alias_resolver(text)

    top_m = re.search(r"module\s+(\S+)\s*\((.*?)\)\s*;", text, re.S)
    top_name = top_m.group(1)
    top_ports = [p.strip().split("[")[0].strip() for p in top_m.group(2).split(",")]

    instances = []  # (type, name, {pin: net})
    for m in re.finditer(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(.*?)\)\s*;",
        text, re.M | re.S,
    ):
        typ, name, portlist = m.groups()
        if typ in _SKIP_TYPES:
            continue
        pins = {}
        for pm in re.finditer(r"\.(\w+)\s*\(\s*([^)]*?)\s*\)", portlist):
            pins[pm.group(1)] = resolve(pm.group(2).strip())
        instances.append((typ, name, pins))

    return {"top_name": top_name, "top_ports": top_ports, "instances": instances}


if __name__ == "__main__":
    d = parse_netlist()
    print("top:", d["top_name"], d["top_ports"])
    print("instance count:", len(d["instances"]))
    print(d["instances"][0])
