#!/usr/bin/env python3
"""sweep_sclk.py -- how fast will this chip actually run?

Drives the same twelve functional checks (scripts/gen_chip_tb.py) up the
frequency axis until they break, and reports the last frequency that passed
together with the measured clock-to-out delays at each step.

The point is not a single number but WHICH check fails first.  A SPI slave can
run out of speed in three different places, and they have different fixes:

  read_byte      the chip drives SDIO from the SCLK FALLING edge and the master
                 samples it on the next RISING edge, half a period later.  This
                 is the output path -- pad input buffer, core flop, output
                 driver, pad -- and it is the one the tco_sdio_* measures time.
  rx_wr1/rx_wr2  the received byte is wrong: the input path (SDIO through the
                 pad into the shift register) missed its setup or hold against
                 SCLK, or the shift register itself ran out of cycle.
  test_*         byte_end, i.e. the bit counter, lost a count.

Each frequency is an independent ngspice run in its own directory, so a failed
point leaves its netlist and log behind for inspection instead of being
overwritten by the next one.

  usage:  scripts/sweep_sclk.py                       # the default ladder
          scripts/sweep_sclk.py -f 8,10,12,14 --keep
          scripts/sweep_sclk.py --netlist tr_1um_3wire_SPI_sim_ready.spice
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg
import check_chip_sim as _chk

NGDIR = os.path.join(_cfg.ROOT, "ngspice")
GEN = os.path.join(_HERE, "gen_chip_tb.py")
DEFAULT_NETLIST = _cfg.CHIP_TOP_CELL + "_extracted_sim.spice"
DEFAULT_FREQS = "1,2,5,10,15,20,25,30"

# A TRIG/TARG measure prints its own targ=/trig= after the value, so the
# plain "name = value" line the checker parses does not match it -- which is
# exactly why check_chip_sim.py ignores these and never grades them.
TCO_LINE_RE = re.compile(r"^\s*(tco_\S+)\s*=\s*(\S+)\s+targ=", re.M)


def run_one(freq_mhz, netlist, models, workdir, ngspice, load_pf=0.0,
            master_ohm=None):
    """Generate, run and grade one frequency.  Returns (ok, failures, tco)."""
    os.makedirs(workdir, exist_ok=True)
    tb = os.path.join(workdir, "tb.spice")
    exp = os.path.join(workdir, "expected.json")
    cmd = [sys.executable, GEN, "--sclk", str(freq_mhz), "--netlist", netlist,
           "-o", tb, "-j", exp]
    if models:
        cmd += ["--models", models]
    if load_pf:
        cmd += ["--load-pf", str(load_pf)]
    if master_ohm:
        cmd += ["--master-ohm", str(master_ohm)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)

    # the testbench includes the netlist by bare name, so run beside it
    shutil.copy(os.path.join(NGDIR, netlist), workdir)
    log = os.path.join(workdir, "spice.log")
    with open(log, "w") as f:
        subprocess.run([ngspice, "-b", os.path.basename(tb)], cwd=workdir,
                       stdout=f, stderr=subprocess.STDOUT)

    text = open(log).read()
    # A run that never finished has no measurements at all.  That is a
    # SIMULATOR failure, not a chip failure, and calling it FAIL would put a
    # ceiling on the part that the part does not have -- one sweep point
    # aborted with "timestep too small" purely on the reset edge.
    if "aborted" in text or "Measurements for Transient Analysis" not in text:
        why = next((l.strip() for l in text.splitlines()
                    if "Timestep too small" in l or "aborted" in l), "no measurements")
        return "ERROR", [("ngspice", why)], {}, log

    vals = _chk.parse_measures(text)
    checks = json.load(open(exp))
    failures = []
    for c in checks:
        if c["kind"] == "level":
            v = vals.get(c["name"])
            ok = v is not None and (v > _chk.THRESH) == (c["expect"] == "high")
            detail = "no value" if v is None else f"{v:.3f} V"
        else:
            got, missing = 0, False
            for i in range(8):
                v = vals.get(f"{c['name']}_bit{i}")
                if v is None:
                    missing = True
                else:
                    got |= (1 if v > _chk.THRESH else 0) << i
            ok = not missing and got == c["expect"]
            detail = "no value" if missing else f"0x{got:02X} != 0x{c['expect']:02X}"
        if not ok:
            failures.append((c["name"], detail))
    tco = {}
    for name, raw in TCO_LINE_RE.findall(text):
        try:
            tco[name] = float(raw)
        except ValueError:
            pass
    return ("PASS" if not failures else "FAIL"), failures, tco, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-f", "--freqs", default=DEFAULT_FREQS,
                    help=f"comma-separated SCLK in MHz (default {DEFAULT_FREQS})")
    ap.add_argument("--netlist", default=DEFAULT_NETLIST)
    ap.add_argument("--models", default=None)
    ap.add_argument("--ngspice", default="ngspice")
    ap.add_argument("--master-ohm", type=float, default=None,
                    help="bench master series resistance in ohms, passed "
                         "through to the testbench generator")
    ap.add_argument("--load-pf", type=float, default=0.0,
                    help="external pad load in pF, passed through to the "
                         "testbench generator")
    ap.add_argument("-d", "--dir", default=None,
                    help="where to keep each run (default: a temp dir, removed "
                         "unless a point fails)")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    freqs = [float(x) for x in args.freqs.split(",") if x.strip()]
    root = args.dir or tempfile.mkdtemp(prefix="sclk_sweep_")
    print(f"netlist  {args.netlist}")
    print(f"load     {args.load_pf:g} pF on every signal pad")
    print(f"runs     {root}\n")

    last_pass, first_fail, rows = None, None, []
    for f in freqs:
        wd = os.path.join(root, f"f{f:g}MHz")
        tag, failures, tco, log = run_one(f, args.netlist, args.models, wd,
                                          args.ngspice, args.load_pf,
                                          args.master_ohm)
        rows.append((f, tag, failures, tco))
        parts = "  ".join(f"{k}={v*1e9:.1f}ns" for k, v in sorted(tco.items()))
        print(f"  {f:6.2f} MHz  {tag:5s}  {parts}")
        for name, detail in failures:
            print(f"              {name}: {detail}")
        if tag == "PASS":
            last_pass = f
        else:
            if tag == "FAIL":
                first_fail = first_fail or f
            print(f"              log: {log}")

    print()
    if last_pass is None:
        print("nothing passed -- the ladder starts too high")
    elif first_fail is None:
        print(f"every point passed; the ceiling is above {max(freqs):g} MHz")
    else:
        print(f"highest passing SCLK {last_pass:g} MHz, first failure at "
              f"{first_fail:g} MHz")

    errs = [f for f, tag, _, _ in rows if tag == "ERROR"]
    if errs:
        print(f"{len(errs)} point(s) did not simulate at all (not a speed "
              f"limit -- see the log): {errs}")

    if not args.keep and not args.dir and first_fail is None:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
