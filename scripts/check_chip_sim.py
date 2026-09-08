#!/usr/bin/env python3
"""check_chip_sim.py -- read ngspice's log, say PASS or FAIL.

    ngspice/tb_chip_spi_expected.json  +  the ngspice batch log
  -> one line per check, then a verdict

ngspice batch mode prints every `.measure` result once, right after `run`, as a
plain `name = value` line.  That block is the only thing parsed here -- the
testbench deliberately does not `print` anything itself (an explicit print
after `run` has been seen to trigger a silent second .tran pass).

A measure ngspice could not evaluate prints `name = failed`, which is a FAIL
for that check rather than a crash: a run that half-worked should still tell
you which half.

  usage:  ngspice -b ngspice/tb_chip_spi.spice > ngspice/spice_chip.log 2>&1
          scripts/check_chip_sim.py ngspice/spice_chip.log
"""
import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

EXPECTED = os.path.join(_cfg.ROOT, "ngspice", "tb_chip_spi_expected.json")
VDD = 5.0
THRESH = VDD / 2.0

MEASURE_RE = re.compile(r"^\s*(\S+)\s*=\s*(\S+)\s*$")


def parse_measures(text):
    vals = {}
    for line in text.splitlines():
        m = MEASURE_RE.match(line)
        if not m:
            continue
        try:
            vals[m.group(1)] = float(m.group(2))
        except ValueError:
            vals[m.group(1)] = None        # "failed"
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("-e", "--expected", default=EXPECTED)
    args = ap.parse_args()

    vals = parse_measures(open(args.log).read())
    checks = json.load(open(args.expected))

    npass = nfail = 0
    for c in checks:
        if c["kind"] == "level":
            v = vals.get(c["name"])
            if v is None:
                ok, detail = False, "measure not found / failed"
            else:
                ok = (v > THRESH) == (c["expect"] == "high")
                detail = f"{v:.3f} V on {c['node']}"
        else:
            got, missing = 0, False
            for i in range(8):
                v = vals.get(f"{c['name']}_bit{i}")
                if v is None:
                    missing = True
                    continue
                got |= (1 if v > THRESH else 0) << i
            if missing:
                ok, detail = False, "one or more bit measures not found / failed"
            else:
                ok = got == c["expect"]
                detail = f"got 0x{got:02X}, expected 0x{c['expect']:02X}"
        npass, nfail = (npass + 1, nfail) if ok else (npass, nfail + 1)
        print(f"[t={c['t']*1e9:7.0f} ns] {'OK  ' if ok else 'FAIL'}: "
              f"{c['desc']:<55s} ({detail})")

    print("\n---- RESULT ----")
    if nfail:
        print(f"{npass} passed, {nfail} FAILED")
    else:
        print(f"All {npass} checks PASSED")
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
