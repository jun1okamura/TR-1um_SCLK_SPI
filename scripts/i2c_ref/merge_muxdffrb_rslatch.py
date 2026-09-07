"""
merge_muxdffrb_rslatch.py

Post-synthesis netlist transform: replaces the two recurring 2-gate
patterns identified in design_notes.md section 108.27 with the new
compound STDCELLs the user physically laid out this session (section
108.29's LEF regen confirmed both exist in TR-1um_STDCELL.gds/.lef):

  1. MUX2 -> DFFRB.D (single fanout: the MUX2's Y drives nothing else)
     becomes one MUXDFFRB instance (pins A,B,S,CK,RSTB,Q,QB -- the D/Y
     pair that used to be a real net becomes purely internal to the
     compound cell, see 108.27's internal-wiring writeup).
  2. A cross-coupled NOR2 pair (gate1.B == gate2.Y and gate2.B ==
     gate1.Y) becomes one RSLATCH instance (pins S,R,Q,QB). Which gate
     maps to R/Q vs S/QB is NOT arbitrary -- see _pick_latch_roles()'s
     docstring for why source order (not name matching) is the robust
     disambiguator.

This is a pure text transform on the synthesized Verilog netlist (same
technique as insert_row_buffers.py's redirect()): instance blocks are
located by regex and spliced out/in directly, preserving the exact
original net expressions (including bus-index syntax like
`rx_data_r[6]`) rather than routing through netlist_parser's
alias-canonicalized names, which exist only for *finding* the pairs
structurally.

Usage: python3 script/merge_muxdffrb_rslatch.py [--in PATH] [--out PATH]
Default in/out: ../src/i2c_slave_async_net_v10_deduped.v ->
                ../src/i2c_slave_async_net_v10_muxdffrb.v

v37 (design_notes.md 108.38): DEFAULT_IN changed from the raw Yosys
output (i2c_slave_async_net_v10.v) to dedup_gates.py's output
(i2c_slave_async_net_v10_deduped.v). 108.37 found the raw V10 netlist
had the SAME "opt_merge -share_all missing" duplicate-parallel-gate bug
originally diagnosed in section 42.1 (24 NOR2 all wired A=rst_scl_
domain_held/B=_143_, 9 AND2_X1 all wired A=busy/B=rst_n -- 31 fully
redundant instances) -- fixed at the synthesis-script level once
already (42.3) but NOT carried forward into whatever regenerated V10's
RTL/netlist. Running dedup_gates.py is now step 0 of the V10 pipeline,
BEFORE this script, every time -- never skip it even if the input
netlist "looks the same size as before," since this exact bug has now
recurred once already across a full netlist regeneration. See
run_v10_pipeline.py for the complete standardized step-by-step
invocation (dedup -> this script -> DFF_GROUPS reference -> row/BUFTH
buffering -> placement -> routing -> ripup/reroute -> DRC/connectivity).
"""
import argparse
import pathlib
import re
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from netlist_parser import parse_netlist  # noqa: E402

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

# 108.38: was i2c_slave_async_net_v10.v (raw Yosys output) -- see the
# module docstring's v37 note for why this now defaults to
# dedup_gates.py's output instead. RAW_V10_PATH kept below purely for
# reference/reproducibility (e.g. re-deriving the dedup diff).
RAW_V10_PATH = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v10.v")
DEFAULT_IN = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v10_deduped.v")
DEFAULT_OUT = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v10_muxdffrb.v")


def find_mux_dffrb_pairs(instances):
    """-> [(mux_name, dffrb_name), ...]: every MUX2 whose Y drives
    exactly one DFFRB.D pin and nothing else (single fanout -- the same
    structural test used to discover the 19 sites in 108.27, re-derived
    here on canonicalized nets so it stays correct if the netlist
    changes)."""
    driver = {}
    consumers = defaultdict(list)
    for typ, name, pins in instances:
        if "Y" in pins:
            driver[pins["Y"]] = (name, typ)
        for pname, net in pins.items():
            if pname != "Y":
                consumers[net].append((name, typ, pname))

    pairs = []
    for net, (drv_name, drv_typ) in driver.items():
        if drv_typ != "MUX2":
            continue
        cons = consumers.get(net, [])
        if len(cons) == 1 and cons[0][1] == "DFFRB" and cons[0][2] == "D":
            pairs.append((drv_name, cons[0][0]))
    return pairs


def find_rs_latch_pairs(instances):
    """-> [(q_side_name, qb_side_name), ...] for every cross-coupled NOR2
    pair (gateA.B == gateB.Y and gateB.B == gateA.Y).

    Role assignment (_pick_latch_roles): a NOR-based SR latch is
    Q=NOR(R,QB), QB=NOR(S,Q) -- NOT symmetric in isolation (R directly
    attacks Q's own gate, S directly attacks QB's own gate), but the
    *pair* (R,Q) vs (S,QB) IS interchangeable as a unit (swapping both
    simultaneously yields an equivalent circuit with the two roles
    relabeled). So the only thing that must be gotten right is that each
    gate's own A-input goes out as the SAME latch's R (if that gate's Y
    is chosen as Q) or S (if that gate's Y is chosen as QB) -- not which
    of the two arbitrary choices is picked. This implementation always
    designates the gate that appears FIRST in the source file (lower
    index in `instances`, i.e. Yosys/RTL declaration order) as the
    Q-side. This matches the RTL's own convention of always writing the
    "_q" instance immediately before its "_qn" partner (u_sda_q before
    u_sda_qn, u_lat_q before u_lat_qn, u_rst_stretch_q before
    u_rst_stretch_qn -- verified for all 3 sites in the current
    netlist), but does not actually depend on instance *names* matching
    that convention -- only on file order, which the RTL's own
    instantiation order structurally guarantees for any latch written
    the same way.
    """
    order = {name: i for i, (_t, name, _p) in enumerate(instances)}
    nor2 = [(name, pins) for typ, name, pins in instances if typ == "NOR2"]
    nor2_by_name = dict(nor2)

    found = set()
    pairs = []
    for n1, p1 in nor2:
        y1 = p1.get("Y")
        for n2, p2 in nor2:
            if n1 == n2:
                continue
            y2 = p2.get("Y")
            for f1 in ("A", "B"):
                for f2 in ("A", "B"):
                    if p1.get(f1) == y2 and p2.get(f2) == y1:
                        key = frozenset((n1, n2))
                        if key in found:
                            continue
                        found.add(key)
                        q_side, qb_side = (n1, n2) if order[n1] < order[n2] else (n2, n1)
                        pairs.append((q_side, qb_side))
    return pairs


_INST_BLOCK_RE_TMPL = r'\b{typ}\s+{name}\s*\([^;]*?\)\s*;'
# Yosys emits at most a couple of single-line (* ... *) attribute
# comments (module_not_derived / src) directly above each instance --
# matched by looking BACKWARD from the instance keyword with a bounded,
# anchored regex instead of folding it into the forward search. An
# earlier version used an open-ended `(\(\*.*?\*\)\s*\n\s*)?` PREFIX on
# the forward search instead: since `.*?` backtracks across newlines
# under re.S, and this file has one `(* ... *)` attribute before nearly
# every one of its ~170 instances, that prefix could (and did) satisfy
# itself by stretching all the way from the file's FIRST attribute
# comment (right after the `Generated by Yosys` header) to the one
# immediately preceding the actual target instance -- silently matching
# a multi-kilobyte span starting at file offset ~133 for every single
# instance, corrupting every extracted span. Anchoring the attribute
# lookup to end exactly where the instance keyword begins removes any
# possibility of that.
_ATTR_LINE_RE = re.compile(r'(?:\(\*[^\n]*\*\)\s*\n\s*)*$')


def _extract_block(src, typ, name):
    """-> (block_text, span) for the named instance, including any
    immediately-preceding (* ... *) attribute comment lines Yosys emits
    (module_not_derived / src), so removal doesn't leave orphaned
    attribute lines behind."""
    pat = re.compile(_INST_BLOCK_RE_TMPL.format(typ=re.escape(typ), name=re.escape(name)), re.S)
    m = pat.search(src)
    assert m, f"could not find instance block for {typ} {name}"
    start, end = m.span()
    am = _ATTR_LINE_RE.search(src, 0, start)
    if am:
        start = am.start()
    return src[start:end], (start, end)


def _pins_from_block(block):
    """-> {pin: raw_net_text} from a `.PIN(net)` block, preserving the
    exact original net expression text (bus indices etc.) verbatim."""
    return dict(re.findall(r'\.(\w+)\s*\(\s*([^()]*?)\s*\)', block))


def main(in_path=DEFAULT_IN, out_path=DEFAULT_OUT):
    net = parse_netlist(path=in_path)
    instances = net["instances"]
    print(f"parsed {len(instances)} instances from {in_path}")

    mux_dffrb_pairs = find_mux_dffrb_pairs(instances)
    rs_latch_pairs = find_rs_latch_pairs(instances)
    print(f"found {len(mux_dffrb_pairs)} MUX2->DFFRB.D pairs "
          f"-> MUXDFFRB, {len(rs_latch_pairs)} cross-coupled NOR2 pairs -> RSLATCH")

    src = open(in_path).read()

    new_blocks = []
    removed_spans = []

    muxdffrb_count = 0
    for mux_name, dffrb_name in mux_dffrb_pairs:
        mux_block, mux_span = _extract_block(src, "MUX2", mux_name)
        dff_block, dff_span = _extract_block(src, "DFFRB", dffrb_name)
        mux_pins = _pins_from_block(mux_block)
        dff_pins = _pins_from_block(dff_block)
        muxdffrb_count += 1
        iname = f"u_muxdffrb_{muxdffrb_count}"
        new_blocks.append(
            (min(mux_span[0], dff_span[0]),
             f"  MUXDFFRB {iname} (\n"
             f"    .A({mux_pins['A']}),\n"
             f"    .B({mux_pins['B']}),\n"
             f"    .S({mux_pins['S']}),\n"
             f"    .CK({dff_pins['CK']}),\n"
             f"    .RSTB({dff_pins['RSTB']}),\n"
             f"    .Q({dff_pins['Q']}),\n"
             f"    .QB({dff_pins['QB']}),\n"
             f"    .VDD(VDD),\n"
             f"    .GND(GND)\n"
             f"  );"))
        removed_spans.append(mux_span)
        removed_spans.append(dff_span)

    rslatch_count = 0
    for q_name, qb_name in rs_latch_pairs:
        q_block, q_span = _extract_block(src, "NOR2", q_name)
        qb_block, qb_span = _extract_block(src, "NOR2", qb_name)
        q_pins = _pins_from_block(q_block)
        qb_pins = _pins_from_block(qb_block)
        rslatch_count += 1
        iname = f"u_rslatch_{rslatch_count}"
        new_blocks.append(
            (min(q_span[0], qb_span[0]),
             f"  RSLATCH {iname} (\n"
             f"    .R({q_pins['A']}),\n"
             f"    .S({qb_pins['A']}),\n"
             f"    .Q({q_pins['Y']}),\n"
             f"    .QB({qb_pins['Y']}),\n"
             f"    .VDD(VDD),\n"
             f"    .GND(GND)\n"
             f"  );"))
        removed_spans.append(q_span)
        removed_spans.append(qb_span)

    # Rebuild: walk the source once, skipping any character inside a
    # removed span, and splicing in a new block whenever we reach the
    # (start-sorted) position where the FIRST of that pair's two removed
    # blocks began. Using min(span) as the insertion point keeps the new
    # instance physically close to where its constituents used to live
    # (easier diffing), while still being purely span-based (robust to
    # attribute-comment-line differences in exact block text).
    removed_spans.sort()
    insert_at = {pos: text for pos, text in new_blocks}

    out_parts = []
    cursor = 0
    i = 0
    pending_inserts = sorted(insert_at.items())
    ins_idx = 0
    for start, end in removed_spans:
        assert start >= cursor, f"overlapping removed span at {start} (cursor={cursor})"
        out_parts.append(src[cursor:start])
        while ins_idx < len(pending_inserts) and pending_inserts[ins_idx][0] <= start:
            out_parts.append(pending_inserts[ins_idx][1])
            ins_idx += 1
        cursor = end
    out_parts.append(src[cursor:])
    while ins_idx < len(pending_inserts):
        out_parts.append(pending_inserts[ins_idx][1])
        ins_idx += 1

    out_src = "".join(out_parts)

    with open(out_path, "w") as f:
        f.write(out_src)
    print(f"wrote {out_path}")
    print(f"  MUXDFFRB instances created: {muxdffrb_count}")
    print(f"  RSLATCH instances created:  {rslatch_count}")

    # sanity: re-parse the output and report cell-type counts
    net2 = parse_netlist(path=out_path)
    counts = defaultdict(int)
    for typ, _n, _p in net2["instances"]:
        counts[typ] += 1
    print(f"\npost-merge instance count: {len(net2['instances'])}")
    for typ in sorted(counts):
        print(f"  {typ:<12} {counts[typ]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=DEFAULT_IN)
    ap.add_argument("--out", dest="out_path", default=DEFAULT_OUT)
    args = ap.parse_args()
    main(in_path=args.in_path, out_path=args.out_path)
