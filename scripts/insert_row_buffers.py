#!/usr/bin/env python3
"""insert_row_buffers.py -- one buffer per placement row on a high-fanout net.

Generalised from TR-1um_Async_I2C/script/insert_row_buffers.py.  Same idea:
rather than splitting a clock-like net N ways by instance order (branches
that still span rows), give every placement ROW its own buffer driven from
the global net and feeding only that row's sinks.  Each branch then becomes
a genuinely row-local net that the channel router needs no special case for.

Differences from the original:
  * any net list, any buffer cell, any number of rows (nothing I2C-specific)
  * the row assignment may come from a JSON file ({instance: row} or
    {row: [instances]}), or -- when placement does not exist yet -- from an
    even split of each net's sinks into --rows groups, which is enough to
    get the buffer count and the netlist shape right for area estimation
  * reports the added cell count so gate_count.py can be re-run on the result

  usage:
    scripts/insert_row_buffers.py IN.v OUT.v --nets sclk_buf,shift_clk --rows 4
    scripts/insert_row_buffers.py IN.v OUT.v --nets sclk_buf \\
        --row-assignment layout/row_assignment.json
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlist_util as nu

OUT_PINS = {"MUX2": {"Y"}, "DFFRB": {"Q", "QB"}, "MUXDFFRB": {"Q", "QB"},
            "DFFS": {"Q", "QB"}, "RSLATCH": {"Q", "QB"}}


def load_rows(path):
    """Accept {inst: row} or {row: [inst, ...]}; return {inst: row}."""
    raw = json.load(open(path))
    if raw and isinstance(next(iter(raw.values())), list):
        return {i: int(r) for r, insts in raw.items() for i in insts}
    return {k: int(v) for k, v in raw.items()}


def insert(src, nets, rows=4, row_map=None, cell="BUF_X1",
           in_pin="A", out_pin="Y"):
    insts = nu.parse(src)
    out_pins = {i.cell: OUT_PINS.get(i.cell, {"Y"}) for i in insts}
    _, load = nu.drivers_and_loads(insts, out_pins)

    wires, blocks, edits, added = [], [], [], 0
    for net in nets:
        sinks = load.get(net, [])
        if not sinks:
            print(f"  !! net '{net}' has no sinks -- skipped")
            continue
        groups = defaultdict(list)
        for k, it in enumerate(sinks):
            groups[row_map.get(it.name, k * rows // len(sinks))
                   if row_map else k * rows // len(sinks)].append(it)

        for r in sorted(groups):
            local = f"{net}_row{r}"
            wires.append(local)
            blocks.append(f"  {cell} u_buf_{net}_row{r} "
                          f"(.{in_pin}({net}), .{out_pin}({local}));")
            added += 1
            for it in groups[r]:
                new = dict(it.conns)
                for pin, n in it.conns.items():
                    if n == net and pin not in out_pins.get(it.cell, {"Y"}):
                        new[pin] = local
                edits.append((it.span[0], it.span[1],
                              nu.render(it.cell, it.name, new, list(it.conns))))
        print(f"  {net}: {len(sinks)} sink(s) -> {len(groups)} row buffer(s)")

    src = nu.replace_spans(src, edits)
    src = nu.add_wires(src, wires)
    src = nu.add_instances(src, blocks)
    return src, added


def main(in_path, out_path, nets, rows=4, row_assignment_json=None,
         cell="BUF_X1"):
    row_map = load_rows(row_assignment_json) if row_assignment_json else None
    src = open(in_path).read()
    dst, added = insert(src, nets, rows=rows, row_map=row_map, cell=cell)
    open(out_path, "w").write(dst)
    print(f"{in_path} -> {out_path}: added {added} {cell} cell(s)")
    return added


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--nets", required=True)
    ap.add_argument("--rows", type=int, default=4)
    ap.add_argument("--row-assignment", default=None)
    ap.add_argument("--cell", default="BUF_X1")
    a = ap.parse_args()
    main(a.input, a.output, [n.strip() for n in a.nets.split(",") if n.strip()],
         rows=a.rows, row_assignment_json=a.row_assignment, cell=a.cell)
