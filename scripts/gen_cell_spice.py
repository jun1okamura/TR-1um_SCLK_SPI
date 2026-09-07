#!/usr/bin/env python3
"""gen_cell_spice.py -- build lef/TR-1um_STDCELL.spice, the project's
schematic-side transistor-level standard-cell library.

WHY THIS EXISTS
---------------
The LVS reference netlist has to be a *schematic-side* description of every
placed cell.  The I2C project took those bodies from xschem's own per-cell
exports under ~/.xschem/simulations/ (see scripts/i2c_ref/gen_lvs_spice_v10.py),
a directory that lives outside any repository and is not reachable from this
project.  Rather than hand-copy transistors -- the one thing an LVS reference
must never be -- this script lifts each cell's `.subckt ... .ends` block
verbatim out of

    TR-1um_I2C_2026/src/tr_1um_i2c_slave_async.cir

which is the *as-submitted* chip-level LVS netlist of the I2C MPW entry: the
same standard-cell library, and one that a real KLayout LVS run has already
matched device-for-device against the same lef/TR-1um_STDCELL.gds this project
uses (design_notes.md 108.51).  That makes it the strongest available source.
Override the path with $I2C_REF_CIR if the sibling checkout lives elsewhere.

The output file IS checked in, so the project stays self-contained and this
script only has to be re-run when the cell library itself changes.

CELLS NOT IN THE I2C NETLIST
----------------------------
BUF_X2 is new in this project (added to lef/TR-1um_STDCELL.gds this session),
so the I2C .cir has no body for it and one is defined below.  It is *not* a
transcription of the layout: BUF_X2 is by construction BUF_X1 with a second
output-stage inverter wired in parallel, which is what the "X2" drive means.
scripts/check_cell_spice.py then checks that definition against the real GDS
geometry independently.

  NOTE: lef/BUF_X2.sch as shipped by the user describes only FOUR transistors
  -- it is a copy of BUF_X1's schematic -- while the DRC-clean BUF_X2 in
  lef/TR-1um_STDCELL.gds has SIX (3 PMOS fingers of W=10.2u, 3 NMOS of
  W=3.4u).  That schematic would fail LVS; see design_notes.md.

TAP2 has no transistors at all (well/substrate taps only) and therefore gets no
subckt -- it simply contributes nothing to the netlist.  FILL3 likewise has no
subckt here: like the I2C flow, its two decap devices are emitted inline in the
top cell by gen_lvs_spice.py, because that is how the layout extracts them.

CROSS-CHECK AGAINST XSCHEM
--------------------------
When xschem's own per-cell exports are reachable (layout/step10/simulation ->
~/.xschem/simulations, or $XSCHEM_SIM_DIR) every emitted body is compared
against <CELL>.spice there, device for device.  Those files are the schematics'
own output, so they are the closest thing to a primary source; the .cir is
preferred as the body to EMIT only because it is additionally LVS-proven, and
because the compound cells arrive there already in the flat form the layout
extraction needs.  A cell the exports and the .cir disagree on is reported.

Two differences are expected and reported as such rather than as errors:

  * MUXDFFRB's export is hierarchical -- `.subckt MUXDFFRB ... x1 DFFRB / x2
    MUX2 ... .ends` -- while the layout draws it as one flat leaf cell.  The
    comparison flattens the export the same way the I2C flow did (x1_/x2_
    prefixes on each call's private nodes) before comparing.
  * FILL2's export declares its pins as `GND VDD`, the .cir as `VDD GND`.  Same
    circuit, and gen_lvs_spice.py takes the call order from whichever body it
    is given, so either is safe.

  usage:  scripts/gen_cell_spice.py [--check]
"""
import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

OUT_PATH = os.path.join(_cfg.ROOT, "lef", "TR-1um_STDCELL.spice")

DEFAULT_REF_CIR = os.path.join(
    os.path.dirname(_cfg.ROOT), "TR-1um_I2C_2026", "src", "tr_1um_i2c_slave_async.cir"
)
REF_CIR = os.environ.get("I2C_REF_CIR", DEFAULT_REF_CIR)

# What the generated file records as the source.  Deliberately NOT the absolute
# path: that differs between machines (and between a sandbox and the user's
# disk), which would make --check report a spurious "out of date".
REF_LABEL = "TR-1um_I2C_2026/src/" + os.path.basename(REF_CIR)


def _sim_dir():
    """xschem's own per-cell exports, if this machine has them."""
    for cand in (os.environ.get("XSCHEM_SIM_DIR"),
                 os.path.join(_cfg.LAYOUT, "step10", "simulation"),
                 os.path.join(_cfg.ROOT, "lef", "simulation"),
                 os.path.expanduser("~/.xschem/simulations")):
        if cand and os.path.isdir(cand):
            return cand
    return None


SIM_DIR = _sim_dir()

# Cells that the .cir does not define and this project has to supply itself.
# Keep every one of these justified in the module docstring.
LOCAL_BODIES = {
    "BUF_X2": """.subckt BUF_X2 VDD A Y GND
*.PININFO A:I Y:O VDD:B GND:B
* BUF_X1's input inverter ...
MM7 net1 A VDD VDD PMOS w=10.2u l=1u
MM3 net1 A GND GND NMOS w=3.4u l=1u
* ... driving two output inverters in parallel (that is the "X2").
MM1 Y net1 VDD VDD PMOS w=10.2u l=1u
MM2 Y net1 GND GND NMOS w=3.4u l=1u
MM4 Y net1 VDD VDD PMOS w=10.2u l=1u
MM5 Y net1 GND GND NMOS w=3.4u l=1u
.ends""",
}

# Cells that are placed but contribute no .subckt.  Documented above.
NO_DEVICE_CELLS = {"TAP2"}
INLINE_DECAP_CELLS = {"FILL3"}

HEADER = """** TR-1um_STDCELL.spice -- schematic-side transistor-level bodies for the
** standard cells this project places.  GENERATED by scripts/gen_cell_spice.py;
** do not edit by hand.
**
** Source for every cell marked "from I2C .cir":
**   {ref}
** (the as-submitted TR-1um_I2C_2026 chip LVS netlist -- same cell library,
** already matched device-for-device against lef/TR-1um_STDCELL.gds by a real
** KLayout LVS run).  Bodies are copied verbatim, never retyped.
**
** TAP2 has no devices and no subckt.  FILL3's decap pair is emitted inline in
** the top cell by scripts/gen_lvs_spice.py, matching the layout extraction.
"""


def parse_subckts(text):
    """Return {name: (decl_pins, [lines including .subckt/.ends])}."""
    out = {}
    cur = None
    buf = []
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith(".subckt "):
            cur = s.split()[1]
            buf = [line]
        elif cur is not None:
            buf.append(line)
            if s.lower().startswith(".ends"):
                out[cur] = (buf[0].split()[2:], list(buf))
                cur = None
    return out


def used_cell_types():
    """Every cell type instantiated by the netlist, plus the fill/tap cells the
    placement adds."""
    import json
    text = open(_cfg.NET_PATH).read()
    skip = {"module", "endmodule", "input", "output", "wire", "assign", "reg", "inout"}
    types = set()
    for m in re.finditer(
        r"^\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\(\s*(.*?)\)\s*;", text, re.M | re.S
    ):
        if m.group(1) not in skip:
            types.add(m.group(1))
    placement = json.load(open(_cfg.PLACEMENT_JSON))
    for row in placement["rows"]:
        for inst in row:
            types.add(inst["type"])
    return types


def build():
    ref_text = open(REF_CIR).read()
    ref = parse_subckts(ref_text)

    types = used_cell_types()
    wanted = sorted(t for t in types if t not in NO_DEVICE_CELLS and t not in INLINE_DECAP_CELLS)

    missing = [t for t in wanted if t not in ref and t not in LOCAL_BODIES]
    if missing:
        raise SystemExit(
            f"no transistor-level body available for {missing} -- neither in\n"
            f"  {REF_CIR}\n"
            f"nor in this script's LOCAL_BODIES"
        )

    chunks = [HEADER.format(ref=REF_LABEL)]
    emitted = {}
    for typ in wanted:
        if typ in ref:
            emitted[typ] = "\n".join(ref[typ][1])
            chunks.append(f"** {typ}: from I2C .cir\n" + emitted[typ])
        else:
            emitted[typ] = LOCAL_BODIES[typ]
            chunks.append(f"** {typ}: defined locally (see gen_cell_spice.py)\n" + emitted[typ])
    return "\n\n".join(chunks) + "\n", wanted, emitted


# --------------------------------------------------------------------------
# cross-check against xschem's own per-cell exports
# --------------------------------------------------------------------------
def _first_block(path):
    """(pin order, body lines) of the FIRST .subckt in an xschem export.  The
    exports append copies of every sub-cell after the outer block; only the
    outer one is this cell."""
    lines = open(path).read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().lower().startswith(".subckt "))
    end = next(i for i in range(start, len(lines)) if lines[i].strip().lower().startswith(".ends"))
    pins = lines[start].split()[2:]
    body = [l.strip() for l in lines[start + 1:end] if l.strip() and not l.strip().startswith("*")]
    return pins, body


def _devices(body_lines, pin_map=None, prefix=None, sim_dir=None):
    """Flatten a body to a list of (nodes, model, params) device tuples.
    Lines starting with 'x' are subcircuit calls and get inlined from
    <sim_dir>/<TYPE>.spice, exactly as the I2C flow did: each call's private
    nodes take a per-call prefix so two copies of the same sub-cell do not
    collide, while the nets passed in at the call site keep their names."""
    out = []
    for line in body_lines:
        t = line.split()
        if not t:
            continue
        if t[0][0] in "Xx":
            sub = t[-1]
            args = t[1:-1]
            sub_pins, sub_body = _first_block(os.path.join(sim_dir, sub + ".spice"))
            if len(args) != len(sub_pins):
                raise SystemExit(f"{line!r}: {len(args)} args for {sub}'s {len(sub_pins)} pins")
            out += _devices(sub_body, dict(zip(sub_pins, args)),
                            prefix=t[0], sim_dir=sim_dir)
            continue
        nodes = t[1:5]
        if pin_map is not None:
            nodes = [pin_map.get(n, f"{prefix}_{n}") for n in nodes]
        out.append((tuple(nodes), t[5], tuple(sorted(t[6:]))))
    return out


def verify_against_xschem(bodies):
    """bodies: {type: emitted subckt text}.  Returns (n_ok, n_diff, n_absent)."""
    if SIM_DIR is None:
        print("xschem exports not reachable -- skipping the cross-check")
        return 0, 0, 0
    print(f"cross-check against {SIM_DIR}")
    n_ok = n_diff = n_absent = 0
    for typ in sorted(bodies):
        path = os.path.join(SIM_DIR, typ + ".spice")
        if not os.path.exists(path):
            print(f"  --   {typ:10s} no {typ}.spice there (nothing to check against)")
            n_absent += 1
            continue
        sim_pins, sim_body = _first_block(path)
        mine = bodies[typ].splitlines()
        my_pins = mine[0].split()[2:]
        my_body = [l.strip() for l in mine[1:-1] if l.strip() and not l.strip().startswith("*")]
        hier = any(l.split()[0][0] in "Xx" for l in sim_body if l.split())
        sim_dev = sorted(_devices(sim_body, sim_dir=SIM_DIR))
        my_dev = sorted(_devices(my_body, sim_dir=SIM_DIR))
        notes = []
        if sim_pins != my_pins:
            notes.append(f"pin order {sim_pins} vs {my_pins}")
        if hier:
            notes.append("export is hierarchical, flattened to compare")
        if sim_dev == my_dev:
            n_ok += 1
            print(f"  ok   {typ:10s} {len(my_dev):2d} device(s)"
                  + (("  (" + "; ".join(notes) + ")") if notes else ""))
        else:
            n_diff += 1
            print(f"  DIFF {typ:10s} export {len(sim_dev)} device(s), emitted {len(my_dev)}")
            for d in sorted(set(sim_dev) - set(my_dev)):
                print(f"         only in {typ}.spice : {d}")
            for d in sorted(set(my_dev) - set(sim_dev)):
                print(f"         only in ours      : {d}")
    return n_ok, n_diff, n_absent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report whether the checked-in file is up to date; write nothing")
    ap.add_argument("--verify-only", action="store_true",
                    help="only run the xschem cross-check")
    args = ap.parse_args()

    content, wanted, emitted = build()
    if args.verify_only:
        _, n_diff, _ = verify_against_xschem(emitted)
        raise SystemExit(1 if n_diff else 0)
    if args.check:
        old = open(OUT_PATH).read() if os.path.exists(OUT_PATH) else None
        if old == content:
            print(f"up to date: {OUT_PATH} ({len(wanted)} cell bodies)")
        else:
            print(f"OUT OF DATE: {OUT_PATH}")
            raise SystemExit(1)
        return

    with open(OUT_PATH, "w") as f:
        f.write(content)
    local = sorted(set(wanted) & set(LOCAL_BODIES))
    print(f"wrote {OUT_PATH}: {len(wanted)} cell body(ies) "
          f"({len(wanted) - len(local)} from {os.path.basename(REF_CIR)}, "
          f"{len(local)} local: {local})")
    print()
    n_ok, n_diff, n_absent = verify_against_xschem(emitted)
    if n_diff:
        raise SystemExit(f"\n{n_diff} cell(s) disagree with xschem's export -- resolve before LVS")
    if n_ok or n_absent:
        print(f"\n{n_ok} cell(s) match xschem's export"
              + (f", {n_absent} not exported yet" if n_absent else ""))


if __name__ == "__main__":
    main()
