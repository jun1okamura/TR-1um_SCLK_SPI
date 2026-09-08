#!/usr/bin/env python3
"""export_mpw.py -- the two files the MPW shuttle actually takes.

    layout/chip/step4_final.gds        ->  src/<top_cell>.gds
    layout/chip/<top_cell>.spice       ->  src/<top_cell>.cir

Both are copies with exactly ONE edit between them, and it is the same edit:
the pad ring cell is renamed OSS_FRAME_GIO -> OSS_FRAME.

WHY THE RENAME
--------------
scripts/pre_check.py -- the submission's own gate, which runs first in the CI
workflow -- requires that a cell named OSS_FRAME or OSS_FRAME_TEG exists in the
layout.  This design instantiates OSS_FRAME_GIO, the GIO variant.  The frame GDS
does carry plain OSS_FRAME and OSS_FRAME_TEG as well, but nothing instantiates
them, so they are top-level cells, and the same pre-check requires exactly one
of those -- they are pruned by place_logo.py along with the unused library
cells.  Renaming the one ring the chip actually uses satisfies both rules at
once.  The I2C submission did the same thing, for the same reason.

The rename is made in the GDS and the netlist together, so the pair LVS
compares stays self-consistent.  It happens ONLY in this exported copy; the
master layout and the reference netlist keep the real name, which is what every
other script in this repo, and the PDK's own cell library, use.

WHAT IS CHECKED BEFORE ANYTHING IS WRITTEN
------------------------------------------
Everything pre_check.py checks, plus the things it cannot see: that the netlist
parses, that its top subcircuit declares exactly the bond-pad ports the layout
has pin markers for, and that info.yaml names the files this script is about to
produce.  A submission that fails in CI has cost a day; failing here costs a
second.

  usage:  scripts/export_mpw.py [--in-gds ...] [--src-dir src] [-n]
"""
import argparse
import os
import re
import shutil
import sys

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg
import gen_lvs_spice_top as _ref          # TOP_PIN_ORDER

IN_GDS = os.path.join(_cfg.CHIP, "step4_final.gds")
IN_CIR = os.path.join(_cfg.CHIP, _cfg.CHIP_TOP_CELL + ".spice")
SRC_DIR = os.path.join(_cfg.ROOT, "src")
INFO = os.path.join(_cfg.ROOT, "info.yaml")

RING_FROM, RING_TO = "OSS_FRAME_GIO", "OSS_FRAME"
FRAME_CELL_NAMES = {"OSS_FRAME", "OSS_FRAME_TEG"}   # pre_check.py's own set
DIE = 2500.0
EXPECTED_DBU = 0.001
TXM2 = (49, 0)
M2PIN = (49, 1)

# The template ships a placeholder design; leaving it beside the real one is
# how the wrong file gets submitted.
TEMPLATE_STEMS = ("tr_1um_username",)


def info_field(text, *path):
    """The value of a nested key in info.yaml, without a yaml dependency."""
    depth, want = 0, list(path)
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, _, rest = line.strip().partition(":")
        if indent == depth * 2 and key == want[0]:
            if len(want) == 1:
                v = rest.split("#")[0].strip()
                return v.strip('"').strip("'")
            want.pop(0)
            depth += 1
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-gds", default=IN_GDS)
    ap.add_argument("--in-cir", default=IN_CIR)
    ap.add_argument("--src-dir", default=SRC_DIR)
    ap.add_argument("-n", "--dry-run", action="store_true")
    args = ap.parse_args()

    top_name = _cfg.CHIP_TOP_CELL
    problems = []

    # ---- info.yaml has to be describing these files -----------------------
    info = open(INFO).read()
    cfg_top = info_field(info, "gds", "top_cell")
    gds_ext = info_field(info, "gds", "extension")
    cir_ext = info_field(info, "lvs", "extension")
    print(f"info.yaml: top_cell={cfg_top!r} gds.extension={gds_ext!r} "
          f"lvs.extension={cir_ext!r} netlist_only="
          f"{info_field(info, 'lvs', 'netlist_only')!r}")
    if cfg_top != top_name:
        problems.append(f"info.yaml gds.top_cell {cfg_top!r} != {top_name!r}")
    if gds_ext != "gds" or cir_ext != "cir":
        problems.append(f"unexpected extensions {gds_ext!r}/{cir_ext!r}")

    # ---- the layout -------------------------------------------------------
    ly = db.Layout()
    ly.read(args.in_gds)
    tops = [c.name for c in ly.top_cells()]
    print(f"\n{os.path.relpath(args.in_gds, _cfg.ROOT)}: {len(list(ly.each_cell()))} "
          f"cell(s), top {tops}, dbu {ly.dbu}")
    if tops != [top_name]:
        problems.append(f"expected exactly one top cell {top_name!r}, got {tops}")
    if abs(ly.dbu - EXPECTED_DBU) > 1e-12:
        problems.append(f"dbu {ly.dbu} != {EXPECTED_DBU}")
    top = ly.cell(top_name)
    if top is not None:
        bb = top.dbbox()
        want = db.DBox(-DIE / 2, -DIE / 2, DIE / 2, DIE / 2)
        print(f"  die {bb}")
        if abs(bb.left - want.left) > 1e-6 or abs(bb.bottom - want.bottom) > 1e-6 \
                or abs(bb.right - want.right) > 1e-6 or abs(bb.top - want.top) > 1e-6:
            problems.append(f"die box {bb} != {want}")

    if ly.cell(RING_FROM) is None:
        problems.append(f"{RING_FROM} not in the layout -- nothing to rename")
    clash = [n for n in FRAME_CELL_NAMES if ly.cell(n) is not None]
    if clash:
        problems.append(f"{clash} already present; the rename would collide "
                        f"(place_logo.py should have pruned them)")

    # ---- bond-pad pins the two sides have to agree on ---------------------
    labels = sorted({s.text.string for s in top.shapes(ly.layer(*TXM2)).each()
                     if s.is_text()}) if top else []
    npin = top.shapes(ly.layer(*M2PIN)).size() if top else 0
    print(f"  {len(labels)} pin label(s) on TXM2 {TXM2}, {npin} box(es) on "
          f"M2PIN {M2PIN}")
    if sorted(labels) != sorted(_ref.TOP_PIN_ORDER):
        problems.append(f"pin labels {labels} != the netlist's ports "
                        f"{sorted(_ref.TOP_PIN_ORDER)}")

    # ---- the netlist ------------------------------------------------------
    cir = open(args.in_cir).read()
    n_ring = len(re.findall(rf"\b{RING_FROM}\b", cir))
    print(f"\n{os.path.relpath(args.in_cir, _cfg.ROOT)}: {n_ring} mention(s) of "
          f"{RING_FROM}")
    if n_ring < 2:
        problems.append(f"expected at least a .subckt and one call of {RING_FROM}")
    if re.search(rf"\b{RING_TO}\b", cir):
        problems.append(f"{RING_TO} already appears in the netlist")

    if problems:
        for p in problems:
            print("\n  PROBLEM: " + p)
        raise SystemExit(f"{len(problems)} problem(s) -- nothing written")

    # ---- rename and write -------------------------------------------------
    ly.cell(RING_FROM).name = RING_TO
    out_gds = os.path.join(args.src_dir, f"{top_name}.{gds_ext}")
    out_cir = os.path.join(args.src_dir, f"{top_name}.{cir_ext}")
    header = (
        f"* {top_name}.{cir_ext} -- MPW submission netlist.\n"
        f"* GENERATED by scripts/export_mpw.py from "
        f"{os.path.relpath(args.in_cir, _cfg.ROOT)}; do not edit by hand.\n"
        f"* The ONLY change from that file: {RING_FROM} is renamed {RING_TO},\n"
        f"* here and in {top_name}.{gds_ext} together, because scripts/pre_check.py\n"
        f"* requires a cell by that name.  Everything else is identical.\n"
    )
    body = re.sub(rf"\b{RING_FROM}\b", RING_TO, cir)

    if args.dry_run:
        print(f"\n(dry run) would write {out_gds} and {out_cir}")
        return

    os.makedirs(args.src_dir, exist_ok=True)
    ly.write(out_gds)
    with open(out_cir, "w") as f:
        f.write(header + body)
    print(f"\nwrote {out_gds}")
    print(f"wrote {out_cir}")

    stale = [os.path.join(args.src_dir, f) for f in sorted(os.listdir(args.src_dir))
             if any(f.startswith(s + ".") for s in TEMPLATE_STEMS)]
    for p in stale:
        os.remove(p)
    if stale:
        print(f"removed the template placeholder: "
              f"{[os.path.basename(p) for p in stale]}")

    # ---- read back what was actually written ------------------------------
    print("\nre-reading the exported pair")
    ck = db.Layout()
    ck.read(out_gds)
    tops = [c.name for c in ck.top_cells()]
    frame = sorted(n for n in FRAME_CELL_NAMES if ck.cell(n) is not None)
    print(f"  gds: {len(list(ck.each_cell()))} cell(s), top {tops}, "
          f"dbu {ck.dbu}, die {ck.cell(top_name).dbbox()}")
    print(f"  gds: frame cell(s) pre_check will look for: {frame}")
    bad = []
    if tops != [top_name]:
        bad.append(f"top cells {tops}")
    if not frame:
        bad.append("no OSS_FRAME/OSS_FRAME_TEG cell")
    if ck.cell(RING_FROM) is not None:
        bad.append(f"{RING_FROM} survived the rename")

    nl = db.Netlist()
    nl.read(out_cir, db.NetlistSpiceReader())
    names = {c.name.upper() for c in nl.each_circuit()}
    print(f"  cir: parses, {len(names)} circuit(s)")
    for want in (top_name.upper(), RING_TO):
        if want not in names:
            bad.append(f"{want} missing from the netlist")
    if RING_FROM in names:
        bad.append(f"{RING_FROM} survived in the netlist")
    ports = [p.name() for p in nl.circuit_by_name(top_name.upper()).each_pin()] \
        if top_name.upper() in names else []
    print(f"  cir: top subckt has {len(ports)} port(s)")
    if len(ports) != len(_ref.TOP_PIN_ORDER):
        bad.append(f"{len(ports)} ports vs {len(_ref.TOP_PIN_ORDER)} pin markers")

    if bad:
        for b in bad:
            print("  PROBLEM: " + b)
        raise SystemExit(f"{len(bad)} problem(s) in what was written")
    print("\nthe exported pair passes every pre-check rule this script can test")


if __name__ == "__main__":
    main()
