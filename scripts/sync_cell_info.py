#!/usr/bin/env python3
"""sync_cell_info.py -- re-measure the standard cells and update cell_info.json.

Run this after ANY change to lef/TR-1um_STDCELL.gds (a new cell, a resized
cell, a fixed abutment).  It re-measures every cell's bounding box from the
GDS, refreshes transistor counts from the cells' extracted SPICE when those
are reachable, and merges the result into lef/cell_info.json -- which is what
scripts/gen_liberty.py and scripts/gate_count.py both read, so one run keeps
synthesis and the area report in step with the library.

Logic functions cannot be measured, so they come from the table below; a new
combinational cell that is not in it is reported and left without a function
(harmless for gate_count.py, but gen_liberty.py will not offer it to ABC
until a function is added here).

  usage:
    scripts/sync_cell_info.py
    scripts/sync_cell_info.py --gds lef/TR-1um_STDCELL.gds \
        --extracted-dir ../TR-1um_Async_I2C/LEF
    scripts/sync_cell_info.py --set BUF_X2:transistors=6      # manual override
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GDS = os.path.join(ROOT, "lef", "TR-1um_STDCELL.gds")
LEF = os.path.join(ROOT, "lef", "TR-1um_STDCELL.lef")
INFO = os.path.join(ROOT, "lef", "cell_info.json")
EXTRACTED = os.path.join(ROOT, "..", "TR-1um_Async_I2C", "LEF")

# (output pin, liberty function, input pins) -- extend when a cell is added
FUNCS = {
    "INV_X1": ("Y", "A'", ["A"]),
    "BUF_X1": ("Y", "A", ["A"]),
    "BUF_X2": ("Y", "A", ["A"]),
    "BUF_X4": ("Y", "A", ["A"]),
    "INV_X2": ("Y", "A'", ["A"]),
    "INV_X4": ("Y", "A'", ["A"]),
    "BUFTH":  ("Y", "A", ["A"]),
    "NAND2": ("Y", "!(A*B)", ["A", "B"]),
    "NAND3": ("Y", "!(A*B*C)", ["A", "B", "C"]),
    "NAND4": ("Y", "!(A*B*C*D)", ["A", "B", "C", "D"]),
    "NOR2": ("Y", "!(A+B)", ["A", "B"]),
    "NOR3": ("Y", "!(A+B+C)", ["A", "B", "C"]),
    "NOR4": ("Y", "!(A+B+C+D)", ["A", "B", "C", "D"]),
    "AND2_X1": ("Y", "A*B", ["A", "B"]),
    "AND3_X1": ("Y", "A*B*C", ["A", "B", "C"]),
    "AND4_X1": ("Y", "A*B*C*D", ["A", "B", "C", "D"]),
    "OR2": ("Y", "A+B", ["A", "B"]),
    "OR3": ("Y", "A+B+C", ["A", "B", "C"]),
    "OR4": ("Y", "A+B+C+D", ["A", "B", "C", "D"]),
    "XOR2": ("Y", "A^B", ["A", "B"]),
    "XNOR2": ("Y", "!(A^B)", ["A", "B"]),
    "MUX2": ("Y", "(A*!S)+(B*S)", ["A", "B", "S"]),
    "NAND2B": ("Y", "!(!A*B)", ["A", "B"]),
    "NOR2B": ("Y", "!(!A+B)", ["A", "B"]),
    "AOI21": ("Y", "!((A*B)+C)", ["A", "B", "C"]),
    "OAI21": ("Y", "!((A+B)*C)", ["A", "B", "C"]),
}
SEQ = {"DFFRB", "DFFS", "DFF"}
MUXFF = {"MUXDFFRB"}
LATCH = {"RSLATCH"}


def measure_lef(lef_path):
    """MACRO SIZE from the LEF -- the authoritative placement footprint.

    NOTE: a cell's GDS bounding box is NOT its footprint.  Every TR-1um cell
    overhangs its prBoundary by exactly 12.6 um in x and 4.0 um in y (well /
    implant enclosure that abuts with the neighbouring cell), so measuring the
    GDS makes INV_X1 look like 1610 um2 when the LEF says 700 um2.  Always
    prefer the LEF; the GDS measurement below is only a fallback.
    """
    import re
    out, cur = {}, None
    for ln in open(lef_path):
        m = re.match(r"\s*MACRO\s+(\S+)", ln)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", ln)
        if m and cur:
            w, h = float(m.group(1)), float(m.group(2))
            out[cur] = dict(width_um=w, height_um=h, area_um2=round(w * h, 1))
    return out


def measure(gds_path, min_w=5.0, min_h=20.0):
    import gdstk
    out = {}
    for c in gdstk.read_gds(gds_path).cells:
        b = c.bounding_box()
        if not b:
            continue
        (x0, y0), (x1, y1) = b
        w, h = x1 - x0, y1 - y0
        if w < min_w or h < min_h:
            continue                       # vias and sub-shapes
        out[c.name] = dict(width_um=round(w, 2), height_um=round(h, 2),
                           area_um2=round(w * h))
    return out


def transistors(dirpath):
    out = {}
    if not dirpath or not os.path.isdir(dirpath):
        return out
    for f in glob.glob(os.path.join(dirpath, "*.extracted")):
        n = sum(1 for l in open(f)
                if l.strip().upper().startswith(("XM", "M"))
                and ("PMOS" in l.upper() or "NMOS" in l.upper()))
        out[os.path.splitext(os.path.basename(f))[0]] = n
    return out


def kind_of(name):
    if name in FUNCS:  return "comb"
    if name in SEQ:    return "ff"
    if name in MUXFF:  return "muxff"
    if name in LATCH:  return "latch"
    if name.startswith(("FILL", "TAP")): return "physical"
    return "other"


def main(gds_path=GDS, info_path=INFO, extracted_dir=EXTRACTED, overrides=None,
         lef_path=LEF):
    if lef_path and os.path.exists(lef_path):
        geo = measure_lef(lef_path)
        src = os.path.relpath(lef_path, ROOT) + " MACRO SIZE"
        if os.path.exists(gds_path):                 # cross-check
            gg = measure(gds_path)
            missing = sorted(set(gg) - set(geo))
            if missing:
                print(f"  !! in GDS but not in LEF (regenerate the LEF with "
                      f"gen_lef.py): {', '.join(missing)}")
                for n in missing:
                    geo[n] = dict(gg[n], area_um2=round(
                        (gg[n]['width_um'] - 12.6) * (gg[n]['height_um'] - 4.0), 1))
    else:
        geo = measure(gds_path)
        src = os.path.relpath(gds_path, ROOT) + " bounding boxes (NOT the footprint)"
    tr = transistors(extracted_dir)
    old = json.load(open(info_path)) if os.path.exists(info_path) else {}
    meta = {k: v for k, v in old.items() if k.startswith("_")}

    new, added, changed, no_func, no_tr = {}, [], [], [], []
    for name in sorted(geo):
        prev = old.get(name, {})
        e = dict(geo[name])
        e["transistors"] = tr.get(name, prev.get("transistors"))
        e["kind"] = kind_of(name)
        if name in FUNCS:
            e["out_pin"], e["function"], e["in_pins"] = FUNCS[name]
        new[name] = e
        if name not in old:
            added.append(name)
        elif any(prev.get(k) != e.get(k) for k in ("area_um2", "transistors")):
            changed.append(name)
        if e["kind"] == "other":
            no_func.append(name)
        if e["transistors"] is None:
            no_tr.append(name)

    removed = [n for n in old if not n.startswith("_") and n not in new]

    # LEF/GDS consistency: a MACRO's FOREIGN names the physical cell the
    # placement GDS builder instantiates.  A stale FOREIGN (e.g. a MACRO
    # added by copying another one) silently swaps the geometry -- the
    # netlist says BUF_X2, the layout gets BUF_X1, and only LVS notices.
    bad_foreign, no_geom = [], []
    if lef_path and os.path.exists(lef_path) and os.path.exists(gds_path):
        import re
        gds_cells = set(measure(gds_path))
        text = open(lef_path).read()
        for m in re.finditer(r"^MACRO (\S+)\n(.*?)\n\s*END\s+\1\s*$",
                             text, re.M | re.S):
            macro, body = m.group(1), m.group(2)
            fm = re.search(r"FOREIGN\s+(\S+)", body)
            foreign = fm.group(1) if fm else macro
            if macro not in gds_cells:
                no_geom.append((macro, foreign))
            elif foreign != macro:
                bad_foreign.append((macro, foreign))

    meta.setdefault("_source", {}).update({
        "areas": src,
        "transistors": "counted from <cell>.extracted",
        "gate_equivalent_ref": "NAND2 = 4 transistors = 1981 um2 = 1 equivalent gate"})
    for spec in overrides or []:
        cell, _, kv = spec.partition(":")
        k, _, v = kv.partition("=")
        if cell in new:
            new[cell][k] = int(v) if v.lstrip("-").isdigit() else v
            print(f"  override {cell}.{k} = {new[cell][k]}")

    meta.update(new)
    json.dump(meta, open(info_path, "w"), indent=1)

    print(f"{info_path}: {len(new)} cells")
    if added:    print(f"  ADDED   : {', '.join(added)}")
    if changed:  print(f"  CHANGED : {', '.join(changed)}")
    if removed:  print(f"  REMOVED : {', '.join(removed)}")
    if no_func:  print(f"  !! no logic function (add to FUNCS in this script "
                       f"to let ABC use them): {', '.join(no_func)}")
    if no_tr:    print(f"  !! no transistor count (pass --extracted-dir, or "
                       f"--set CELL:transistors=N): {', '.join(no_tr)}")
    if bad_foreign:
        print("  !! LEF FOREIGN points at a DIFFERENT cell than the MACRO, "
              "so the placed geometry will not match the netlist:")
        for macro, foreign in bad_foreign:
            print(f"       MACRO {macro} -> FOREIGN {foreign}")
    if no_geom:
        print("  !! MACRO with no cell of that name in the GDS (any use "
              "silently becomes its FOREIGN target):")
        for macro, foreign in no_geom:
            print(f"       MACRO {macro} -> FOREIGN {foreign}")
    if added or changed:
        print("\n  next: scripts/gen_liberty.py && scripts/build.sh && "
              "scripts/run_tests.sh")
    return new


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gds", default=GDS)
    ap.add_argument("--lef", default=LEF,
                    help="LEF whose MACRO SIZE is the authoritative footprint")
    ap.add_argument("--cell-info", default=INFO)
    ap.add_argument("--extracted-dir", default=EXTRACTED)
    ap.add_argument("--set", action="append", default=[], metavar="CELL:key=value")
    a = ap.parse_args()
    main(a.gds, a.cell_info, a.extracted_dir, a.set, a.lef)
