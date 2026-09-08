#!/usr/bin/env python3
"""route_chip.py -- wire the core to the GIO pad ring.

    layout/chip/step1_assembled.gds        (core + ring + PTECT, placement only)
  + layout/chip/signal_routing_plan.json   (every endpoint, generated)
  -> layout/chip/step2_routed.gds

The ring engine is the I2C project's, kept intact: perimeter_s / s_to_xy /
ring_waypoints / project_to_R / seg_layer / unroll are copied verbatim from
scripts/i2c_ref/route_gio_core_v10.py, along with its lane-packing scheme and
the hard-won rule that a net's direction must be the one whose footprint stays
inside the simple unrolled interval the packer assumes -- trusting "shortest
arc" instead once packed two nets that physically overlapped into one lane.

Every wire is Manhattan and the layer follows from the direction: horizontal is
M1, vertical is M2 (seg_layer), with a via wherever that changes.

WHAT IS DIFFERENT HERE -- THE UNDER-CORE CORRIDOR
------------------------------------------------
The I2C core filled its frame, so a pin on its bottom edge dropped straight
down into the bottom channel and joined the ring there.  This core is 314 um
tall in an 1840 um opening, and the PTECT keep-out (layer 63/1: the DRC deck
makes any metal inside it an error) fills the space below it.  A pin on this
core's bottom edge -- 11 of the 24 nets -- has nowhere to go but sideways.

So bottom-edge nets first travel the U corridor, the 80 um channel between the
core's bottom edge and PTECT: down from the pin on M2, west on M1 at their own
U lane, and out past the core's left edge, where they turn up the left corridor
and join the ring like any other net.  U lanes are packed the same way ring
lanes are.  Their ring interval starts at the point where they enter, not at
the core pin, or the packer would be reasoning about a wire that isn't there.

POWER
-----
Each of the four TAP columns is one continuous M2 strap from the core's bottom
edge to its top (checked directly against the routed core), so tying a column
at one end powers all of it.  That frees both buses from having to reach both
edges:

  VDD  a 10 um M1 bus in the T corridor at y=838 -- above the core's top edge
       at 830, below the first signal lane at 847 -- tying the four top VDD tap
       pins, then five M2 risers up across the signal-lane band to the ring's
       one real core-facing VDD pin, an M1 rect at (50,920)-(350,934).  The
       risers cross the lane band on M2 because the lanes are M1 there; they
       hop back to M1 at CROSS_Y to enter the pin.
  GND  a 10 um M1 bus in the U corridor tying the four bottom GND tap pins,
       then one M2 leg down each side of the core at |x| = 838 to a SECOND
       10 um M1 bus bar at y = -795 running the width of the die, and from
       that five 10 um M2 strips straight down into the frame's own VSS pin
       -- M2 along the whole bottom edge, in from the die edge to y = -920 --
       right under the VSS bond pad.

The ring's own VDD pin (M1) and VSS pin (M2) overlap in plan view by design at
the top edge; the risers stay on M1 there and place no via, which is what keeps
them apart.  The I2C project shorted exactly this pair once by treating the M2
shape as VDD's own bus.

  usage:  scripts/route_chip.py [-o OUT]
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg

sys.path.insert(0, _cfg.pdk_tech_python())
import pya  # noqa: E402
from cells import tr_1um  # noqa: E402

IN_GDS = os.path.join(_cfg.CHIP, "step1_assembled.gds")
OUT_GDS = os.path.join(_cfg.CHIP, "step2_routed.gds")
PLAN = os.path.join(_cfg.CHIP, "signal_routing_plan.json")
CONN = os.path.join(_cfg.CHIP, "gio_connections.json")

M1_LAYER = (13, 0)
M2_LAYER = (20, 0)
M1_WIRE_W = 1.8
M2_WIRE_W = 3.4
VIA_PAD = 3.4

# ---- ring geometry (I2C values; see that script's own comments) ----------
R_NOM = 880.0
PERI = 8 * R_NOM
CUT = 4625.0
LANE_R0 = 847.0          # first lane, 30.7 um outside the core's edge at 816.3
LANE_PITCH = 5.4         # M2 3.4 wide + exactly the 2.0 um M2 minimum gap,
                         # which this deck accepts -- the core's own TAP straps
                         # already sit at that spacing (I2C 79.8)
NEAR_R = 915.0           # radial hop that meets each terminal, 5 um inside 921.7
# The DIS chain and the tie-off chain ride just above the signal lanes -- there
# is room here, unlike the I2C chip, and below the lanes is PTECT's own edge at
# -840.  Both radii are derived from however many lanes the packing needed.

# ---- the 30.7 um strip between the core's edge (816.3) and lane 0 (847) --
# Four private radii fit here at the M2 minimum spacing; the numbers are
# checked by scripts/check_chip.py, not by eye.
TIE_R = {"HIZ3": 820.0, "HIZ5": 825.5, "HIZ4": 831.0}
GND_LEG_W = 6.0

# ---- power ---------------------------------------------------------------
BUS_W = 10.0
TAP_STUB_W = 3.4         # matches the core's own native strap width exactly,
                         # so the join is flush -- a stepped width reads as a
                         # notch to the real width checker (I2C 79.8)
VDD_BUS_Y = 838.0
GIO_VDD_PIN = (200.0, 927.0)     # inside the M1 rect (50,920)-(350,934)
# The risers hop M2 -> M1 ABOVE the whole lane band: their M1 is vertical, so
# any of it level with a lane would cross that lane's horizontal M1 outright.
# Nine narrow risers rather than the I2C's five wide ones -- at the wire's own
# width the via pad adds no step, and a stepped width is what the real width
# checker reads as a notch (I2C 79.8).  Room here is 4 um of clearance below
# and the ring wall at 920 above.
VDD_CROSS_Y = 916.0
VDD_RISER_W = 3.4
VDD_RISER_DX = tuple(8.0 * i for i in range(-4, 5))
# GND leaves the core the way the I2C chip's did: one M2 leg down each side of
# the core to a second M1 bus bar along the bottom of the die, and from that bar
# five wide M2 strips straight into the frame's own VSS pin, 60 um of M2 running
# the whole bottom edge, right under the VSS bond pad.
#
# The first version instead hopped sideways from the U-corridor bus to the ring's
# GND terminals at (-+921.7, 600).  That IS the VSS net -- the frame ties its GND
# terminals to the VSS pad internally, which is why LVS passed -- but it makes
# the core's return current travel a quarter of the way around the ring to reach
# the pad.  This gives it a direct path instead.
GND_LEG_X = (-838.0, 838.0)      # same x as the old legs: 4.3 um clear of lane 0
# The frame brings its own PTECT boxes at three die corners; the bottom-right
# one is (810,-1120)-(1120,-810), so anything at |x| >= 810 has to stay above
# y = -810.  The bus and both legs therefore stop at -795, 15 um clear of it --
# not at -838 mirroring VDD, which put the right leg and the bus's right end
# straight inside that box.
GND_BOT_BUS_Y = -795.0
# The frame's VSS pin is M2 running the whole bottom edge, from the die edge in
# to y = -920 -- the ring's inner wall, the same 920 every terminal sits on.  An
# early cut of this stopped the strips at -908 on a mis-read of the pin's inner
# edge, which left them 12 um short of the metal they were supposed to land on;
# LVS saw GND and VSS as two nets and said so.  Land 10 um inside it instead.
GIO_VSS_PIN_Y = -930.0
VSS_STRIP_X = -200.0
VSS_STRIP_W = 10.0
VSS_STRIP_DX = (-24.0, -12.0, 0.0, 12.0, 24.0)   # 10 um wide on a 12 um pitch
# The frame's own GND terminals, one every 400 um around the ring.
GND_RING_PINS = [(sx * 921.7, sy * v, e)
                 for v in (200.0, 600.0)
                 for sx, sy, e in ((-1, -1, "LEFT"), (-1, 1, "LEFT"),
                                   (1, -1, "RIGHT"), (1, 1, "RIGHT"))] + \
                [(sx * v, sy * 921.7, e)
                 for v in (200.0, 600.0)
                 for sx, sy, e in ((-1, -1, "BOTTOM"), (-1, 1, "TOP"),
                                   (1, -1, "BOTTOM"), (1, 1, "TOP"))]

V1_CUT = 1.4
MIN_VIA_SPACE = 1.5


# --------------------------------------------------------------------------
# ring primitives -- verbatim from scripts/i2c_ref/route_gio_core_v10.py
# --------------------------------------------------------------------------
def perimeter_s(x, y, edge, R):
    if edge == "TOP":    return max(-R, min(R, x)) + R
    if edge == "RIGHT":  return 2 * R + (R - max(-R, min(R, y)))
    if edge == "BOTTOM": return 4 * R + (R - max(-R, min(R, x)))
    if edge == "LEFT":   return 6 * R + (max(-R, min(R, y)) + R)
    raise ValueError(edge)


def s_to_xy(s, R):
    P = 8 * R
    s = s % P
    if s <= 2 * R: return (s - R, R)
    if s <= 4 * R: return (R, R - (s - 2 * R))
    if s <= 6 * R: return (R - (s - 4 * R), -R)
    return (-R, (s - 6 * R) - R)


def ring_waypoints(s1, s2, R, force_dir=None):
    P = 8 * R
    corners = [2 * R, 4 * R, 6 * R, 8 * R]
    if force_dir is None:
        direction = "CW" if (s2 - s1) % P <= (s1 - s2) % P else "CCW"
    else:
        direction = force_dir
    pts = []
    if direction == "CW":
        span = (s2 - s1) % P
        for k in corners:
            off = (k - s1) % P
            if 0 < off < span: pts.append((off, k % P))
    else:
        span = (s1 - s2) % P
        for k in corners:
            off = (s1 - k) % P
            if 0 < off < span: pts.append((off, k % P))
    pts.sort()
    return [s_to_xy(k, R) for _, k in pts]


def project_to_R(px, py, edge, R):
    if edge in ("TOP", "BOTTOM"):
        return (px, R if edge == "TOP" else -R)
    return (R if edge == "RIGHT" else -R, py)


def seg_layer(a, b):
    if abs(a[1] - b[1]) < 1e-6 and abs(a[0] - b[0]) >= 1e-6: return "M1"
    if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) >= 1e-6: return "M2"
    raise ValueError(f"non-manhattan or zero-length segment {a} {b}")


def unroll(s): return (s - CUT) % PERI


# --------------------------------------------------------------------------
def pack(intervals, margin=5.0):
    """Greedy interval scheduling: {key: lane index}.  I2C's own scheme."""
    lane_last, lane_of = [], {}
    for key, (lo, hi) in sorted(intervals.items(), key=lambda kv: kv[1][0]):
        for i, last in enumerate(lane_last):
            if last < lo - margin:
                lane_of[key] = i
                lane_last[i] = hi
                break
        else:
            lane_of[key] = len(lane_last)
            lane_last.append(hi)
    return lane_of, len(lane_last)


def build_plan(plan, conn, geom):
    """Every route the chip needs, with a ring lane for each.

    A route is a pair of endpoints and one arc between them.  Signal nets are
    core-pin to pad terminal; a net that reaches several terminals is wired to
    its pad and the rest are chained pad-to-pad, which is the I2C chip's own
    DIS architecture and keeps a nine-terminal net from claiming a lane all the
    way round.  Chain links and tie-off links are routes too, packed in the same
    pool, so the band is shared rather than carved up in advance.
    """
    cl, cb, cr, ct = geom["core_chip_bbox"]
    pads = conn["pad_pin_coords"]
    nets = {k: v for k, v in plan["nets"].items() if v["core"]}

    primary, chained = {}, {}
    for name, net in nets.items():
        if len(net["gio"]) == 1:
            primary[name], chained[name] = net["gio"][0], []
            continue
        pad = [g for g in net["gio"] if g["terminal"].startswith("P")]
        if len(pad) != 1:
            raise SystemExit(f"{name}: {len(net['gio'])} terminals but "
                             f"{len(pad)} of them a pad -- no obvious chain root")
        primary[name] = pad[0]
        chained[name] = net["gio"]

    def gio_ep(net, term):
        p = pads[term]
        return ("gio", net, p["x"], p["y"], p["edge"], "M2")

    # ---- chain links: consecutive terminals in ring order -----------------
    links = []
    for name, terms in sorted(chained.items()):
        if not terms:
            continue
        order = sorted((g["terminal"] for g in terms),
                       key=lambda tn: perimeter_s(pads[tn]["x"], pads[tn]["y"],
                                                  pads[tn]["edge"], R_NOM))
        for a, b in zip(order, order[1:]):
            links.append((f"{name}:{a}-{b}", gio_ep(name, a), gio_ep(name, b)))
    # An input-only pad leaves its OUT pin floating -- a real gate input inside
    # OSS_ESD_5V_DIO with nothing driving it.  Each one is tied to the NEAREST
    # ring GND terminal, which on this frame is always 20 um away on the same
    # edge; chaining them into one sweep round the ring instead cost four long
    # routes and three extra lanes for no benefit.
    for tn in sorted(conn["dont_care_pins"]):
        p = pads[tn]
        near = min(GND_RING_PINS,
                   key=lambda g: abs(g[0] - p["x"]) + abs(g[1] - p["y"]))
        links.append((f"GND:{tn}", gio_ep("GND", tn),
                      ("gio", "GND", near[0], near[1], near[2], "M2")))

    bottom = sorted(k for k in nets if nets[k]["core"]["edge"] == "BOTTOM")
    u_lo, u_hi = geom["ptect_box"][3], cb

    def ep_s(ep, u_lane_y, sides, R):
        kind, name, x, y, edge, _ = ep
        if kind == "gio":
            return perimeter_s(x, y, edge, R)
        if edge == "TOP":
            return perimeter_s(x, ct, "TOP", R)
        side = sides[name]
        return perimeter_s(-R if side == "L" else R, u_lane_y[name],
                           "LEFT" if side == "L" else "RIGHT", R)

    def evaluate(sides):
        u_iv = {n: ((min(nets[n]["core"]["x"], cl), max(nets[n]["core"]["x"], cl))
                    if sides[n] == "L" else
                    (min(nets[n]["core"]["x"], cr), max(nets[n]["core"]["x"], cr)))
                for n in bottom}
        u_lane, n_u = pack(u_iv, margin=_cfg.TRACK_PITCH)
        u_lane_y = {n: u_hi - 6.0 - u_lane[n] * _cfg.TRACK_PITCH for n in bottom}

        routes = [(name, ("core", name, net["core"]["x"], net["core"]["y"],
                          net["core"]["edge"], net["core"]["layer"]),
                   gio_ep(name, primary[name]["terminal"]))
                  for name, net in nets.items()] + links

        interval, direction = {}, {}
        for rname, a, b in routes:
            # The I2C project's rule, and the reason it exists: the packer
            # compares the SIMPLE, non-wrapping range between the endpoints, so
            # the wire must be the arc that stays inside it.  Taking the shorter
            # arc instead can wrap the seam, leaving the packer bounding a wire
            # that is somewhere else entirely -- that is how two physically
            # overlapping nets once ended up sharing a lane.
            u1 = unroll(ep_s(a, u_lane_y, sides, R_NOM))
            u2 = unroll(ep_s(b, u_lane_y, sides, R_NOM))
            interval[rname] = (min(u1, u2), max(u1, u2))
            direction[rname] = "CW" if u1 < u2 else "CCW"
        lane_of, n_lanes = pack(interval)
        return n_lanes, n_u, u_lane_y, routes, interval, direction, lane_of

    best = None
    for mask in range(1 << len(bottom)):
        sides = {n: ("R" if mask >> i & 1 else "L") for i, n in enumerate(bottom)}
        r = evaluate(sides)
        cost = (r[0], r[1], sum(hi - lo for lo, hi in r[4].values()))
        if best is None or cost < best[0]:
            best = (cost, sides, r)
    sides = best[1]
    n_lanes, n_u, u_lane_y, routes, interval, direction, lane_of = best[2]

    if bottom and min(u_lane_y.values()) < u_lo + 14.0:
        raise SystemExit(f"U corridor too shallow: {n_u} lanes need "
                         f"{u_hi - min(u_lane_y.values()):.1f} um of {u_hi - u_lo:.1f}")
    return nets, routes, interval, direction, lane_of, n_lanes, sides, u_lane_y, n_u


# --------------------------------------------------------------------------
class Drawer:
    def __init__(self, layout, top):
        self.layout, self.top = layout, top
        self.dbu = layout.dbu
        self.m1 = layout.layer(*M1_LAYER)
        self.m2 = layout.layer(*M2_LAYER)
        self.idx = {"M1": self.m1, "M2": self.m2}
        self.w = {"M1": M1_WIRE_W, "M2": M2_WIRE_W}
        tr_1um("TR-1um")
        self.via_lib = pya.Library.library_by_name("TR-1um", "*")
        self.via_decl = self.via_lib.layout().pcell_declaration("via_1")
        self.shapes = defaultdict(list)
        self.net = None
        self._via_done = set()

    def um(self, v): return int(round(v / self.dbu))

    def wire(self, layer, x0, y0, x1, y1, w=None):
        w = w if w is not None else self.w[layer]
        hw = w / 2.0
        if abs(x0 - x1) < 1e-6:
            box = db.Box(self.um(x0 - hw), self.um(min(y0, y1)),
                         self.um(x0 + hw), self.um(max(y0, y1)))
        elif abs(y0 - y1) < 1e-6:
            box = db.Box(self.um(min(x0, x1)), self.um(y0 - hw),
                         self.um(max(x0, x1)), self.um(y0 + hw))
        else:
            raise ValueError(f"non-manhattan segment {(x0, y0, x1, y1)}")
        self.top.shapes(self.idx[layer]).insert(box)
        if self.net:
            self.shapes[self.net].append(
                (layer, box.left * self.dbu, box.bottom * self.dbu,
                 box.right * self.dbu, box.top * self.dbu))

    def via(self, cx, cy, pad=VIA_PAD):
        key = (round(cx, 3), round(cy, 3))
        if key in self._via_done:
            return
        self._via_done.add(key)
        idx = self.layout.add_pcell_variant(
            self.via_lib, self.via_decl.id(),
            {"x": pad, "y": pad, "x0": "c", "y0": "c"})
        self.top.insert(db.CellInstArray(idx, db.Trans(db.Vector(self.um(cx), self.um(cy)))))
        if self.net:
            hw = pad / 2.0
            self.shapes[self.net].append(("VIA", cx - hw, cy - hw, cx + hw, cy + hw))

    def via_row(self, cx, cy, n=2, pad=VIA_PAD, vertical=False):
        if n == 1:
            self.via(cx, cy, pad); return
        pitch = V1_CUT + MIN_VIA_SPACE + 0.2
        a0 = (cy if vertical else cx) - pitch * (n - 1) / 2.0
        for i in range(n):
            if vertical:
                self.via(cx, a0 + i * pitch, pad)
            else:
                self.via(a0 + i * pitch, cy, pad)

    def path(self, pts, start_layer=None, end_layer=None, w=None):
        """Draw a Manhattan polyline, layer per segment, vias at the changes."""
        layers = [seg_layer(pts[k], pts[k + 1]) for k in range(len(pts) - 1)]
        if start_layer and start_layer != layers[0]:
            self.via(*pts[0])
        for k, L in enumerate(layers):
            a, b = pts[k], pts[k + 1]
            self.wire(L, a[0], a[1], b[0], b[1], w)
            if k > 0 and layers[k - 1] != L:
                self.via(a[0], a[1])
        if end_layer and end_layer != layers[-1]:
            self.via(*pts[-1])
        return layers


# --------------------------------------------------------------------------
# One route = one continuous polyline from A to B around the ring.
#
# Drawing the ring and the radial hop into a terminal as two separate paths is
# how this router's first version left every net open: the hop starts where the
# ring ends, and if the ring arrived vertically (M2) while the hop leaves
# horizontally (M1), the via at that corner belongs to neither call and nobody
# placed it.  One polyline, one pass, vias wherever the layer changes.
# --------------------------------------------------------------------------
def endpoint_path(ep, R, u_lane_y, sides, geom):
    """Points from the endpoint itself out to the ring at radius R, plus the
    ring edge it arrives on."""
    cl, cb, cr, ct = geom["core_chip_bbox"]
    kind, name, x, y, edge, layer = ep
    if kind == "gio":
        return ([(x, y), project_to_R(x, y, edge, NEAR_R), project_to_R(x, y, edge, R)],
                edge, layer)
    if edge == "TOP":
        return ([(x, ct), (x, R)], "TOP", layer)
    side = sides[name]
    uy = u_lane_y[name]
    ex = -R if side == "L" else R
    return ([(x, y), (x, uy), (ex, uy)], "LEFT" if side == "L" else "RIGHT", layer)


def draw_route(d, name, a, b, R, force_dir, u_lane_y, sides, geom):
    pa, ea, la = endpoint_path(a, R, u_lane_y, sides, geom)
    pb, eb, lb = endpoint_path(b, R, u_lane_y, sides, geom)
    s1 = perimeter_s(pa[-1][0], pa[-1][1], ea, R)
    s2 = perimeter_s(pb[-1][0], pb[-1][1], eb, R)
    pts = pa + ring_waypoints(s1, s2, R, force_dir) + list(reversed(pb))
    clean = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > 1e-6 or abs(p[1] - clean[-1][1]) > 1e-6:
            clean.append(p)
    d.net = name
    d.path(clean, start_layer=la, end_layer=lb)
    d.net = None
    return len(clean)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=OUT_GDS)
    args = ap.parse_args()

    plan = json.load(open(PLAN))
    conn = json.load(open(CONN))
    geom = _cfg.chip_geometry()
    cl, cb, cr, ct = geom["core_chip_bbox"]

    layout = db.Layout()
    layout.read(IN_GDS)
    top = layout.cell(_cfg.CHIP_TOP_CELL)
    if top is None:
        raise SystemExit(f"{_cfg.CHIP_TOP_CELL} not found in {IN_GDS}")
    d = Drawer(layout, top)

    pads = conn["pad_pin_coords"]
    (nets, routes, interval, direction, lane_of, n_lanes,
     sides, u_lane_y, n_u) = build_plan(plan, conn, geom)
    top_lane_r = LANE_R0 + (n_lanes - 1) * LANE_PITCH
    print(f"{len(routes)} route(s) ({len(nets)} signal net(s) + "
          f"{len(routes) - len(nets)} chain link(s)) in {n_lanes} ring lane(s), "
          f"R {LANE_R0:.1f}..{top_lane_r:.1f} (NEAR_R {NEAR_R})")
    # NEAR_R only names the point where a lane's radial hop meets its terminal;
    # the hop is collinear with the wire that follows, so it costs no room of
    # its own.  What the top lane really has to clear is the ring's inner wall.
    if top_lane_r + M2_WIRE_W / 2 + 2.0 > _cfg.GIO_INNER_WALL:
        raise SystemExit(f"top lane R={top_lane_r} does not clear the ring wall "
                         f"at {_cfg.GIO_INNER_WALL}")
    if top_lane_r >= NEAR_R:
        raise SystemExit(f"top lane R={top_lane_r} is outside NEAR_R={NEAR_R}")
    print(f"{n_u} U-corridor lane(s) y "
          f"{min(u_lane_y.values()):.1f}..{max(u_lane_y.values()):.1f} "
          f"(band {geom['ptect_box'][3]:.1f}..{cb:.1f})")

    for rname, a, b in sorted(routes):
        R = LANE_R0 + lane_of[rname] * LANE_PITCH
        n = draw_route(d, a[1], a, b, R, direction[rname], u_lane_y, sides, geom)
        print(f"  {rname:<22} lane={lane_of[rname]:<3} R={R:6.1f} "
              f"{direction[rname]} {n} point(s)")

    # ---- VDD -------------------------------------------------------------
    pads = conn["pad_pin_coords"]
    d.net = "VDD"
    tap_vdd = [-801.9, -267.3, 267.3, 807.3]
    tap_gnd = [-807.3, -272.7, 261.9, 801.9]
    bus_lo = min(min(tap_vdd), -max(TIE_R.values())) - 8.0
    bus_hi = max(tap_vdd) + 8.0
    d.wire("M1", bus_lo, VDD_BUS_Y, bus_hi, VDD_BUS_Y, BUS_W)
    for tx in tap_vdd:
        d.wire("M2", tx, ct - 1.5, tx, VDD_BUS_Y + 3.5, TAP_STUB_W)
        d.via_row(tx, VDD_BUS_Y, n=2, vertical=True)
    gx, gy = GIO_VDD_PIN
    if VDD_CROSS_Y < top_lane_r + 4.0:
        raise SystemExit(f"VDD_CROSS_Y={VDD_CROSS_Y} is inside the lane band "
                         f"(top lane {top_lane_r})")
    if VDD_CROSS_Y + VIA_PAD / 2 + 2.0 > _cfg.GIO_INNER_WALL:
        raise SystemExit(f"VDD_CROSS_Y={VDD_CROSS_Y} does not clear the ring wall")
    for dx in VDD_RISER_DX:
        sx = gx + dx
        d.via(sx, VDD_BUS_Y)
        d.wire("M2", sx, VDD_BUS_Y, sx, VDD_CROSS_Y, VDD_RISER_W)
        d.via(sx, VDD_CROSS_Y)
        d.wire("M1", sx, VDD_CROSS_Y, sx, gy, VDD_RISER_W)
    print(f"VDD bus M1 y={VDD_BUS_Y} x [{bus_lo},{bus_hi}], "
          f"{len(VDD_RISER_DX)} riser(s) to the M1 pin at {GIO_VDD_PIN}")

    # ---- GND -------------------------------------------------------------
    d.net = "GND"
    gnd_bus_y = min(u_lane_y.values()) - 9.0 if u_lane_y else (geom["ptect_box"][3] + cb) / 2
    if gnd_bus_y - BUS_W / 2 < geom["ptect_box"][3] + 2.0:
        raise SystemExit("GND bus does not clear PTECT")
    d.wire("M1", min(min(tap_gnd), min(GND_LEG_X)) - 8.0, gnd_bus_y,
           max(max(tap_gnd), max(GND_LEG_X)) + 8.0, gnd_bus_y, BUS_W)
    for tx in tap_gnd:
        d.wire("M2", tx, cb + 1.5, tx, gnd_bus_y - 3.5, TAP_STUB_W)
        d.via_row(tx, gnd_bus_y, n=2, vertical=True)
    # down each side to the bottom bus bar.  These legs are at |x| = 838, just
    # outside the PTECT box's own +-800, and they cross the ring's horizontal M1
    # lanes on the way: vertical M2 over horizontal M1 with no via is exactly how
    # every other route crosses a lane.
    for x in GND_LEG_X:
        d.via_row(x, gnd_bus_y, n=2, vertical=True)
        d.wire("M2", x, gnd_bus_y, x, GND_BOT_BUS_Y, GND_LEG_W)
        d.via_row(x, GND_BOT_BUS_Y, n=2, vertical=True)
    bot_lo, bot_hi = min(GND_LEG_X) - 7.0, max(GND_LEG_X) + 7.0
    d.wire("M1", bot_lo, GND_BOT_BUS_Y, bot_hi, GND_BOT_BUS_Y, BUS_W)
    for dx in VSS_STRIP_DX:
        sx = VSS_STRIP_X + dx
        d.via_row(sx, GND_BOT_BUS_Y, n=2)
        d.wire("M2", sx, GND_BOT_BUS_Y, sx, GIO_VSS_PIN_Y, VSS_STRIP_W)
    print(f"GND bus M1 y={gnd_bus_y:.1f} (core taps), legs at x {list(GND_LEG_X)} "
          f"down to the bottom M1 bus at y={GND_BOT_BUS_Y}, "
          f"{len(VSS_STRIP_DX)} M2 strip(s) into the frame's VSS pin at "
          f"({VSS_STRIP_X}, {GIO_VSS_PIN_Y})")

    # ---- HIZ ties --------------------------------------------------------
    for pin, rail in sorted(conn["power_ties"].items()):
        p = pads[pin]
        d.net = f"{pin}_{rail}_tie"
        if p["edge"] == "TOP" and rail == "VDD":
            d.via(p["x"], VDD_BUS_Y)
            d.wire("M2", p["x"], VDD_BUS_Y, p["x"], p["y"])
        elif p["edge"] == "LEFT":
            R = TIE_R[pin]
            bus_y = VDD_BUS_Y if rail == "VDD" else gnd_bus_y
            # ends inside the bus itself: the M2->M1 corner at the bus row is
            # where path() places the via, and the short M1 run past it lands on
            # the bus, which is why the bus is extended out to cover these radii
            d.path([(p["x"], p["y"]),
                    project_to_R(p["x"], p["y"], "LEFT", NEAR_R),
                    (-R, p["y"]), (-R, bus_y), (-R + 8.0, bus_y)],
                   start_layer="M2")
        else:
            raise SystemExit(f"no tie route for {pin} on the {p['edge']} edge")
        print(f"  {pin} -> {rail}")

    # ---- PTECT comes out --------------------------------------------------
    # PTECT is this project's OWN keep-out, drawn by assemble_top.py to stop the
    # signal router filling the space under the core; the fabricated chip has no
    # such layer, and the I2C project deletes it at the same point.  It has done
    # its job by now -- every signal net was routed with it in place, which is
    # what forced the U corridor -- and the GND bottom bus bar has to cross it.
    # check_chip.py keeps the guarantee honest: it re-checks each SIGNAL net's
    # own recorded shapes against the box, which it reads from the geometry JSON
    # rather than from the layer.
    pt = layout.layer(*_cfg.PTECT_LAYER)
    n_pt = top.shapes(pt).size()
    top.shapes(pt).clear()
    print(f"\nPTECT: {n_pt} shape(s) removed (layer {_cfg.PTECT_LAYER}); "
          f"the box it claimed was {tuple(geom['ptect_box'])}")

    with open(args.out.replace(".gds", "_net_shapes.json"), "w") as f:
        json.dump({k: v for k, v in d.shapes.items()}, f, indent=1)
    layout.write(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
