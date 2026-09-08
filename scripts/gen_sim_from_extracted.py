#!/usr/bin/env python3
"""gen_sim_from_extracted.py -- run the LAYOUT itself, not a netlist about it.

    layout/chip/tr_1um_3wire_SPI.extracted        (KLayout's LVS extraction)
 -> ngspice/tr_1um_3wire_SPI_extracted_sim.spice

`gen_chip_sim_ready.py` converts the *reference* netlist -- the one this
project builds from the Verilog and the cell schematics, and hands to LVS as
the "should be" side.  This converts the *other* side: what KLayout actually
found in the GDS.  LVS says the two match as graphs; only this one carries the
layout's own numbers.  Every transistor comes with its measured diffusion area
and perimeter:

    XM$1 vdd A Y vdd PMOS L=1u W=10.2u AS=28.56p AD=15.3p PS=26u PD=13.2u

The PDK's PMOS/NMOS subcircuits default AS/AD to `w*sdwidth` and PS/PD to
`2*(sdwidth+w)` when nobody passes them, which is what the reference netlist
gets.  Here the extractor measured the real shapes -- shared source/drain
diffusions in an abutted row are counted once, not twice -- so the junction
capacitances, and therefore the delays, are the drawn ones.

WHAT NEEDS FIXING, AND NOTHING ELSE
-----------------------------------
KLayout writes names ngspice cannot read.  Same discipline as
gen_chip_sim_ready.py: purely mechanical, counted, and reported.

  1. `\\$107`, `\\$I4`      -> `net_107`, `net_I4`.  Anonymous nets, written
                             with a backslash escape SPICE has no notion of,
                             around a `$` that starts a comment.
  2. `PAD|VDD`             -> `PAD_VDD`.  KLayout's spelling for one net that
                             carries two labels (the VDD ESD cell's pad IS the
                             rail); the `|` is not a name character.
  3. `X$1`, `XM$1`, `D$17` -> `X_1`, `XM_1`, `D_17`.  Instance names.
  4. `tx_data[0]`          -> `tx_data_0`.
  5. `A=`/`P=` on diodes   -> `AREA=`/`PJ=`.
  6. `NMOSE`               -> `MNE`.  There is no NMOSE in the PDK.

Renames are collision-checked against every name already in the file before
anything is written -- two distinct nets quietly becoming one would be a short
that no later check could tell from a real one.

The top subcircuit's ports are re-ordered to the project's canonical bond-pad
order (P1..P7, VSS, P9..P15, VDD) so this file is a drop-in replacement for
the reference netlist in the testbench -- KLayout emits them alphabetically,
which would silently permute the pads.

  usage:  scripts/gen_sim_from_extracted.py [-i SRC] [-o OUT] [--no-reorder]
"""
import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg
import gen_lvs_spice_top as _ref          # for TOP_PIN_ORDER

SRC = os.path.join(_cfg.CHIP, _cfg.CHIP_TOP_CELL + ".extracted")
OUT = os.path.join(_cfg.ROOT, "ngspice", _cfg.CHIP_TOP_CELL + "_extracted_sim.spice")
REF = os.path.join(_cfg.ROOT, "ngspice", _cfg.CHIP_TOP_CELL + "_sim_ready.spice")

MODEL_RENAME = {"NMOSE": "MNE"}
ANON_PREFIX = "net_"

DIODE_RE = re.compile(r"^(D\S+.*?)\bA=(\S+)\s+P=(\S+)(.*)$")


def rename(tok):
    """One identifier, KLayout's spelling -> a name ngspice will accept."""
    t = re.sub(r"\\\$", ANON_PREFIX, tok)      # \$107 -> net_107
    t = t.replace("|", "_")                    # PAD|VDD -> PAD_VDD
    t = re.sub(r"\[(\d+)\]", r"_\1", t)        # tx_data[0] -> tx_data_0
    t = t.replace("$", "_")                    # X$1 -> X_1
    return MODEL_RENAME.get(t, t)


def convert(text):
    stats = {k: 0 for k in ("anonymous nets", "PAD|rail pins", "instance names",
                            "bracketed names", "diode A=/P=", "NMOSE -> MNE")}
    seen, mapped = set(), {}
    out = []

    for line in text.splitlines():
        if line.startswith("*"):               # comment: left exactly as it is
            out.append(line)
            continue
        if not line.strip():
            out.append(line)
            continue

        head = line[0] if line[0] == "+" else ""
        body = line[1:] if head else line
        if not head and body.upper().startswith(("D",)):
            m = DIODE_RE.match(body)
            if m:
                body = f"{m.group(1)}AREA={m.group(2)} PJ={m.group(3)}{m.group(4)}"
                stats["diode A=/P="] += 1

        toks = body.split()
        new = []
        for i, t in enumerate(toks):
            if "=" in t or t.startswith("."):   # w=..., .SUBCKT, .ENDS
                new.append(t)
                continue
            seen.add(t)
            r = rename(t)
            if r != t:
                if t.startswith("\\$"):
                    stats["anonymous nets"] += 1
                elif "|" in t:
                    stats["PAD|rail pins"] += 1
                elif "$" in t:
                    stats["instance names"] += 1
                elif "[" in t:
                    stats["bracketed names"] += 1
                elif t in MODEL_RENAME:
                    stats["NMOSE -> MNE"] += 1
                mapped.setdefault(t, r)
            new.append(r)
        out.append((head + " " if head else "") + " ".join(new))

    # A rename that lands on a name the file already used, or on another
    # rename's result, would merge two nets into one -- a short indis-
    # tinguishable from a real one by the time anybody looks.
    clashes = []
    for src, dst in sorted(mapped.items()):
        if dst in seen and dst != src:
            clashes.append(f"{src!r} -> {dst!r}, which the file already uses")
    inverse = {}
    for src, dst in sorted(mapped.items()):
        inverse.setdefault(dst, []).append(src)
    for dst, srcs in sorted(inverse.items()):
        if len(srcs) > 1:
            clashes.append(f"{srcs} all become {dst!r}")

    return "\n".join(out) + "\n", stats, mapped, clashes


def reorder_top(text, top, order):
    """KLayout writes the top ports alphabetically; put them back in pad order."""
    pat = re.compile(r"^(\.SUBCKT\s+" + re.escape(top) + r")((?:[^\n]*\n\+[^\n]*)*[^\n]*)$",
                     re.M | re.I)
    m = pat.search(text)
    if not m:
        raise SystemExit(f".SUBCKT {top} not found")
    have = m.group(2).replace("\n+", " ").split()
    if set(have) != set(order):
        raise SystemExit(f"top ports {sorted(have)} != the canonical "
                         f"{sorted(order)}")
    note = ("* ports re-ordered to bond-pad order by "
            "scripts/gen_sim_from_extracted.py;\n"
            "* the \"* pin\" list above is KLayout's own alphabetical one\n")
    return (text[:m.start()] + note + f".SUBCKT {top} " + " ".join(order)
            + text[m.end():], have)


DEV_MODELS = {"PMOS": "P", "MPE": "P", "NMOS": "N", "MNE": "N", "NMOSE": "N"}


def logical_lines(text):
    """SPICE continuation lines folded back into one line each."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("*"):
            continue
        if s.startswith("+") and out:
            out[-1] += " " + s[1:].strip()
        else:
            out.append(s)
    return out


def census(text):
    """Per cell: the child instances by name, and total P/N channel width.

    Width, not device count, because the extractor MERGES parallel devices --
    the 48 FILL3 decoupling transistors come back as one 1017.6 um PMOS -- and
    a schematic may spell the same thing as `m=2`.  Total width survives both
    foldings; a count survives neither.  (check_cell_spice.py compares the
    cells against their GDS on the same principle.)
    """
    out, cur = {}, None
    for s in logical_lines(text):
        low = s.lower()
        if low.startswith(".subckt"):
            cur = s.split()[1]
            out.setdefault(cur, {"cells": {}, "P": 0.0, "N": 0.0})
        elif low.startswith(".ends"):
            cur = None
        elif cur and s[0] in "XxDdMm":
            # X for a subcircuit call or (in this PDK) a transistor, M for a
            # bare MOSFET line -- the reference netlist still spells them that
            # way -- D for a diode, which carries no channel width.
            t = s.split()
            kind = DEV_MODELS.get(t[5]) if len(t) >= 6 else None
            if kind:
                w = m = None
                for tok in t[6:]:
                    k, _, v = tok.partition("=")
                    if k.lower() == "w":
                        w = float(v.rstrip("uU")) if v.lower().endswith("u") \
                            else float(v) * 1e6
                    elif k.lower() == "m":
                        m = float(v)
                out[cur][kind] += (w or 0.0) * (m or 1.0)
            elif s[0] in "Xx":
                out[cur]["cells"][t[-1]] = out[cur]["cells"].get(t[-1], 0) + 1
    return out


def compare(a, b, label_a, label_b):
    """Report every cell that the two netlists describe differently."""
    problems = []
    for name in sorted(set(a) | set(b)):
        if name not in a:
            problems.append(f"{name}: only in {label_b}")
            continue
        if name not in b:
            problems.append(f"{name}: only in {label_a}")
            continue
        x, y = a[name], b[name]
        if x["cells"] != y["cells"]:
            for c in sorted(set(x["cells"]) | set(y["cells"])):
                if x["cells"].get(c, 0) != y["cells"].get(c, 0):
                    problems.append(f"{name}: {c} x{x['cells'].get(c, 0)} in "
                                    f"{label_a}, x{y['cells'].get(c, 0)} in {label_b}")
        for k in ("P", "N"):
            if abs(x[k] - y[k]) > 0.05:
                problems.append(f"{name}: total {k}MOS width {x[k]:.1f} um in "
                                f"{label_a}, {y[k]:.1f} um in {label_b}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--src", default=SRC)
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--top", default=_cfg.CHIP_TOP_CELL)
    ap.add_argument("--no-reorder", action="store_true")
    ap.add_argument("--ref", default=REF,
                    help="reference netlist to compare the cell census against")
    args = ap.parse_args()

    text, stats, mapped, clashes = convert(open(args.src).read())
    for k in ("anonymous nets", "PAD|rail pins", "instance names",
              "bracketed names", "diode A=/P=", "NMOSE -> MNE"):
        print(f"  {k:22s} {stats[k]}")
    print(f"  {'distinct names renamed':22s} {len(mapped)}")

    if clashes:
        for c in clashes:
            print("  PROBLEM: rename collision: " + c)
        raise SystemExit(f"{len(clashes)} collision(s) -- nothing written")

    for bad, what in ((r"\\\$", "backslash-escaped name"), (r"\|", "'|' in a name"),
                      (r"\[\d+\]", "bracketed name"), (r"\bNMOSE\b", "NMOSE")):
        left = [l for l in text.splitlines()
                if not l.startswith("*") and re.search(bad, l)]
        if left:
            raise SystemExit(f"{len(left)} line(s) still carry a {what}: {left[0]!r}")

    if not args.no_reorder:
        text, was = reorder_top(text, args.top, _ref.TOP_PIN_ORDER)
        print(f"\ntop ports re-ordered to bond-pad order")
        print(f"  KLayout   {' '.join(was)}")
        print(f"  canonical {' '.join(_ref.TOP_PIN_ORDER)}")

    header = [
        f"** {os.path.basename(args.out)} -- the EXTRACTED layout, made runnable.",
        "** GENERATED by scripts/gen_sim_from_extracted.py; do not edit by hand.",
        f"**   from : {os.path.relpath(args.src, _cfg.ROOT)} (KLayout LVS extraction)",
        "**",
        "** Every device carries the extractor's own AS/AD/PS/PD, so the junction",
        "** capacitances are the drawn ones rather than the PDK's w*sdwidth",
        "** defaults.  Only names were changed -- see the generator for the six",
        "** substitutions and their counts.  The top subcircuit's ports are in",
        "** bond-pad order, so this drops straight into the testbench in place",
        "** of the reference netlist.",
        "",
    ]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write("\n".join(header) + text)

    # ---- does it parse, and is it the same circuit the reference describes? --
    try:
        import klayout.db as db
        nl = db.Netlist()
        nl.read(args.out, db.NetlistSpiceReader())
        names = {c.name.upper() for c in nl.each_circuit()}
        print(f"\nKLayout's SPICE reader parses it: {len(names)} circuit(s)")
        if args.top.upper() not in names:
            raise SystemExit(f"{args.top} missing from the parsed netlist")
    except ImportError:
        print("\n(klayout not available -- skipped the parse-back check)")

    if args.ref and os.path.exists(args.ref):
        a, b = census(text), census(open(args.ref).read())
        problems = compare(a, b, "the extraction", "the reference")
        tw = lambda d, k: sum(v[k] for v in d.values())
        print(f"\nvs {os.path.relpath(args.ref, _cfg.ROOT)}: {len(a)} vs {len(b)} cell(s); "
              f"total PMOS {tw(a,'P'):.1f} vs {tw(b,'P'):.1f} um, "
              f"NMOS {tw(a,'N'):.1f} vs {tw(b,'N'):.1f} um")
        for p in problems:
            print("  DIFFERS: " + p)
        if not problems:
            print("  every cell holds the same children and the same total "
                  "P/N channel width")

    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
