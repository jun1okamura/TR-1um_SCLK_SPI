#!/usr/bin/env python3
"""insert_bufth.py -- insert a threshold buffer on chosen top-level inputs.

Generalised from TR-1um_Async_I2C/script/insert_bufth_scl_sda.py, which
hard-coded the two I2C nets.  Here any net list is accepted, the cell is a
parameter, and the port list / declarations are left alone -- only internal
uses are redirected:

    sclk ---> BUFTH u_bufth_sclk ---> sclk_buf ---> (every internal sink)

Run it BEFORE insert_row_buffers.py so the row fanout stage is driven from
the buffered signal.

  usage:  scripts/insert_bufth.py IN.v OUT.v --nets sclk,cs_n,sdio_in
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlist_util as nu


def rename_internal(src, old, new):
    """Rename whole-word `old` to `new` everywhere except the module header
    and the port/wire/input/output declarations."""
    lines = src.split("\n")
    pat = re.compile(r'\b%s\b' % re.escape(old))
    decl = re.compile(r'^\s*(module|input|output|inout|wire|reg)\b')
    for i, ln in enumerate(lines):
        if decl.match(ln):
            continue
        lines[i] = pat.sub(new, ln)
    return "\n".join(lines)


def insert(src, nets, cell="BUFTH", suffix="_buf", in_pin="A", out_pin="Y",
           prefix="u_bufth_"):
    wires, blocks, done = [], [], []
    for net in nets:
        if not re.search(r'\b%s\b' % re.escape(net), src):
            print(f"  !! net '{net}' not found -- skipped")
            continue
        buf = net + suffix
        src = rename_internal(src, net, buf)
        wires.append(buf)
        blocks.append(f"  {cell} {prefix}{net} (.{in_pin}({net}), .{out_pin}({buf}));")
        done.append(net)
        print(f"  {cell} on '{net}' -> '{buf}'")
    src = nu.add_wires(src, wires)
    src = nu.add_instances(src, blocks)
    return src, done


def main(in_path, out_path, nets, **kw):
    src = open(in_path).read()
    dst, done = insert(src, nets, **kw)
    open(out_path, "w").write(dst)
    print(f"{in_path} -> {out_path}: buffered {len(done)} net(s)")
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--nets", required=True,
                    help="comma-separated top-level input nets")
    ap.add_argument("--cell", default="BUFTH")
    ap.add_argument("--suffix", default="_buf")
    a = ap.parse_args()
    main(a.input, a.output, [n.strip() for n in a.nets.split(",") if n.strip()],
         cell=a.cell, suffix=a.suffix)
