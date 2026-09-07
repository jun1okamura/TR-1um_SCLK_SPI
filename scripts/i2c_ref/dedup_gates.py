"""
dedup_gates.py

Post-ABC common-subexpression-elimination pass for synthesized netlists.

Background (design_notes.md section 77.29): investigating why the v4/
fulllib netlist (169 instances at N_ROWS=4, similar total count to v7's
155) failed to route at row_width=1620.0um while v7 succeeded, we found
ABC's technology mapping (`abc -liberty ...`) had duplicated small
combinational gates that feed DFFRB RSTB pins -- instead of computing
"busy & rst_n" ONCE and fanning the result out to all sinks (what v7's
synthesis did: two shared trunk nets RSTB1 fanout=24, RSTB2 fanout=9),
our netlist has ~33 separate single-fanout copies of the SAME gate
(identical cell type + identical input connections, only the output net
name differs), each independently wired back to busy/rst_n/start_pulse.
This inflated those three backbone nets' direct fanout from ~3 (v7) to
11/11/26 (ours), each duplicate needing its own long-haul route to
wherever its one sink landed -- concentrating traffic in the scarce
X-direction jog corridors near the TAP columns and causing the "no clear
X found" routing failure.

A plain `opt_merge` pass added after `abc` in the yosys script did NOT
collapse these (tested empirically, 0 cells removed) -- reason not
fully understood, so this script performs the same CSE manually and
deterministically on the written netlist text, via netlist_parser.py's
already-alias-resolved instance list.

108.38 (design_notes.md): STANDARDIZED as step 0 of the V10 (and every
future version's) placement/routing pipeline, run unconditionally
against the raw synthesized netlist BEFORE any other transform
(merge_muxdffrb_rslatch.py, DFF_GROUPS clustering, row-buffer
insertion, etc.). This was originally written for a one-off v7-era
netlist (see the "v4/fulllib" note above) and NOT re-run when V10's RTL
was regenerated -- 108.37 found the exact same duplicate-gate pattern
(same root cause as section 42.1, same 24-NOR2/9-AND2_X1 shape) had
silently reappeared in the raw i2c_slave_async_net_v10.v, and was the
TRUE root cause of the channel congestion / short-circuit chain that
consumed most of a session (108.34-108.36) to chase at the placement/
routing level before this was found. Never skip this step again, even
when a netlist "looks about the same size as last time" -- that is
exactly what a silently-reintroduced missing-opt_merge regression looks
like. See run_v10_pipeline.py for the full standardized invocation
order.

Method
------
1. Parse the netlist (parse_netlist(), pins alias-resolved).
2. For each instance whose TYPE is in COMBINATIONAL_CELLS (single named
   output pin OUT_PIN[type], typically "Y"), build a dedup key =
   (type, tuple of sorted (pin, net) for every pin EXCEPT the output
   pin). Instances sharing a key compute the identical function of the
   identical inputs -- true duplicates.
3. Within each group of size > 1: keep the FIRST (file-order) instance
   as canonical. For every other ("redundant") instance in the group,
   record output_net(redundant) -> output_net(canonical) as a rename,
   UNLESS output_net(redundant) is a top-level port net (never rename a
   primary port away -- if the canonical's output happens to be the
   port net instead, that direction is fine and handled automatically
   since port names sort first in practice; as an extra safety check,
   if BOTH nets are ports this cell group is skipped entirely).
4. Text surgery on the original (unparsed) source: for every redundant
   instance, delete its "TYPE NAME (...);" block and its
   "wire OUTNET;" declaration (if present as a bare scalar wire decl),
   then globally rename all remaining whole-word occurrences of
   OUTNET -> canonical OUTNET.

Run: python3 script/dedup_gates.py [<in.v> <out.v>]
     (defaults to i2c_slave_async_net_v10.v -> _v10_deduped.v, 108.38)
Or:  import and call main(in_path=..., out_path=...)
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from netlist_parser import parse_netlist  # noqa: E402

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

# 108.38: standardized V10 step-0 default -- raw Yosys output in,
# dedup_gates.py's cleaned netlist out. Positional CLI args still
# override both (`python3 dedup_gates.py <in.v> <out.v>`).
DEFAULT_IN = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v10.v")
DEFAULT_OUT = str(_REPO_ROOT / "src" / "i2c_slave_async_net_v10_deduped.v")

# cell type -> name of its single output pin. Only pure single-output
# combinational cells are safe to CSE this way; DFFRB/DFFS (state) and
# any multi-output cell are intentionally excluded.
OUT_PIN = {
    "INV_X1": "Y", "BUF_X1": "Y", "BUFTH": "Y",
    "AND2_X1": "Y", "AND3_X1": "Y", "AND4_X1": "Y",
    "OR2": "Y", "OR3": "Y", "OR4": "Y",
    "NAND2": "Y", "NAND3": "Y", "NAND4": "Y",
    "NOR2": "Y", "NOR3": "Y", "NOR4": "Y",
    "XOR2": "Y", "XNOR2": "Y",
    "MUX2": "Y",
    "DEL1": "Y", "DEL2": "Y", "DEL4": "Y",
}


def main(in_path, out_path):
    text = open(in_path).read()
    net = parse_netlist(path=in_path)
    top_ports = set(net["top_ports"])
    instances = net["instances"]

    groups = {}
    for typ, name, pins in instances:
        out_pin = OUT_PIN.get(typ)
        if out_pin is None or out_pin not in pins:
            continue
        key = (typ, tuple(sorted((p, n) for p, n in pins.items() if p != out_pin)))
        groups.setdefault(key, []).append((name, pins[out_pin]))

    rename = {}          # redundant_net -> canonical_net
    dead_instances = set()  # instance names to delete
    dup_group_count = 0
    dup_instance_count = 0

    for key, members in groups.items():
        if len(members) < 2:
            continue
        # skip if 2+ distinct output nets are BOTH primary ports (can't
        # rename either away safely) -- pick a canonical preferring a
        # port net if exactly one member is a port.
        port_members = [m for m in members if m[1] in top_ports]
        if len(port_members) >= 2:
            continue
        canonical = port_members[0] if port_members else members[0]
        canonical_net = canonical[1]
        dup_group_count += 1
        for name, out_net in members:
            if (name, out_net) == canonical:
                continue
            if out_net in top_ports:
                continue  # shouldn't happen given the check above, but be safe
            rename[out_net] = canonical_net
            dead_instances.add(name)
            dup_instance_count += 1

    print(f"found {dup_group_count} duplicate group(s), "
          f"{dup_instance_count} redundant instance(s) to remove")

    # ---- text surgery -----------------------------------------------
    for name in dead_instances:
        # remove "TYPE NAME (\n ... \n);" block (and an immediately
        # preceding (* src = ... *) attribute line, if present)
        pat = re.compile(
            r"(?:\(\*[^\n]*\*\)\s*\n)?"
            r"^\s*[A-Za-z_]\w*\s+" + re.escape(name) + r"\s*\([^;]*?\)\s*;\s*\n",
            re.M | re.S,
        )
        text, n = pat.subn("", text, count=1)
        if n == 0:
            print(f"  WARNING: could not find instance block for {name!r}")

    for out_net in set(rename.keys()):
        wire_pat = re.compile(r"^\s*wire\s+" + re.escape(out_net) + r"\s*;\s*\n", re.M)
        text, n = wire_pat.subn("", text, count=1)
        # (n==0 is fine -- some nets may not have a bare wire decl, e.g.
        # bus bits declared as part of a vector)

    # rename every remaining whole-word occurrence, longest-first so a
    # net name that is a prefix of another doesn't get partially eaten
    # (not expected with yosys's _NNN_ naming, but safe regardless)
    for old_net in sorted(rename, key=len, reverse=True):
        new_net = rename[old_net]
        text = re.sub(r"\b" + re.escape(old_net) + r"\b", new_net, text)

    open(out_path, "w").write(text)
    print(f"wrote {out_path}")
    return {"groups": dup_group_count, "removed": dup_instance_count, "rename": rename}


if __name__ == "__main__":
    in_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IN
    out_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    main(in_path, out_path)
