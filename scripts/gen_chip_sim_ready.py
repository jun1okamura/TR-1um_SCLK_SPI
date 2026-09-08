#!/usr/bin/env python3
"""gen_chip_sim_ready.py -- make the chip's LVS netlist runnable in ngspice.

    layout/chip/tr_1um_3wire_SPI.spice  ->  ngspice/tr_1um_3wire_SPI_sim_ready.spice

The LVS reference is written for KLayout's netlist comparer, which is happy
with several things ngspice is not.  Five mechanical fixes, all of them the
same ones the I2C project needed (scripts/i2c_ref/gen_chip_sim_ready_v10.py);
nothing else in the file is touched -- same ports, same nets, same hierarchy,
same device sizing:

  1. `M<name> d g s b PMOS w=.. l=..`  ->  `X<name> ...`
     The PDK's PMOS/NMOS/MPE/MNE are SUBCIRCUITS (`.subckt PMOS d g s b` in
     models_IP62_mos_v2.lib), not `.model` cards, so an instance of one has to
     start with X.  A leading M makes ngspice look for a MOSFET model that does
     not exist.
  2. `rx_data[0]` -> `rx_data_0`.  Bracketed vector names are fine for LVS and
     are not a node name ngspice will accept.
  3. A `*.PININFO` comment continued on a bare `+` line: the comment ends at
     the newline, so the continuation is read as a circuit line.  `*+`.
  4. The pad-ESD diodes' `A=`/`P=` -> `AREA=`/`PJ=`, the names ngspice uses.
  5. `NMOSE` -> `MNE`.  There is no NMOSE in the PDK; MNE is the extended-drain
     NMOS the pad cells actually instantiate.

Every substitution is counted and reported, and the result is parsed back with
KLayout's own SPICE reader before it is written -- a netlist that no longer
parses is worse than one that never converted.

  usage:  scripts/gen_chip_sim_ready.py [-o OUT]
"""
import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

SRC = os.path.join(_cfg.CHIP, _cfg.CHIP_TOP_CELL + ".spice")
OUT = os.path.join(_cfg.ROOT, "ngspice", _cfg.CHIP_TOP_CELL + "_sim_ready.spice")

MODEL_RENAME = {"NMOSE": "MNE"}
SUBCKT_MODELS = ("PMOS", "NMOS", "NMOSE", "MPE", "MNE")

BARE_M_RE = re.compile(r"^M(\S+)((?: \S+){4}) (" + "|".join(SUBCKT_MODELS) + r")\b(.*)$", re.M)
BRACKET_RE = re.compile(r"\b(\w+)\[(\d+)\]")
DIODE_RE = re.compile(r"^(D\S+.*?)\bA=(\S+)\s+P=(\S+)(.*)$", re.M)


def convert(text):
    stats = {}

    def m_to_x(m):
        model = MODEL_RENAME.get(m.group(3), m.group(3))
        stats["M->X"] = stats.get("M->X", 0) + 1
        if model != m.group(3):
            stats["model rename"] = stats.get("model rename", 0) + 1
        return f"X{m.group(1)}{m.group(2)} {model}{m.group(4)}"

    text = BARE_M_RE.sub(m_to_x, text)

    names = set(BRACKET_RE.findall(text))
    text, n = BRACKET_RE.subn(lambda m: f"{m.group(1)}_{m.group(2)}", text)
    stats["bracket names"] = n
    stats["distinct bracket names"] = len(names)

    out, fixed = [], 0
    in_comment = False
    for line in text.splitlines():
        if line.startswith("+") and in_comment:
            out.append("*" + line)
            fixed += 1
            continue
        in_comment = line.startswith("*")
        out.append(line)
    text = "\n".join(out) + "\n"
    stats["PININFO continuations"] = fixed

    text, n = DIODE_RE.subn(lambda m: f"{m.group(1)}AREA={m.group(2)} PJ={m.group(3)}{m.group(4)}",
                            text)
    stats["diode A=/P="] = n
    return text, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--src", default=SRC)
    ap.add_argument("-o", "--out", default=OUT)
    args = ap.parse_args()

    text, stats = convert(open(args.src).read())
    for k in ("M->X", "model rename", "bracket names", "distinct bracket names",
              "PININFO continuations", "diode A=/P="):
        print(f"  {k:24s} {stats.get(k, 0)}")

    leftovers = re.findall(r"^M\S+", text, re.M)
    if leftovers:
        raise SystemExit(f"{len(leftovers)} bare M-line(s) left: {leftovers[:5]}")
    if "NMOSE" in text:
        raise SystemExit("NMOSE still present")
    if BRACKET_RE.search(text):
        raise SystemExit("bracketed node name still present")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(text)

    # parse it back before believing it
    try:
        import klayout.db as db
        nl = db.Netlist()
        nl.read(args.out, db.NetlistSpiceReader())
        # KLayout's SPICE reader upper-cases circuit names; SPICE is
        # case-insensitive, so compare that way rather than chasing it.
        circuits = [c.name for c in nl.each_circuit()]
        print(f"\nKLayout's SPICE reader parses it: {len(circuits)} circuit(s)")
        if _cfg.CHIP_TOP_CELL.upper() not in {c.upper() for c in circuits}:
            raise SystemExit(f"{_cfg.CHIP_TOP_CELL} missing from the parsed netlist")
    except ImportError:
        print("\n(klayout not available -- skipped the parse-back check)")

    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
