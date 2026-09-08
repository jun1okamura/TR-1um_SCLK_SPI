#!/usr/bin/env python3
"""gen_chip_tb.py -- the chip-level ngspice testbench, generated.

    layout/chip/gio_connections.json  (which pad is which)
  + ngspice/tr_1um_3wire_SPI_sim_ready.spice
  -> ngspice/tb_chip_spi.spice
     ngspice/tb_chip_spi_expected.json   (what the companion checker expects)

This is the last verification step before export: the same functional checks
the Verilog testbenches and the gate-level netlist already pass, re-run on the
WHOLE CHIP at transistor level -- core plus the sixteen OSS_ESD_5V_DIO pads
plus the ESD rails -- with the PDK's own device models.  Nothing above the
transistors is idealised, so this is the first run that can catch a pad
polarity error, a driver fight, or a level that never actually reaches a rail.

WHAT IT DRIVES
--------------
Three frames back to back, at SCLK = 1 MHz, Mode 0, MSB first:

    WRITE 0xA5   DIS=0.  Master drives SDIO; the chip latches the byte on the
                 8th rising edge and drives it back out on the DATA pads.
    READ  0x3D   DIS=1.  DATA pads become inputs carrying tx_data; the chip
                 drives SDIO from CS-low until CS-high.
    WRITE 0x5A   DIS=0 again -- proves the second frame starts from bit 7 and
                 that the READ frame did not disturb rx_data.

0x3D is deliberate: its LSB is 1, so the last bit the chip drives on SDIO is
high, and the Hi-Z check right after CS rises (a 20k pulldown takes the pad to
0 the moment the driver lets go) actually distinguishes "released" from "still
driving".  With an LSB of 0 that check would pass either way.

NOBODY DRIVES A PAD FROM BOTH SIDES
-----------------------------------
The two bidirectional groups are driven through voltage-controlled switches so
only one side is ever on, exactly as the I2C chip testbench did it
(TR-1um_Async_I2C/ngspice/TB/tb_chip_i2c_batch14_v10.spice):

    DATA[7:0]   switch gated by TXGATE, which mirrors DIS.  DIS=1 -> the pads
                are inputs and the bench drives tx_data through 10k; DIS=0 ->
                the switch is open (ROFF=1T) and the chip owns the pads.
    SDIO        switch gated by MGATE, high only during the two WRITE frames.

A 1M resistor to VSS on each DATA pad and 20k on SDIO give every pad a DC path
for the operating point and define the Hi-Z level; both are far weaker than
either driver, so they cost about 1% of a logic level and change no decision.

Tmax = 1 ns
-----------
The 4th argument of .tran.  The I2C project spent several sessions on a READ
frame that "failed" in SPICE and passed in IRSIM, Verilog and gate-level sim,
and root-caused it to ngspice's adaptive step control at Tmax=50ns rounding a
sub-10ns setup margin to the wrong side; 1 ns alone took that configuration
from 10/14 to 14/14.  It applies to the whole run (ngspice cannot scope Tmax
to an interval), so the run is slow -- that is the price of trusting it.

  usage:  scripts/gen_chip_tb.py [--models PATH] [-o OUT]
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

NGDIR = os.path.join(_cfg.ROOT, "ngspice")
OUT_SPICE = os.path.join(NGDIR, "tb_chip_spi.spice")
OUT_JSON = os.path.join(NGDIR, "tb_chip_spi_expected.json")
CONN = os.path.join(_cfg.CHIP, "gio_connections.json")

# The PDK as it sits on the design machine.  --models overrides it (the cloud
# container keeps the same tree somewhere else).
MODELS = "~/Dropbox/91_OpenPDK/TR-1um/libs.tech/spice/models/ip62_models"
NETLIST = "tr_1um_3wire_SPI_sim_ready.spice"

VDD = 5.0
SETTLE = 5e-6              # power-up / reset settling before the first frame

# Clock, set by set_clock() from --sclk.  The default is the rate the
# functional run is signed off at; the sweep in design_notes.md 18.9 drives
# the same twelve checks up the frequency axis to find where they break.
TCK = 1e-6                 # SCLK period -- 1 MHz
TH = TCK / 2.0
TR = 20e-9                 # PWL edge time


def set_clock(f_hz):
    """SCLK frequency -> period, half period, and a sane edge time.

    The edge time has to scale: a fixed 20 ns ramp is a clean edge at 1 MHz
    and most of the cycle at 20 MHz, which would characterise the ramp
    generator rather than the chip.  TCK/25 keeps the edge the same fraction
    of the period, capped at 20 ns so the slow end stays realistic.
    """
    global TCK, TH, TR
    TCK = 1.0 / f_hz
    TH = TCK / 2.0
    TR = min(20e-9, TCK / 25.0)


WR1 = 0xA5
RD = 0x3D
WR2 = 0x5A


class Sig:
    """A digital stimulus: a start level and a list of (time, level) changes."""

    def __init__(self, name, v0, tr=None):
        self.name = name
        self.ev = [(0.0, float(v0))]
        # Per-signal edge time.  None means "scale with SCLK" -- right for the
        # SPI signals, wrong for reset, which has nothing to do with the clock
        # rate and whose ramp only has to be slow enough for the solver.  A
        # 2 ns ramp into the reset pad's ESD structures was enough to make
        # ngspice give up with "timestep too small" at one sweep point.
        self.tr = tr

    def set(self, t, v):
        v = float(v)
        if abs(self.ev[-1][1] - v) < 1e-9:
            return self
        if t <= self.ev[-1][0]:
            raise ValueError(f"{self.name}: {t} not after {self.ev[-1][0]}")
        self.ev.append((t, v))
        return self

    def pwl(self, tend):
        tr = self.tr if self.tr is not None else TR
        pts = [(0.0, self.ev[0][1])]
        for t, v in self.ev[1:]:
            pts.append((t - tr, pts[-1][1]))
            pts.append((t, v))
        pts.append((tend, pts[-1][1]))
        return "PWL(" + " ".join(f"{t:.9g} {v:.3f}" for t, v in pts) + ")"


def bits_msb_first(byte):
    return [(byte >> (7 - i)) & 1 for i in range(8)]


def build():
    conn = json.load(open(CONN))
    padmap = {int(k[1:]): v for k, v in conn["connections"].items()}
    pad_of_role = {v["role"]: f"P{n}" for n, v in padmap.items()}
    # DATA[i] -> pad, straight from the connection map so it cannot drift
    data_pad = [pad_of_role[f"DATA[{i}]"] for i in range(8)]
    p_sclk = pad_of_role["SCLK"]
    p_sdio = pad_of_role["SDIO"]
    p_cs = pad_of_role["CS"]
    p_dis = pad_of_role["DIS"]
    p_test = pad_of_role["TEST"]
    p_rstn = pad_of_role["RSTN"]

    rstn = Sig("rstn", 0, tr=100e-9)   # power-on reset, not a SPI edge
    cs = Sig("cs", VDD)
    dis = Sig("dis", 0)
    sclk = Sig("sclk", 0)
    sdio_m = Sig("sdio_m", 0)
    mgate = Sig("mgate", VDD)      # master owns SDIO
    txgate = Sig("txgate", 0)      # bench does NOT drive the DATA pads

    checks, timing = [], []

    def delay(name, trig, t_trig, trig_edge, targ, targ_edge, desc):
        """A TRIG/TARG delay, windowed with TD so it catches the edge we mean.

        ngspice counts RISE=/FALL= from TD onward, so TD lands just before the
        edge in question and the count is always 1 -- far more robust than
        counting edges from t=0 across a run with 24 clock pulses.
        """
        timing.append(dict(name=name, trig=trig, targ=targ, td=t_trig - 0.2 * TCK,
                           trig_edge=trig_edge, targ_edge=targ_edge, desc=desc))

    def level(name, t, node, expect, desc):
        checks.append(dict(name=name, kind="level", t=t, node=node,
                           expect=expect, desc=desc))

    def byte(name, t, nodes, expect, desc):
        checks.append(dict(name=name, kind="byte", t=t, nodes=list(nodes),
                           times=[t] * 8, expect=expect, desc=desc))

    def byte_at(name, times, nodes, expect, desc):
        checks.append(dict(name=name, kind="byte", t=times[0], nodes=list(nodes),
                           times=list(times), expect=expect, desc=desc))

    rstn.set(0.5e-6, VDD)

    # ---- after reset, before anything is clocked --------------------------
    t = SETTLE
    byte("rx_reset", t - 0.5e-6, data_pad, 0x00,
         "DATA pads read 0x00 out of reset")
    level("test_idle", t - 0.5e-6, p_test, "low",
          "byte_end low while CS is high")

    def clock_frame(t0, sdio_bits=None):
        """CS low at t0, eight SCLK pulses, CS high.  Returns the frame's edges."""
        cs.set(t0, 0)
        R, F = [], []
        for k in range(8):
            rk = t0 + TH + k * TCK
            fk = t0 + TCK + k * TCK
            sclk.set(rk, VDD)
            sclk.set(fk, 0)
            R.append(rk)
            F.append(fk)
            if sdio_bits is not None and k < 7:
                sdio_m.set(fk, VDD if sdio_bits[k + 1] else 0)
        t_end = t0 + 8 * TCK + TH
        cs.set(t_end, VDD)
        return R, F, t_end

    # ---- WRITE frame 1 ----------------------------------------------------
    b = bits_msb_first(WR1)
    sdio_m.set(t - TH, VDD if b[0] else 0)          # MSB valid before CS falls
    R, F, t = clock_frame(t, b)
    level("test_mid_wr1", R[2] + TH, p_test, "low",
          "byte_end low mid-frame (bit 3 of the WRITE)")
    level("test_last_wr1", R[6] + TH, p_test, "high",
          "byte_end high during the 8th bit of the WRITE")
    byte("rx_wr1", R[7] + 0.7 * TCK, data_pad, WR1,
         f"DATA pads carry the received 0x{WR1:02X}")
    # rx_data goes 0x00 -> 0xA5 on the 8th rising edge, so DATA[0] rises there:
    # SCLK at the pad -> input buffer -> flop -> output driver -> pad.
    delay("tco_data", p_sclk, R[7], "RISE", data_pad[0], "RISE",
          "SCLK 8th rising edge -> DATA[0] pad valid (WRITE)")

    # ---- turn the bus round: DIS high, master lets go of SDIO -------------
    t += 0.5e-6
    dis.set(t, VDD)
    txgate.set(t, VDD)
    mgate.set(t, 0)
    t += 2e-6                                        # let the 10k drives settle
    level("sdio_hiz_idle", t - 0.3e-6, p_sdio, "low",
          "SDIO high-Z with DIS=1 and CS high")
    byte("tx_pads", t - 0.3e-6, data_pad, RD,
         f"DATA pads are inputs carrying tx_data = 0x{RD:02X}")

    # ---- READ frame -------------------------------------------------------
    R, F, t = clock_frame(t)
    rb = bits_msb_first(RD)
    # bit i of the byte (i = LSB) was shifted out as MSB-first position 7-i,
    # sampled at that position's rising edge -- the middle of its window
    byte_at("read_byte", [R[7 - i] for i in range(8)], [p_sdio] * 8, RD,
            f"the chip shifted 0x{RD:02X} out on SDIO")
    level("test_last_rd", R[6] + TH, p_test, "high",
          "byte_end high during the 8th bit of the READ")
    # SDIO changes on FALLING edges in a READ.  Pick two where the byte really
    # does change, so there is an edge to measure rather than a flat line.
    for k in range(7):
        if rb[k] != rb[k + 1]:
            delay(f"tco_sdio_{'r' if rb[k + 1] else 'f'}{k}", p_sclk, F[k], "FALL",
                  p_sdio, "RISE" if rb[k + 1] else "FALL",
                  f"SCLK falling edge {k} -> SDIO bit {k + 1} valid (READ)")

    t += 1.0e-6
    level("sdio_hiz_after_read", t - 0.3e-6, p_sdio, "low",
          "SDIO released after CS rises (last bit driven was 1)")

    # ---- back to WRITE ----------------------------------------------------
    dis.set(t, 0)
    txgate.set(t, 0)
    mgate.set(t, VDD)
    t += 2e-6
    byte("rx_kept", t - 0.3e-6, data_pad, WR1,
         f"the READ frame left rx_data at 0x{WR1:02X}")

    b = bits_msb_first(WR2)
    sdio_m.set(t - TH, VDD if b[0] else 0)
    R, F, t = clock_frame(t, b)
    byte("rx_wr2", R[7] + 0.7 * TCK, data_pad, WR2,
         f"a second WRITE frame lands 0x{WR2:02X}")

    tend = t + 1.5e-6

    sigs = dict(rstn=(p_rstn, rstn), cs=(p_cs, cs), dis=(p_dis, dis),
                sclk=(p_sclk, sclk))
    return (conn, checks, timing, tend, sigs, sdio_m, mgate, txgate,
            data_pad, p_sdio, p_test)


def render(models, netlist, checks, timing, tend, sigs, sdio_m, mgate, txgate,
           data_pad, p_sdio, p_test, load_pf=0.0, master_ohm=1000.0):
    L = []
    A = L.append
    A("* tb_chip_spi.spice -- chip-level ngspice testbench for tr_1um_3wire_SPI.")
    A("* GENERATED by scripts/gen_chip_tb.py; do not edit by hand.")
    A("* Companion checker: scripts/check_chip_sim.py, using")
    A("* ngspice/tb_chip_spi_expected.json written by the same run.")
    A("*")
    A(f"*   WRITE 0x{WR1:02X} (DIS=0) -> READ 0x{RD:02X} (DIS=1) -> WRITE 0x{WR2:02X}")
    A(f"*   SCLK {1.0/TCK/1e6:g} MHz, Mode 0, MSB first, {tend*1e6:.1f} us total")
    A("*")
    A(f".include '{models}'")
    A(f".include '{netlist}'")
    A("")
    A(f"vvdd VDD 0 DC {VDD}")
    A("vvss VSS 0 DC 0")
    A("")
    for key, (pad, sig) in sigs.items():
        A(f"v{key} {pad} 0 {sig.pwl(tend)}")
    A("")
    A("* Only one side ever drives a bidirectional pad.  TXGATE mirrors DIS, so")
    A("* the bench's tx_data drives are open exactly while the chip owns the")
    A("* DATA pads; MGATE is high only during the WRITE frames.")
    A(".model TXSW SW(RON=10 ROFF=1T VT=2.5 VH=0.3)")
    A(f"vtxgate TXGATE 0 {txgate.pwl(tend)}")
    A(f"vmgate MGATE 0 {mgate.pwl(tend)}")
    A("")
    for i, pad in enumerate(data_pad):
        v = VDD if (RD >> i) & 1 else 0.0
        A(f"vtx{i} vtx{i}n 0 DC {v:.1f}   $ tx_data[{i}] of 0x{RD:02X}")
        A(f"stx{i} vtx{i}n vtx{i}s TXGATE 0 TXSW")
        A(f"rtx{i} vtx{i}s {pad} 10k")
        A(f"rpd{i} {pad} VSS 1MEG   $ DC path + Hi-Z level; 1% of a logic level")
    A("")
    A(f"vsdio SDIO_M 0 {sdio_m.pwl(tend)}")
    A(f"ssdio SDIO_M SDIO_S MGATE 0 TXSW")
    A(f"rsdio SDIO_S {p_sdio} {master_ohm:g}")
    A("*   ^ the master's own output impedance.  It matters once the pad is")
    A("*   loaded and clocked fast: 1k into 20 pF is a 20 ns time constant,")
    A("*   which starts limiting the WRITE side before the chip does.  Lower it")
    A("*   with --master-ohm to separate the bench's master from the part.")
    A(f"rsdio_pd {p_sdio} VSS 20k   $ pulls SDIO to 0 the moment a driver lets go")
    A("")
    ports = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "VSS",
             "P9", "P10", "P11", "P12", "P13", "P14", "P15", "VDD"]
    if load_pf:
        A(f"* External load: {load_pf:g} pF on every signal pad.  Without this the")
        A("* only capacitance the output drivers see is the pad's own, which is")
        A("* not what a bonded part on a board looks like -- and clock-to-out is")
        A("* what sets the maximum SCLK.")
        for p in ports:
            if p not in ("VDD", "VSS"):
                A(f"cload{p} {p} VSS {load_pf:g}p")
    A("xdut " + " ".join(ports) + f" {_cfg.CHIP_TOP_CELL}")
    A("")
    A("* Tmax = 1ns (4th argument).  See this file's generator for why anything")
    A("* coarser is not trustworthy on this design.")
    A(f".tran {TCK/20:.9g} {tend:.9g} 0 1n")
    A("")
    A("* ---- checks ----")
    for c in checks:
        if c["kind"] == "level":
            A(f".measure tran {c['name']} FIND v({c['node']}) "
              f"AT={c['t']:.9g}  $ {c['desc']}")
        else:
            for i, (node, t) in enumerate(zip(c["nodes"], c["times"])):
                A(f".measure tran {c['name']}_bit{i} FIND v({node}) AT={t:.9g}"
                  f"  $ {c['desc']} -- bit{i}")
    if timing:
        A("")
        A("* ---- timing, reported not judged ----")
        A("* TD windows each measure just before the edge it means, so the")
        A("* RISE=/FALL= count is 1 rather than a count from t=0 across 24")
        A("* clock pulses.  These are what set the maximum SCLK: the master")
        A("* samples SDIO half a period after the falling edge that changed it.")
        for d in timing:
            A(f".measure tran {d['name']} TRIG v({d['trig']}) VAL={VDD/2:g} "
              f"{d['trig_edge']}=1 TD={d['td']:.9g} TARG v({d['targ']}) "
              f"VAL={VDD/2:g} {d['targ_edge']}=1 TD={d['td']:.9g}"
              f"  $ {d['desc']}")
    A("")
    A("* The control block exists only to hold `save`, which keeps the output")
    A("* vectors down to the sixteen pads instead of every node in the chip.")
    A("* Deliberately NO `run` and no `print`: in batch mode ngspice runs the")
    A("* .tran card itself, so a `run` here makes it run the whole analysis a")
    A("* SECOND time and print a second, identical measurement block -- twice")
    A("* the wall clock for nothing.  (The I2C bench carried that `run`; both")
    A("* of its passes agreed to the last digit, which is how we know it was")
    A("* redundant rather than wrong.)  ngspice auto-prints every .measure")
    A("* result once, and that block is the sole thing the checker parses.")
    A(".control")
    A("  save " + " ".join(f"v({p})" for p in ports if p not in ("VDD", "VSS")))
    A(".endc")
    A("")
    A(".end")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=MODELS)
    ap.add_argument("--sclk", type=float, default=1.0,
                    help="SCLK frequency in MHz (default 1.0)")
    ap.add_argument("--master-ohm", type=float, default=1000.0,
                    help="the bench master's series resistance into SDIO, ohms "
                         "(default 1000)")
    ap.add_argument("--load-pf", type=float, default=0.0,
                    help="external capacitance on every signal pad, pF "
                         "(default 0 -- the pad's own capacitance only)")
    ap.add_argument("--netlist", default=NETLIST)
    ap.add_argument("-o", "--out", default=OUT_SPICE)
    ap.add_argument("-j", "--json", default=OUT_JSON)
    args = ap.parse_args()
    set_clock(args.sclk * 1e6)

    (conn, checks, timing, tend, sigs, sdio_m, mgate, txgate,
     data_pad, p_sdio, p_test) = build()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(render(args.models, args.netlist, checks, timing, tend, sigs,
                       sdio_m, mgate, txgate, data_pad, p_sdio, p_test,
                       args.load_pf, args.master_ohm))
    with open(args.json, "w") as f:
        json.dump(checks, f, indent=1)
        f.write("\n")

    n_meas = sum(1 if c["kind"] == "level" else 8 for c in checks)
    print(f"wrote {args.out}")
    print(f"wrote {args.json}")
    print(f"{len(checks)} check(s) + {len(timing)} timing measure(s), "
          f"{n_meas + len(timing)} .measure statement(s), "
          f"tstop {tend*1e6:.2f} us at SCLK {1.0/TCK/1e6:g} MHz "
          f"(edge {TR*1e9:g} ns, load {args.load_pf:g} pF), Tmax 1 ns")
    for c in checks:
        exp = c["expect"] if c["kind"] == "level" else f"0x{c['expect']:02X}"
        print(f"  [t={c['t']*1e9:9.0f} ns] {c['name']:<20s} {str(exp):<6s} {c['desc']}")


if __name__ == "__main__":
    main()
