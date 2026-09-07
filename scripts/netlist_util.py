#!/usr/bin/env python3
"""netlist_util.py -- minimal parser/writer for Yosys `write_verilog -noattr`
gate-level netlists.

Deliberately text-based and non-destructive: transforms edit the original
source text rather than re-emitting it, so comments, formatting and anything
this parser does not understand survive untouched.  Shared dependency of
merge_muxdffrb.py / insert_row_buffers.py / insert_bufth.py / gate_count.py.

Generalised from TR-1um_Async_I2C/script/{insert_row_buffers,dedup_gates}.py:
nothing here is specific to one design, one cell library or one net name.
"""
import re

# CELLTYPE inst ( .pin(net), .pin(net) );   -- possibly spanning lines
INST_RE = re.compile(
    r'^[ \t]*([A-Z][A-Za-z0-9_]*)[ \t]+(\\?[^\s(]+)[ \t]*\((.*?)\)[ \t]*;[ \t]*$',
    re.MULTILINE | re.DOTALL)
CONN_RE = re.compile(r'\.([A-Za-z0-9_]+)\s*\(\s*([^)]*?)\s*\)')


class Instance:
    __slots__ = ("cell", "name", "conns", "span", "text")

    def __init__(self, cell, name, conns, span, text):
        self.cell, self.name, self.conns = cell, name, conns
        self.span, self.text = span, text

    def __repr__(self):
        return f"<{self.cell} {self.name}>"


def parse(src):
    """Return the list of cell instances found in `src`."""
    out = []
    for m in INST_RE.finditer(src):
        cell, name, body = m.group(1), m.group(2), m.group(3)
        conns = {p: n.strip() for p, n in CONN_RE.findall(body)}
        if not conns:
            continue                      # not a cell instance
        out.append(Instance(cell, name, conns, m.span(), m.group(0)))
    return out


def drivers_and_loads(insts, out_pins):
    """Map net -> driving instance and net -> list of loading instances.

    `out_pins` maps a cell type to the set of its output pin names; a pin not
    listed there is treated as an input.
    """
    drv, load = {}, {}
    for it in insts:
        outs = out_pins.get(it.cell, set())
        for pin, net in it.conns.items():
            if pin in outs:
                drv[net] = it
            else:
                load.setdefault(net, []).append(it)
    return drv, load


def render(cell, name, conns, order=None):
    """Format one instance the way Yosys writes them."""
    pins = order or list(conns)
    body = ", ".join(f".{p}({conns[p]})" for p in pins if p in conns)
    return f"  {cell} {name} ({body});"


def replace_spans(src, edits):
    """Apply [(start, end, replacement_or_None), ...] to `src`.

    A None replacement deletes the line.  Spans must not overlap.
    """
    for start, end, rep in sorted(edits, key=lambda e: -e[0]):
        if rep is None:
            nl = src.find("\n", end)
            end = nl + 1 if nl >= 0 else end
            src = src[:start] + src[end:]
        else:
            src = src[:start] + rep + src[end:]
    return src


def add_wires(src, names):
    """Declare extra wires just after the module header."""
    if not names:
        return src
    m = re.search(r'^module\s+[^;]*;\s*$', src, re.MULTILINE)
    if not m:
        raise RuntimeError("module header not found")
    decl = "".join(f"  wire {n};\n" for n in names)
    return src[:m.end() + 1] + decl + src[m.end() + 1:]


def add_instances(src, blocks):
    """Insert instance text just before `endmodule`."""
    if not blocks:
        return src
    m = re.search(r'^endmodule', src, re.MULTILINE)
    if not m:
        raise RuntimeError("endmodule not found")
    return src[:m.start()] + "\n".join(blocks) + "\n" + src[m.start():]
