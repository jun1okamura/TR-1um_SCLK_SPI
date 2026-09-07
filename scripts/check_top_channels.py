#!/usr/bin/env python3
"""check_top_channels.py -- will the chip-level nets fit in the channels?

The core sits inside the GIO ring with four corridors around it, plus one under
it:

    T   above the core          L,R  beside the core (wall to core edge)
    B   below the PTECT box     U    between the core's bottom edge and PTECT

T, R, B and L form a closed loop.  Unrolling that loop into one coordinate
turns every net into an interval on a circle -- the same "unrolled ring
interval" the I2C project's own routing plan minimised -- and the deepest stack
of overlapping intervals inside a corridor is the number of tracks that
corridor has to provide.  U is a spur off the loop: a core pin on the bottom
edge drops into it and runs sideways until it meets L or R.

Each net takes the shorter way round; a net with several terminals takes the
smallest arc covering all of them (the complement of its largest gap).  One
track per net, no jogs, no via cost -- a lower bound.  A corridor already over
capacity here will not route, and the placement is the cheap thing to change.

  usage:  scripts/check_top_channels.py [--pitch 5.4]
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

PLAN = os.path.join(_cfg.CHIP, "signal_routing_plan.json")


class Ring:
    """The T-R-B-L loop, unrolled into one coordinate starting at the
    bottom-left corner and running counter-clockwise."""

    def __init__(self, geom):
        cl, cb, cr, ct = geom["core_chip_bbox"]
        _, py0, _, py1 = geom["ptect_box"]
        w = _cfg.GIO_INNER_WALL
        self.width = {"T": w - ct, "B": py0 + w, "L": cl + w, "R": w - cr, "U": cb - py1}
        self.lx, self.rx = (-w + cl) / 2, (cr + w) / 2      # side corridor centrelines
        self.by, self.ty = (-w + py0) / 2, (ct + w) / 2     # bottom/top centrelines
        self.uy = (py1 + cb) / 2                            # under-core centreline
        self.core_x = (cl, cr)
        self.hspan, self.vspan = self.rx - self.lx, self.ty - self.by
        self.bounds = {                                     # corridor -> s interval
            "B": (0.0, self.hspan),
            "R": (self.hspan, self.hspan + self.vspan),
            "T": (self.hspan + self.vspan, 2 * self.hspan + self.vspan),
            "L": (2 * self.hspan + self.vspan, 2 * (self.hspan + self.vspan)),
        }
        self.total = 2 * (self.hspan + self.vspan)

    def s_top(self, x):
        return self.hspan + self.vspan + (self.rx - x)

    def s_bottom(self, x):
        return x - self.lx

    def s_left(self, y):
        return 2 * self.hspan + self.vspan + (self.ty - y)

    def s_right(self, y):
        return self.hspan + (y - self.by)

    def terminal_s(self, t):
        return {"TOP": lambda: self.s_top(t["x"]), "BOTTOM": lambda: self.s_bottom(t["x"]),
                "LEFT": lambda: self.s_left(t["y"]), "RIGHT": lambda: self.s_right(t["y"])}[t["edge"]]()

    def arc(self, points):
        """Smallest arc covering every s in points -> (start, length)."""
        pts = sorted(p % self.total for p in points)
        if len(pts) == 1:
            return pts[0], 0.0
        gaps = [(pts[(i + 1) % len(pts)] - pts[i]) % self.total for i in range(len(pts))]
        widest = max(range(len(pts)), key=lambda i: gaps[i])
        return pts[(widest + 1) % len(pts)], self.total - gaps[widest]

    def spans(self, start, length):
        """Split an arc into (corridor, s0, s1) pieces."""
        out = []
        s = start % self.total
        left = length
        while left > 1e-9:
            for c, (a, b) in self.bounds.items():
                if a - 1e-9 <= s < b - 1e-9 or (s < 1e-9 and a < 1e-9):
                    step = min(b - s, left)
                    out.append((c, s, s + step))
                    s = (s + step) % self.total
                    left -= step
                    break
            else:
                s = 0.0
        return out


def net_paths(net, ring):
    """[(corridor, lo, hi)] for one net, taking the cheaper entry for a core pin
    that has to come out from under the core."""
    terms = [ring.terminal_s(t) for t in net["gio"]]
    core = net["core"]
    if core is None:
        return [], None
    if core["edge"] == "TOP":
        return _lay(ring, terms + [ring.s_top(core["x"])]), None

    # bottom-edge pin: drop into U, run to the left or right corridor
    best = None
    for side, ex, s_entry in (("L", ring.core_x[0], ring.s_left(ring.uy)),
                              ("R", ring.core_x[1], ring.s_right(ring.uy))):
        start, length = ring.arc(terms + [s_entry])
        u = abs(core["x"] - ex)
        if best is None or length + u < best[0]:
            best = (length + u, _lay(ring, terms + [s_entry]),
                    ("U", min(core["x"], ex), max(core["x"], ex)))
    return best[1], best[2]


def _lay(ring, points):
    start, length = ring.arc(points)
    return ring.spans(start, length)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--pitch", type=float, default=_cfg.TRACK_PITCH)
    args = ap.parse_args()

    plan = json.load(open(args.plan))
    ring = Ring(_cfg.chip_geometry())

    load = {k: [] for k in ("T", "R", "B", "L", "U")}
    skipped = []
    for name, net in sorted(plan["nets"].items()):
        if net["core"] is None:
            skipped.append(name)
            continue
        segs, u = net_paths(net, ring)
        for c, lo, hi in segs:
            load[c].append((lo, hi, name))
        if u:
            load["U"].append((u[1], u[2], name))

    print(f"{'corridor':9s} {'width':>7s} {'tracks':>7s} {'needed':>7s}   busiest nets")
    bad = 0
    for k in ("T", "R", "B", "L", "U"):
        segs = load[k]
        cap = int(ring.width[k] // args.pitch)
        peak, who = 0, []
        for lo, _, _ in segs:
            here = [n for a, b, n in segs if a - 1e-9 <= lo <= b + 1e-9]
            if len(here) > peak:
                peak, who = len(here), here
        ok = peak <= cap
        bad += 0 if ok else 1
        print(f"{k:9s} {ring.width[k]:7.1f} {cap:7d} {peak:7d}   "
              f"{'ok  ' if ok else 'OVER'} {', '.join(sorted(who)[:6])}"
              f"{' ...' if len(who) > 6 else ''}")
    if skipped:
        print(f"\nnot modelled (no core pin yet): {skipped}")
    print(f"\npitch {args.pitch} um; one track per net, shortest arc, no jogs -- a lower bound.")
    if bad:
        print(f"{bad} corridor(s) over capacity.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
