"""
insert_bufth_scl_sda.py

Insert one BUFTH (hysteresis/Schmitt-trigger buffer, LEF/BUFTH.sch --
see design_notes.md BUFTH section) immediately downstream of the
top-level scl/sda_in pads, ahead of the existing per-row BUF_X1 fanout
stage. Per user request:

  "トップの sda_in と scl は一旦 BUFTH を通してから、各ROW毎に BUF_X1
   でバファーリングしてから優先レーンで配線ください。まずNETを直しま
   しょう。"

This script does ONLY the netlist ("NET") part; row-buffered/priority-
lane physical routing is a separate later step.

Method
------
scl:    already has per-row BUF_X1 fanout (u_buf_scl_row0..3, from
        insert_row_buffers.py / v3->v4). Only need to insert BUFTH
        between the raw top pin `scl` and those existing row buffers:
        rename every internal (non-declaration/non-port-list) use of
        the whole word `scl` to `scl_buf`, then add
        `BUFTH u_bufth_scl (.A(scl), .Y(scl_buf), .VDD(VDD), .GND(GND));`
        scl_n (INV_X1 u_inv_scl) already derives from scl_row2 (a row
        buffer output, not raw scl), so it automatically inherits the
        BUFTH-cleaned signal -- no separate handling needed.

sda_in: has NO row buffering yet (7 direct cell-pin sinks + 1 dead
        `assign shreg_next = {shreg[6:0], sda_in};` alias -- shreg_next
        is otherwise unused, so it is left reading whatever sda_in
        becomes and is NOT part of the row split). First rename raw
        `sda_in` -> `sda_in_buf` and add
        `BUFTH u_bufth_sda_in (.A(sda_in), .Y(sda_in_buf), .VDD(VDD), .GND(GND));`,
        then run the SAME row-partition-based BUF_X1 splitting
        insert_row_buffers.py used for scl (v3->v4): a reference
        fm_multiway_partition pass over the (post-BUFTH-insertion,
        pre-row-split) instance set, group sda_in_buf's real cell-pin
        sinks by row, one BUF_X1 per touched row
        (u_buf_sda_in_row{r} -> sda_in_row{r}), redirect each sink.

Input:  src/i2c_slave_async_net_v6.v   (left untouched)
Output: src/i2c_slave_async_net_v7.v
Also writes LEF/row_assignment_v7.json (full instance->row reference --
gen_placement_nrow_fm.py should reuse this instead of re-deriving from
scratch, same rationale/caveat as row_assignment_v4.json: re-running
fm_multiway_partition after adding a handful of small instances can
shift the recursive-bisection cut and relocate unrelated instances,
see design_notes.md section 40).

v8 (design_notes.md section 77.10): parametrized main() to accept
in_path/out_path/row_assignment_json instead of the hardcoded V6_PATH/
V7_PATH/ROW_ASSIGNMENT_JSON module-level defaults (which remain as the
historical v6->v7 defaults, unchanged) -- called as
main(in_path=..., out_path=..., row_assignment_json=...) to reuse this
script for the v8 netlist (walking-one bit_walk RTL, section 77.2)
without touching the v6->v7 default behavior.

Run (v6->v7, historical default): python3 script/insert_bufth_scl_sda.py
Run (any other netlist): import and call main(in_path=..., out_path=..., row_assignment_json=...)
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lef_parser import parse_lef  # noqa: E402
from netlist_parser import parse_netlist  # noqa: E402
from fm_partition import fm_multiway_partition  # noqa: E402

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

V6_PATH = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v6.v")
V7_PATH = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v7.v")
TMP_STAGE1_PATH = str(_REPO_ROOT / "src" / ".i2c_slave_async_net_v7_stage1_tmp.v")
ROW_ASSIGNMENT_JSON = str(_REPO_ROOT / "LEF" / "row_assignment_v7.json")

N_ROWS = 4
BUF_CELL = "BUF_X1"
BUFTH_CELL = "BUFTH"

HEADER_RE = re.compile(r"^\s*module\s+i2c_slave_async\s*\(")
DECL_LINES = {"input scl;", "wire scl;", "input sda_in;", "wire sda_in;"}


def rename_word(text, old, new):
    """Whole-word rename of `old` -> `new`, skipping the module port-list
    header line and the top-port input/wire declaration lines (so the
    top-level port itself keeps its original name -- only internal
    fanout is redirected)."""
    word_re = re.compile(r"\b" + re.escape(old) + r"\b")
    out_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if HEADER_RE.match(line) or stripped in DECL_LINES:
            out_lines.append(line)
        else:
            out_lines.append(word_re.sub(new, line))
    return "\n".join(out_lines)


def insert_wire_decls(src, wires):
    decl = "\n".join(f"  wire {w};" for w in wires)
    first_inst_m = re.search(r"\n  \w+ \w+\s*\(", src)
    return src[: first_inst_m.start()] + "\n" + decl + src[first_inst_m.start():]


def insert_instance_blocks(src, blocks):
    endmod_idx = src.rindex("endmodule")
    return src[:endmod_idx] + "\n" + "\n\n".join(blocks) + "\n\n" + src[endmod_idx:]


def redirect(src, instname, pinname, new_net):
    block_re = re.compile(r"(\b" + re.escape(instname) + r"\s*\(.*?\)\s*;)", re.S)
    bm = block_re.search(src)
    assert bm, f"could not find instance block for {instname}"
    block = bm.group(1)
    pin_re = re.compile(r"(\." + re.escape(pinname) + r"\s*\(\s*)([^()]*?)(\s*\))")
    new_block, n = pin_re.subn(lambda m2: m2.group(1) + new_net + m2.group(3), block, count=1)
    assert n == 1, f"could not redirect {instname}.{pinname}"
    return src[: bm.start()] + new_block + src[bm.end():]


def main(in_path=V6_PATH, out_path=V7_PATH, row_assignment_json=ROW_ASSIGNMENT_JSON,
         tmp_stage1_path=TMP_STAGE1_PATH, partition_fn=None, existing_part=None):
    """partition_fn (design_notes.md 108.31/108.33): optional
    (instances, widths, n_rows) -> {name: row} override for stage 2's
    reference partition, same rationale as insert_row_buffers.py's own
    partition_fn parameter -- defaults to plain fm_partition.
    fm_multiway_partition (original v6->v7/v8 behavior) so existing
    callers are unaffected.

    existing_part (108.33 -- supersedes partition_fn when given): a
    precomputed {instance_name: row} covering every instance already
    present in `in_path` (e.g. insert_row_buffers.py's own
    row_assignment_json output). When given, stage 2 does NOT recompute
    a fresh partition at all -- it reuses this assignment verbatim for
    every pre-existing instance, and places the 2 new BUFTH cells
    (u_bufth_scl/u_bufth_sda_in, which this function itself adds and so
    can never appear in `existing_part`) in whichever row currently has
    the fewest instances. This closes the exact instability gap
    insert_row_buffers.py's own docstring warns about (section 40): a
    FRESH fm_multiway_partition run on a netlist that only differs by a
    couple of newly-inserted small cells can reshuffle the recursive-
    bisection's top-level cut and relocate many unrelated instances to
    different rows -- confirmed happening here in practice on V10 (a
    fresh grouped-partition re-run after just adding 2 BUFTH cells moved
    clk156_rx_data from row3 to row0, shifted every sclN_txreg split,
    and even reopened a max_row_width violation that had already been
    resolved on the pre-BUFTH netlist). Passing existing_part instead of
    partition_fn is the correct way to call this for any netlist that
    already has a placement-committed row assignment from an earlier
    pipeline stage."""
    if partition_fn is None:
        partition_fn = fm_multiway_partition
    src = open(in_path).read()

    # ---- Stage 1: rename raw scl/sda_in fanout to scl_buf/sda_in_buf,
    # insert the two BUFTH instances. -----------------------------------
    src = rename_word(src, "scl", "scl_buf")
    src = rename_word(src, "sda_in", "sda_in_buf")

    src = insert_wire_decls(src, ["scl_buf", "sda_in_buf"])
    bufth_blocks = [
        f"  {BUFTH_CELL} u_bufth_scl (\n    .A(scl),\n    .Y(scl_buf),\n"
        f"    .VDD(VDD),\n    .GND(GND)\n  );",
        f"  {BUFTH_CELL} u_bufth_sda_in (\n    .A(sda_in),\n    .Y(sda_in_buf),\n"
        f"    .VDD(VDD),\n    .GND(GND)\n  );",
    ]
    src = insert_instance_blocks(src, bufth_blocks)

    n_scl_a = len(re.findall(r"\.A\(scl_buf\)", src))
    n_sda_pins = len(re.findall(r"\bsda_in_buf\b", src))
    print(f"stage1: {n_scl_a} scl_buf .A() sinks (expect <= N_ROWS={N_ROWS}: u_buf_scl_row0..{N_ROWS-1} BUF_X1, "
          f"one per row scl actually touches)")
    print(f"stage1: {n_sda_pins} total sda_in_buf references "
          f"(expect 9 = 7 cell-pin sinks + 1 dead-assign + BUFTH.Y decl... "
          f"see report below for exact split)")

    # Sanity: scl_buf's only remaining consumers should be the existing
    # per-row BUF_X1 buffers (u_buf_scl_row0..N_ROWS-1.A) inserted by
    # insert_row_buffers.py -- at most N_ROWS of them (one per row scl's
    # own fanout actually touches; a row with zero scl sinks simply gets
    # no buffer, so n_scl_a can be < N_ROWS but never >). Originally
    # hardcoded to "== 4" (N_ROWS=4 only); generalized (this session,
    # N_ROWS=5/6 feasibility experiments, design_notes 77.21-24) to the
    # actual N_ROWS module attribute so this script works for any row
    # count without re-editing this assertion each time.
    assert n_scl_a <= N_ROWS, (
        f"expected at most N_ROWS={N_ROWS} .A(scl_buf) sinks "
        f"(u_buf_scl_row0..{N_ROWS-1}); got {n_scl_a} -- scl's existing "
        f"row-buffer structure may have changed, review before proceeding"
    )

    open(tmp_stage1_path, "w").write(src)

    # ---- Stage 2: row-partition-based BUF_X1 split for sda_in_buf. -----
    macros = parse_lef()
    widths = {name: m["size"][0] for name, m in macros.items()}

    net = parse_netlist(path=tmp_stage1_path)
    instances = net["instances"]
    print(f"\nstage2: parsed {len(instances)} instances (post-BUFTH-insertion)")

    if existing_part is not None:
        new_names = [name for _t, name, _p in instances if name not in existing_part]
        part = dict(existing_part)
        if new_names:
            row_counts0 = {}
            for r in part.values():
                row_counts0[r] = row_counts0.get(r, 0) + 1
            for name in new_names:
                lightest = min(range(N_ROWS), key=lambda r: row_counts0.get(r, 0))
                part[name] = lightest
                row_counts0[lightest] = row_counts0.get(lightest, 0) + 1
        print(f"reusing precomputed row assignment for {len(existing_part)} instances "
              f"({len(new_names)} new instance(s) placed by lightest-row heuristic: "
              + ", ".join(f"{n}=row{part[n]}" for n in new_names) + ")")
    else:
        part = partition_fn(instances, widths, N_ROWS)
    row_counts = {}
    for name, r in part.items():
        row_counts[r] = row_counts.get(r, 0) + 1
    for r in sorted(row_counts):
        print(f"  row{r}: {row_counts[r]} instances")

    target_net = "sda_in_buf"
    sinks = []  # (instname, pinname, row)
    for typ, name, pins in instances:
        pin_meta = macros[typ]["pins"]
        for pname, net_name in pins.items():
            if net_name != target_net:
                continue
            info = pin_meta.get(pname)
            if info is None or info["direction"] != "INPUT":
                continue
            sinks.append((name, pname, part[name]))

    assert sinks, f"net {target_net!r} has no INPUT-direction cell-pin sinks"
    print(f"\n{target_net}: {len(sinks)} cell-pin sinks found: "
          + ", ".join(f"{n}.{p}(row{r})" for n, p, r in sinks))

    by_row = {}
    for instname, pinname, row in sinks:
        by_row.setdefault(row, []).append((instname, pinname))

    inserted_blocks = []
    new_wires = []
    branch_report = []
    for r in sorted(by_row):
        group = by_row[r]
        branch_net = f"sda_in_row{r}"
        iname = f"u_buf_sda_in_row{r}"
        for instname, pinname in group:
            src = redirect(src, instname, pinname, branch_net)
        inserted_blocks.append(
            f"  {BUF_CELL} {iname} (\n    .A({target_net}),\n    .Y({branch_net}),\n"
            f"    .VDD(VDD),\n    .GND(GND)\n  );"
        )
        new_wires.append(branch_net)
        branch_report.append((r, branch_net, len(group)))

    print(f"\nsda_in_buf: {len(by_row)} rows touched -> "
          + ", ".join(f"row{r}:{bn}({c})" for r, bn, c in branch_report))

    src = insert_wire_decls(src, new_wires)
    src = insert_instance_blocks(src, inserted_blocks)

    with open(out_path, "w") as f:
        f.write(src)
    print(f"\nwrote {out_path}")

    full_part = dict(part)
    for r, bn, _c in branch_report:
        full_part[f"u_buf_sda_in_row{r}"] = r
    with open(row_assignment_json, "w") as f:
        json.dump(full_part, f, indent=1)
    print(f"wrote {row_assignment_json} ({len(full_part)} instances)")

    print("\n=== summary ===")
    print("  u_bufth_scl      A=scl        Y=scl_buf      (feeds existing scl_row0-3 buffers)")
    print("  u_bufth_sda_in   A=sda_in     Y=sda_in_buf")
    for r, bn, c in branch_report:
        print(f"  u_buf_sda_in_row{r:<2} A=sda_in_buf Y={bn:<12} FO={c}")

    import os
    os.remove(tmp_stage1_path)


if __name__ == "__main__":
    main()
