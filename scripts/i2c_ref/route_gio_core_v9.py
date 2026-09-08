"""
route_gio_core_v9.py (this session, "進めましょう" -- v9's chip-level GIO<->
core routing, successor to script/route_gio_core.py which was v7-specific)

Draws the 24 signal nets + VDD/GND onto src/tr_1um_i2c_slave_async.gds
(v9's core+GIO+PTECT placement, from script/assemble_top_v9.py), using
the SAME ring-routing/lane-packing algorithm as v7's proven
route_gio_core.py (unchanged: perimeter_s/s_to_xy/ring_waypoints/
project_to_R/seg_layer, R_NOM=880/LANE_R0=847/LANE_PITCH=6.0/NEAR_R=913.0/
DIS_R=840.0/VDD_PRIVATE_R=834.0), but with v9-specific inputs:

  - Signal net core-side coordinates: v9's REAL, GDS-extracted BBOX-edge
    pin positions (schematic/v9_signal_routing_plan.json, itself derived
    from schematic/v7_v9_pin_migration.json's "v9" entries -- ground
    truth pulled directly from layout/step8/v9_step_9_power_pins_added.gds
    via klayout.db, AFTER the sda_oe PORT_NET_ALIAS fix, design_notes.md
    78.7).
  - Signal net GIO-side coordinates: UNCHANGED from v7's NETS dict --
    these are fixed by OSS_FRAME_GIO's own schematic/pad layout, which
    did not change between v7 and v9 (only the core changed).
  - Direction (CW/CCW) and lane assignment: recomputed fresh for v9's
    source positions via the identical greedy-interval-scheduling
    algorithm (schematic/v9_signal_routing_plan.json) -- came out to 11
    lanes, matching v7's own 11-lane result, a good sanity signal.
  - VDD/GND: v9's power architecture is structurally different from v7
    (4 TAP-column pins x 2 edges(TOP/BOTTOM) = 8 pins/net, vs v7's
    row-rail-tap + 10um-bus-per-net scheme) -- schematic/
    v9_power_routing_plan.json assigns each of v9's 4 TAP columns
    (one pin per column per net, edge chosen to minimize ring-distance)
    directly to the SAME 8 GIO pads v7 used (VDD: main/HIZ2/HIZ7/HIZ15,
    VSS: main/HIZ9/HIZ10/OUT13 -- these are schematic-mandated ties on
    OSS_FRAME_GIO's own netlist, independent of core version). No
    v7-style row-rail bus is needed since v9's TAP columns already ARE
    the power-mesh exit points; each of the 4 per-net connections routes
    directly from its own TAP-column pin to its assigned GIO pad.
  - DIS chain (P7 + 8 HIZ pins, GIO-pin-to-GIO-pin only, no core
    involvement): reused VERBATIM from v7 -- entirely a property of the
    frame, unaffected by which core is wired in.

Output: layout/step8/v9_top_routed.gds (draft; copy to
src/tr_1um_i2c_slave_async.gds only after DRC/connectivity verification,
per this project's established draft-then-finalize convention).
"""
import json
import sys

import klayout.db as db

sys.path.insert(0, "/sessions/dreamy-ecstatic-heisenberg/mnt/klayout/tech/python")
import pya  # noqa: E402
from cells import tr_1um  # noqa: E402

BASE = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C"
IN_GDS = BASE + "/src/tr_1um_i2c_slave_async.gds"
OUT_GDS = BASE + "/layout/step8/v9_top_routed.gds"
TOP_CELL = "tr_1um_i2c_slave_async"

SIGNAL_PLAN = BASE + "/schematic/v9_signal_routing_plan.json"
POWER_PLAN = BASE + "/schematic/v9_power_routing_plan.json"

M1_LAYER = (13, 0)
M2_LAYER = (20, 0)
M1_WIRE_W = 1.8
M2_WIRE_W = 3.4
PAD = 3.4
LANE_R0 = 847.0
LANE_PITCH = 6.0
NEAR_R = 913.0
R_NOM = 880.0
CUT = 4625.0
PERI = 7400.0
DIS_R = 840.0
VDD_PRIVATE_R = 834.0
VSS_PRIVATE_R = 838.0  # v9: VSS's main(LEFT)/OUT13(RIGHT) legs are long
                        # corner sweeps like v7's VDD main was -- give them
                        # the same dedicated-private-radius treatment,
                        # picked just above VDD_PRIVATE_R=834 (2um clear,
                        # matching that net's own margin rationale) and
                        # still comfortably below DIS_R=840's own footprint
                        # start; DRC will confirm.

# force_dir carried over from v7 precedent (route_gio_core.py's NETS
# dict): rx_data[1] is the one signal whose natural/shortest ring
# direction would sweep through the BOTTOM corner near PTECT -- v7 forced
# CW to route it via TOP instead. v9's rx_data[1] is the same LEFT-edge
# signal (just a different Y), so the same conservative choice is carried
# over; DRC below will confirm whether it's still needed for v9's exact
# geometry (harmless if not -- CW is never wrong, just possibly not the
# shortest).
FORCE_DIR = {"rx_data[1]": "CW"}

# ---- DIS chain: reused verbatim from route_gio_core.py -- GIO-pin-to-
# GIO-pin only, no core-side involvement, unaffected by core version.
DIS_CHAIN_POINTS = [
    ("HIZ1",  (-220.0, 921.7, "TOP", "M2")),
    ("HIZ14", (921.7, 580.0, "RIGHT", "M2")),
    ("HIZ12", (921.7, -220.0, "RIGHT", "M2")),
    ("HIZ11", (921.7, -580.0, "RIGHT", "M2")),
    ("P7",    (-530.0, -921.7, "BOTTOM", "M2")),
    ("HIZ6",  (-921.7, -580.0, "LEFT", "M2")),
    ("HIZ5",  (-921.7, -220.0, "LEFT", "M2")),
    ("HIZ4",  (-921.7, 220.0, "LEFT", "M2")),
    ("HIZ3",  (-921.7, 580.0, "LEFT", "M2")),
]
DIS_LINKS = [(DIS_CHAIN_POINTS[i][0] + "_" + DIS_CHAIN_POINTS[i + 1][0],
              DIS_CHAIN_POINTS[i][1], DIS_CHAIN_POINTS[i + 1][1])
             for i in range(len(DIS_CHAIN_POINTS) - 1)]


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
        d_cw = (s2 - s1) % P
        d_ccw = (s1 - s2) % P
        direction = "CW" if d_cw <= d_ccw else "CCW"
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
    if edge in ("TOP", "BOTTOM"): return (px, R if edge == "TOP" else -R)
    else: return (R if edge == "RIGHT" else -R, py)


def seg_layer(a, b):
    if abs(a[1] - b[1]) < 1e-6 and abs(a[0] - b[0]) >= 1e-6: return "M1"
    if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) >= 1e-6: return "M2"
    raise ValueError(f"non-manhattan or zero-length segment {a} {b}")


def unroll(s): return (s - CUT) % PERI


def main():
    sig_plan = json.load(open(SIGNAL_PLAN))

    # ---- build NETS dict for the 24 signal nets from the plan ----
    NETS = {}
    for name, e in sig_plan["nets"].items():
        c, g = e["core"], e["gio"]
        fd = FORCE_DIR.get(name)
        NETS[name] = (c["x"], c["y"], c["edge"], c["layer"],
                       g["x"], g["y"], g["edge"], g["layer"], fd)

    # ---- power (v4 REWRITE, this session): the entire ring-lane power
    # scheme that used to live here (schematic/v9_power_routing_plan.json,
    # legs to HIZ2/HIZ7/HIZ15/main/HIZ9/HIZ10/OUT13, PTECT-aware TOP-only
    # edges, per-net lane pools, wide_last_w on "main") is SUPERSEDED --
    # direct GDS inspection this session confirmed HIZ*/OUT* are
    # OSS_FRAME_GIO's digital I/O pin names, not power pins at all, so
    # v9_power_routing_plan.json's destinations were simply wrong. See
    # the new bus-bar implementation further down (after the signal-net
    # shared-pool routing) for the replacement, built from the real
    # VDD/VSS/GND pin labels found via a recursive GDS text scan.

    # ---- lane assignment: 24 signal nets in their own shared pool (R
    # starting at LANE_R0=847, same as v7) -- unchanged, still 11 lanes.
    #
    # Power legs get their OWN separate private pool, NOT merged with
    # signals: a first attempt merged all 32 (24+8) into one pool and hit
    # 13 lanes, which at LANE_PITCH=6.0 pushed the top lane's radius to
    # EXACTLY NEAR_R=913.0 -- a degenerate zero-length ring segment
    # (ValueError at runtime). The v3 PTECT-aware power plan (see below)
    # only needs 3 lanes on its own, so it gets a small dedicated band at
    # PWR_LANE_R0=819.0 (2.7um clear of the die edge ~816.3) with the same
    # PWR_LANE_PITCH=6.0, topping out at 831.0 -- 5.3um clear of DIS_R=840's
    # own real footprint (~836.3, per DIS_R's own margin comment above).
    net_interval = {}
    net_dir = {}
    for name, v in NETS.items():
        px, py, ce, cl, gx, gy, ge, gl, fd = v
        s1 = perimeter_s(px, py, ce, R_NOM)
        s2 = perimeter_s(gx, gy, ge, R_NOM)
        d_cw = (s2 - s1) % PERI
        d_ccw = (s1 - s2) % PERI
        direction = fd if fd else ("CW" if d_cw <= d_ccw else "CCW")
        u1, u2 = unroll(s1), unroll(s2)
        net_interval[name] = (min(u1, u2), max(u1, u2))
        net_dir[name] = direction

    MARGIN = 5.0
    lane_last = []
    lane_of = {}
    for name, (lo, hi) in sorted(net_interval.items(), key=lambda kv: kv[1][0]):
        placed = False
        for i, last in enumerate(lane_last):
            if last < lo - MARGIN:
                lane_of[name] = i; lane_last[i] = hi; placed = True; break
        if not placed:
            lane_of[name] = len(lane_last); lane_last.append(hi)
    print(f"signal lanes needed: {len(lane_last)}")

    # (power lane pool removed this session -- see the bus-bar rewrite
    # further down, which replaces the old ring-lane power scheme.)

    # ---- build layout ----
    layout = db.Layout()
    layout.read(IN_GDS)
    dbu = layout.dbu
    top = layout.cell(TOP_CELL)
    m1_idx = layout.layer(*M1_LAYER)
    m2_idx = layout.layer(*M2_LAYER)

    tr_1um("TR-1um")
    via_lib = pya.Library.library_by_name("TR-1um", "*")
    via_decl = via_lib.layout().pcell_declaration("via_1")

    def um(v): return int(round(v / dbu))

    NET_SHAPES = {}
    cur_net = [None]

    def wire(layer_idx, x0, y0, x1, y1, w):
        hw = w / 2.0
        if abs(x0 - x1) < 1e-6:
            box = db.Box(um(x0 - hw), um(min(y0, y1)), um(x0 + hw), um(max(y0, y1)))
        elif abs(y0 - y1) < 1e-6:
            box = db.Box(um(min(x0, x1)), um(y0 - hw), um(max(x0, x1)), um(y0 + hw))
        else:
            raise ValueError(f"non-manhattan segment {x0,y0,x1,y1}")
        top.shapes(layer_idx).insert(box)
        if cur_net[0]:
            L = "M1" if layer_idx == m1_idx else "M2"
            NET_SHAPES.setdefault(cur_net[0], []).append(
                (L, box.left * dbu, box.bottom * dbu, box.right * dbu, box.top * dbu))

    def via(cx, cy, pad=PAD):
        pcell_idx = layout.add_pcell_variant(via_lib, via_decl.id(), {"x": pad, "y": pad, "x0": "c", "y0": "c"})
        top.insert(db.CellInstArray(pcell_idx, db.Trans(db.Vector(um(cx), um(cy)))))
        if cur_net[0]:
            hw = pad / 2.0
            NET_SHAPES.setdefault(cur_net[0], []).append(("VIA", cx - hw, cy - hw, cx + hw, cy + hw))

    idx_map = {"M1": m1_idx, "M2": m2_idx}
    idx_map_w = {"M1": M1_WIRE_W, "M2": M2_WIRE_W}

    def route_shared_pool(name, v):
        cur_net[0] = name
        px, py, ce, cl, gx, gy, ge, gl, fd = v
        R = LANE_R0 + lane_of[name] * LANE_PITCH
        C = project_to_R(px, py, ce, R)
        D = project_to_R(gx, gy, ge, R)
        NEAR = project_to_R(gx, gy, ge, NEAR_R)
        s1 = perimeter_s(px, py, ce, R); s2 = perimeter_s(gx, gy, ge, R)
        corners = ring_waypoints(s1, s2, R, fd)
        ring_path = [(px, py)] + [C] + corners + [D, NEAR]
        ring_seg_layers = [seg_layer(ring_path[k], ring_path[k + 1]) for k in range(len(ring_path) - 1)]

        n_vias = 0
        if cl != ring_seg_layers[0]:
            via(px, py); n_vias += 1
        for k in range(len(ring_path) - 1):
            a, b = ring_path[k], ring_path[k + 1]
            L = ring_seg_layers[k]
            wire(idx_map[L], a[0], a[1], b[0], b[1], idx_map_w[L])
            if k > 0 and ring_seg_layers[k - 1] != L:
                via(a[0], a[1]); n_vias += 1
        if ring_seg_layers[-1] != gl:
            via(NEAR[0], NEAR[1]); n_vias += 1
        wire(idx_map[gl], NEAR[0], NEAR[1], gx, gy, idx_map_w[gl])
        return R, n_vias

    route_log = []
    for name, v in NETS.items():
        R, nv = route_shared_pool(name, v)
        route_log.append((name, R, lane_of[name], nv))
    for name, R, lane, nv in route_log:
        print(f"{name:<12} lane={lane:<3} R={R:6.1f} vias={nv}")

    def connect_core_to_gio_private(name, px, py, ce, cl, gx, gy, ge, gl, R, fd=None,
                                     wide_first_w=None, wide_last_w=None):
        cur_net[0] = name
        C = project_to_R(px, py, ce, R)
        D = project_to_R(gx, gy, ge, R)
        NEAR = project_to_R(gx, gy, ge, NEAR_R)
        s1 = perimeter_s(px, py, ce, R); s2 = perimeter_s(gx, gy, ge, R)
        corners = ring_waypoints(s1, s2, R, fd)
        ring_path = [(px, py), C] + corners + [D, NEAR]
        ring_seg_layers = [seg_layer(ring_path[k], ring_path[k + 1]) for k in range(len(ring_path) - 1)]

        n_vias = 0
        if cl != ring_seg_layers[0]:
            via(px, py); n_vias += 1
        for k in range(len(ring_path) - 1):
            a, b = ring_path[k], ring_path[k + 1]
            L = ring_seg_layers[k]
            w = wide_first_w if (wide_first_w and k == 0) else idx_map_w[L]
            wire(idx_map[L], a[0], a[1], b[0], b[1], w)
            if k > 0 and ring_seg_layers[k - 1] != L:
                via(a[0], a[1]); n_vias += 1
        if ring_seg_layers[-1] != gl:
            via(NEAR[0], NEAR[1]); n_vias += 1
        wire(idx_map[gl], NEAR[0], NEAR[1], gx, gy, wide_last_w if wide_last_w else idx_map_w[gl])
        return n_vias

    # ---- power (v4, this session -- bus-bar rebuild): the ring-lane
    # power scheme above (VDD/VSS to HIZ2/HIZ7/HIZ15/main/HIZ9/HIZ10/
    # OUT13) is now known WRONG -- direct inspection confirmed HIZ/OUT
    # are OSS_FRAME_GIO's digital I/O pin names, unrelated to power.
    # Recursive scan of the real GDS text labels found the genuine
    # GIO-side power pins (VDD: 34 labels, VSS: 47, GND: 28 -- user
    # confirmed VSS/GND are the same net). New architecture per explicit
    # user instruction: two continuous 10um-wide M1 bus bars --
    #   VDD: above the combined core+PTECT bbox (Y=800, clear of the
    #        core's own top edge at 796.1 and far above PTECT's Y range)
    #   VSS: below the combined core+PTECT bbox (Y=-805, clear below
    #        PTECT's bottom edge at -800)
    # -- each tying all 4 TAP columns' M2 power pins via multi-via
    # junctions, then extending to a real GIO VDD/VSS pin confirmed by
    # direct GDS inspection: VDD -> M1 pillar at (230,955) (real label
    # "VDD" at (230,925), real M1 box (210,920)-(250,990)); VSS -> M2
    # bus at (-200,-920) (real label "VSS" at (-200,-925), real M2 box
    # edge at Y=-920, matching the coordinate the user originally gave).
    #
    # TAP1/TAP2's X (~-270/+265) is inside PTECT's X range (-800..800),
    # so a straight vertical drop from their BOTTOM pin to the VSS bus
    # would cut through PTECT (Y -800..-150). Routed around: horizontal
    # M1 at Y=-138.5 (the TAP's own bottom-margin row -- above PTECT's Y
    # range, which tops out at -150) out to X=-812/+812 (just past
    # TAP0/TAP3, outside PTECT's X range), THEN a vertical M2 drop (now
    # safely outside +-800) down to the bus. TAP0/TAP3 need no detour.
    VDD_TAP_X = {"TAP0": -801.9, "TAP1": -267.3, "TAP2": 267.3, "TAP3": 807.3}
    VSS_TAP_X = {"TAP0": -807.3, "TAP1": -272.7, "TAP2": 261.9, "TAP3": 801.9}
    TOP_Y = 794.6
    BOTTOM_Y = -138.5
    VDD_BUS_Y = 800.0
    VSS_BUS_Y = -805.0
    BUS_X_LO, BUS_X_HI = -812.0, 812.0
    BUS_W = 10.0
    # GIO_VDD_PIN/GIO_VSS_PIN, this session's 3rd revision: read directly
    # from the AUTHORITATIVE pin geometry in LEF/OSS_FRAME_GIO.lef (per
    # user's pointer -- "OSS_FRAME_GIOのVDD/VSS実ピン位置はFRAME/
    # TR-1um_frame_25x25.gdsからLEFとして作成してませんか？"), converted
    # from LEF-local coords to chip coords via the LEF's own ORIGIN
    # 1250.000 1250.000 (chip_xy = lef_xy - 1250):
    #   PIN VDD: USE POWER, LAYER M1, RECT (1300,2170)-(1600,2184) ->
    #            chip (50,920)-(350,934) -- ONE location only, TOP edge.
    #   PIN VSS: USE GROUND, LAYER M2, a continuous ring around all 4
    #            edges (many RECTs) -- e.g. the BOTTOM-edge segment
    #            RECT (800,316)-(1300,330) -> chip (-450,-934)-(50,-920),
    #            which is exactly where the user's original VSS point
    #            (-200,-920) already lands.
    # This overturns the previous (wrong) identification: the M2 shape at
    # chip (200,920)-(400,950), which the prior revision connected VDD
    # to on the assumption it was "VDD's own M2 bus", is per this LEF
    # actually VSS's own PIN geometry (VSS RECT (1200,2170)-(1700,2184)
    # -> chip (-50,920)-(450,934) sits almost exactly there) -- i.e. the
    # prior "fix" was directly shorting VDD onto VSS's real pin, exactly
    # as the user's screenshot showed. The M1 "pillar" shapes explored
    # even earlier (210-250,920-990 and 150-190,920-990) are NEITHER
    # net's LEF-defined pin -- probably ESD/support structure -- and are
    # no longer used as connection targets at all.
    GIO_VDD_PIN = (200.0, 927.0)  # M1, centered in the real (50,920)-(350,934) pin rect
    GIO_VSS_PIN = (-200.0, -925.0)  # M2, 5um inside the real (-450,-934)-(50,-920) pin segment (was -920.0, exactly on
    # the pin's own edge -- merely touching a shape's boundary isn't reliably distinguishable from missing it by a
    # hair; moved a few um inside for unambiguous, verifiable overlap)
    PTECT_X_LO, PTECT_X_HI = -800.0, 800.0

    # TAP columns pack a VDD pin and a GND pin only 5.4um apart (same bug
    # class as the earlier "main"-leg short, design_notes.md 79.2b). A
    # direct check of the real GDS around each TAP pin found native power-
    # mesh straps already present there: at the TOP margin they run
    # Y=736.3..796.1; at the BOTTOM margin Y=-140.0..-80.2. A 10um-wide
    # (half-width 5.0) stub centered on one polarity's pin overlaps the
    # neighbor's native strap by ~1.3um wherever their Y ranges coincide
    # -- confirmed directly (TAP0/TAP1 TOP, TAP0 BOTTOM all overlapped
    # before this fix) and NOT caught by DRC width/space checks, since
    # those operate on the flattened layer geometry and don't know two
    # overlapping shapes belong to different nets. Fix: use a safe narrow
    # width (TAP_STUB_W) for the short hop through each danger zone, only
    # widening to the full 10um bus width once past it (VDD_SAFE_Y=797.5,
    # 1.4um above the native strap's top at 796.1; VSS_SAFE_Y=-142.0,
    # 2.0um below the native strap's bottom at -140.0 and still 8um clear
    # of PTECT's -150 edge).
    #
    # TAP_STUB_W REVISED (design_notes.md 79.8, after the user ran REAL
    # KLayout DRC and found 50 violations my own drc_check_nrow_fm.py had
    # completely missed -- separately root-caused to that script checking
    # the wrong cell all session, now fixed too). The original TAP_STUB_W
    # =4.0 assumed the real M2 minimum spacing rule was ~1.4um (an M1
    # figure, wrongly carried over); the real M2 rule is Smin=2.0um AND
    # Wmin=3.0um.
    #
    # The width-STEP itself (narrow near the TAP, widening to BUS_W
    # further out) is ALSO removed here: the real DRC flagged 15 width
    # violations and several of the 19 spacing violations at exactly
    # those step-transition corners (a T-shaped merge of two different-
    # width rectangles reads as a locally narrow "wing" to the real width
    # checker). One constant width (TAP_STUB_W) for the entire TAP-to-bus
    # run, no step geometry at all.
    #
    # TAP_STUB_W REVISED AGAIN (still 79.8, 2nd pass): picking an
    # arbitrary width in [3.0,3.4] (3.2 was tried first) still left 16 M2
    # spacing + 1 M2 width violation, all sitting exactly at the T-junction
    # where the new stub (3.2 wide) meets the core's own PRE-EXISTING
    # native strap -- which turns out to be exactly 3.4um wide, centered
    # exactly on the TAP pin X (measured directly from the pristine,
    # pre-routing src GDS at all 4 TAP columns, both polarities, both
    # margins). Butting a 3.2-wide new shape onto a 3.4-wide existing one
    # leaves two 0.1um notches at the seam -- not a real short or gap, but
    # exactly the kind of tiny stepped-width artifact the width/space
    # checker flags. Fix: match TAP_STUB_W to the native strap's own
    # measured width (3.4) so the new stub is a seamless, flush
    # continuation -- no notch, because there's no step at all.
    # Re: neighbor clearance (the reason a narrower width was chosen in
    # the first place) -- also measured directly: the core's own native
    # VDD/GND straps within one TAP macro are already only 2.0um apart
    # (exactly the Smin minimum) in the PRISTINE, unrouted design, and
    # that isn't in the user's violation list, confirming this deck
    # accepts exactly-at-minimum spacing. TAP_STUB_W=3.4 reproduces that
    # same exact-2.0um gap to the neighbor -- no worse than what the core
    # itself already relies on.
    TAP_STUB_W = 3.4
    VDD_SAFE_Y = 797.5
    # VSS_SAFE_Y REVISED (79.12, user KLayout review -- real short found):
    # the PTECT-detour's horizontal M1 jog (TAP2/TAP1's own local exit,
    # crossing over to the shared safe X) used to run at Y=-142, width
    # BUS_W=10 (so spanning Y=-137..-147). Checked directly against the
    # PRISTINE core GDS: the core's own bottom row has dense internal M1
    # routing (standard-cell signal wiring, including actual PIN labels
    # like "rx_valid" at X=-737.1, Y=-138.5) reaching down to Y=-136.3, a
    # wide continuous shared rail at Y=-136.3..-139.7, right in the middle
    # of the old jog's Y-span -- a real short with a signal net, not a
    # DRC nitpick, confirmed by directly finding "rx_valid"'s own M1 shape
    # overlapping the jog's old footprint. Checked directly: Y=-140..-150
    # is completely empty of core-internal M1/M2 across the full chip
    # width, AND is entirely above PTECT's own top edge (-150) -- so a
    # jog placed there needs no PTECT-X-clearance at all (it's outside
    # PTECT's Y-range) while also being clear of every core-internal net.
    # Moved to -145 (comfortable margin from both the core's edge at
    # -139.7 and PTECT's edge at -150) and narrowed the jog itself from
    # BUS_W to TAP_STUB_W (a connecting jog doesn't need the full 10um
    # bus width, and the narrower footprint leaves more margin either
    # way: -145+/-1.7 = -143.3..-146.7, comfortably inside the empty
    # window).
    VSS_SAFE_Y = -145.0

    # multi_via() REVISED (same 79.8 fix): the previous spacing formula
    # (span=w-pad-1.0, step=span/(n-1)) was never checked against the
    # real DRC rules. The V1.S1 Smin=1.5um violation is measured on the
    # via CUT itself (klayout DRC's "V1" layer), which the via_1 PCell
    # draws as a FIXED 1.4x1.4 shape (per the deck's own "V1.W1:V1(S)
    # Wfix<1.4" rule) centered inside the larger 3.4x3.4 M1/M2 landing
    # pad (PAD) -- confirmed directly from the violation's own reported
    # edge-pair geometry (a 1.4-tall edge, 1.4um gap). The previous
    # formula sized its step from the 3.4um PAD instead of the 1.4um CUT,
    # so at n=3/w=10/pad=3.4 it produced a step of 2.8um -- enough to
    # clear the pads but leaving only a 1.4um CUT-to-cut gap, 0.1um under
    # the real 1.5um minimum. Fixed: pitch sized from the CUT size, not
    # the pad; the resulting pad-to-pad gap goes slightly negative (pads
    # touch/overlap a little), which merges them into one shape -- fine
    # electrically and for M2 width/space rules (nothing to "space" from
    # once merged). n stays at 2 by default (2 real vias, comfortably
    # inside even the narrowest bus width this design uses).
    V1_CUT = 1.4      # via_1 PCell's own fixed cut size
    MIN_VIA_SPACE = 1.5
    def multi_via(cx, cy, w=BUS_W, n=2, pad=PAD):
        if n == 1:
            via(cx, cy, pad=pad); return
        pitch = V1_CUT + MIN_VIA_SPACE + 0.2  # 3.1um: small margin above the real 1.5um cut-to-cut minimum
        x0 = cx - pitch * (n - 1) / 2.0
        for i in range(n):
            via(x0 + i * pitch, cy, pad=pad)

    # multi_via_v() (79.11, user KLayout review): the TAP-to-busbar
    # junctions (TAP's M2 stub meeting the M1 bus) sit on a narrow
    # TAP_STUB_W(3.4)-wide wire in X -- 2 vias can't be placed side by
    # side there (would need 2*pad+space ~= 8.3um of width). But the
    # wire runs long in Y (the stub itself, plus the M1 bus's own 10um
    # height at that Y), so 2 vias stacked ALONG Y (same X, offset in Y)
    # fit easily with real spacing. Same pitch math as multi_via(), just
    # walking Y instead of X.
    def multi_via_v(cx, cy, n=2, pad=PAD):
        if n == 1:
            via(cx, cy, pad=pad); return
        pitch = V1_CUT + MIN_VIA_SPACE + 0.2
        y0 = cy - pitch * (n - 1) / 2.0
        for i in range(n):
            via(cx, y0 + i * pitch, pad=pad)

    def bwire(layer, x0, y0, x1, y1, w=BUS_W):
        wire(idx_map[layer], x0, y0, x1, y1, w)

    pwr_log = []

    # -- VDD bus (above) --
    cur_net[0] = "VDD"
    bwire("M1", BUS_X_LO, VDD_BUS_Y, BUS_X_HI, VDD_BUS_Y)
    # NATIVE_TOP_Y: the core's own pre-existing TOP-margin power strap
    # already runs Y=736.3..796.1 (design_notes.md 79.2b measurement) --
    # i.e. it already covers TOP_Y=794.6 up to 796.1. Starting the new
    # stub AT TOP_Y (794.6, as the first attempt did) redundantly redrew
    # 1.5um of metal the native strap already has, at a very slightly
    # different width (TAP_STUB_W=4.0 vs the native strap's own ~3.4um)
    # -- two near-identical, not-quite-coincident rectangle edges in that
    # overlap band, which is exactly the "M2が2重" doubled outline the
    # user spotted in KLayout. Fixed: start the new stub AT the native
    # strap's own top edge (796.1) instead -- touches it cleanly with no
    # redundant redraw, same electrical connection, single clean outline.
    NATIVE_TOP_Y = 796.1
    # VIA_STACK_MARGIN (79.11): half the Y-span a 2-via vertical stack
    # needs beyond the junction centerline (pitch/2 + pad/2, with a touch
    # of rounding room) so the M2 stub has solid metal under BOTH vias --
    # the stub itself covers well below VDD_BUS_Y already (NATIVE_TOP_Y
    # to VDD_BUS_Y is 3.9um), it just needs to reach a little past
    # VDD_BUS_Y on the far side too. Harmless to extend a few um further
    # into the M1 bus's own footprint (already 10um tall there).
    VIA_STACK_MARGIN = 3.5
    for tap, tx in VDD_TAP_X.items():
        bwire("M2", tx, NATIVE_TOP_Y, tx, VDD_BUS_Y + VIA_STACK_MARGIN, w=TAP_STUB_W)  # constant width, extended past the bus centerline for the via stack
        multi_via_v(tx, VDD_BUS_Y, n=2)  # 2 vias stacked vertically -- doesn't fit side-by-side on a TAP_STUB_W-wide wire
        pwr_log.append((f"VDD_{tap}", tx, TOP_Y))
    # NOTE (v4, this fully supersedes the two prior attempts at this one
    # connection -- design_notes.md 79.4/79.5):
    #  1st attempt: M1 all the way from the bus (Y=800) to the real GIO
    #    pillar (Y=955). Shorted directly into 9 signal nets + the DIS
    #    chain, because Y=847..907 is exactly where the 24 signal nets'
    #    shared-pool ring lanes run their own M1 travel segments.
    #  2nd attempt: switched to M2 the whole way, landing on what was
    #    assumed to be "VDD's own M2 bus" at chip (200-400,920-950).
    #    User's screenshot flagged this as still shorting VDD to VSS.
    #    Root cause, found by finally reading the AUTHORITATIVE pin
    #    geometry in LEF/OSS_FRAME_GIO.lef (per user's pointer) instead
    #    of guessing from nearby text-label proximity: that M2 shape is
    #    NOT VDD's pin at all -- it's VSS's (PIN VSS, LAYER M2, RECT
    #    (1200,2170)-(1700,2184) in LEF-local coords -> chip
    #    (-50,920)-(450,934), which is almost exactly the same box). The
    #    2nd attempt was connecting VDD directly onto VSS's real pin.
    #  This (3rd, current) attempt: the REAL VDD pin (PIN VDD, LAYER M1,
    #    chip (50,920)-(350,934)) is M1, and there is only ONE such
    #    location on the whole frame -- no way to reach it without
    #    crossing Y=847..907 somewhere. Per the user's explicit direction
    #    ("TAPからのM2をM1に落としてからM1(10um)でGIOで接続"): M1 for the
    #    bus (already true) and M1 landing directly in the real pin (now
    #    true) -- but the unavoidable crossing of the signal-lane band is
    #    done as a deliberately NARROW (TAP_STUB_W, not the full 10um)
    #    M2 jog, exactly the same crossing-without-a-via technique already
    #    verified safe for every signal net's own local exit hop, just
    #    narrower here to minimize its footprint through that region.
    #    Above the highest signal lane (907) and below NEAR_R=913 (where
    #    every signal net's own final M2 approach hop lives), the design
    #    returns to M1 for the final stretch and lands squarely inside
    #    the real VDD pin rect -- M1 at both ends, matching the user's
    #    request, M2 only for the brief, narrow, pre-verified-safe crossing.
    gx, gy = GIO_VDD_PIN
    CROSS_Y = 910.0  # clear of the highest signal lane (907, top edge ~907.9) and below NEAR_R=913
    # WIDTH REVERTED TO BUS_W (79.9, user KLayout review): this crossing was
    # narrowed to TAP_STUB_W in 79.5 to "minimize its footprint" through the
    # signal-lane band, but that was never a hard safety requirement -- the
    # M2 crossing is safe from the signal nets purely because it's a
    # different layer than their M1 travel-lane segments (no via = no
    # short), regardless of M2's width. Checked directly against every
    # signal net's own M2 segments (their radial hops) in the corridor
    # X=[190,210] x Y=[800,910]: none exist there, so widening to the full
    # 10um bus width here is safe. User confirmed the VSS side (which was
    # always BUS_W) is correct and asked for the VDD side to match --
    # restored to BUS_W with a proper multi-via (n=2) at both ends instead
    # of the single via that only fit on the narrow version.
    multi_via(gx, VDD_BUS_Y)                          # M1 bus -> M2, full BUS_W on both sides now
    bwire("M2", gx, VDD_BUS_Y, gx, CROSS_Y)            # full 10um crossing of the signal-lane band
    multi_via(gx, CROSS_Y)                             # M2 -> M1, full BUS_W on both sides
    bwire("M1", gx, CROSS_Y, gx, gy)                   # M1 (10um) the rest of the way into the real pin
    pwr_log.append(("VDD_GIO_pin", gx, gy))

    # -- VSS bus (below), TAP1/TAP2/TAP3 detour around PTECT --
    # (clearance must account for the 10um stub's own half-width, not
    # just the pin's centerline -- TAP3 at X=801.9 is only 1.9um outside
    # PTECT's X=800 edge, far less than the 5um half-width, so its
    # straight-down stub would still clip PTECT; confirmed via direct
    # PTECT-overlap check. TAP0 at X=-807.3 has 7.3um clearance, enough.)
    # The detour's horizontal run happens at VSS_SAFE_Y (not BOTTOM_Y
    # directly) precisely so it's already clear of every TAP column's
    # native strap in Y, regardless of which columns its X path crosses.
    # NATIVE_BOTTOM_Y: same fix as VDD's NATIVE_TOP_Y above, mirrored --
    # the core's native BOTTOM-margin strap already covers -140.0..-80.2,
    # i.e. up through and past BOTTOM_Y=-138.5. Start the new stub at the
    # native strap's own edge (-140.0) instead of -138.5 to avoid the
    # same redundant-redraw double-outline artifact.
    NATIVE_BOTTOM_Y = -140.0
    cur_net[0] = "VSS"
    bwire("M1", BUS_X_LO, VSS_BUS_Y, BUS_X_HI, VSS_BUS_Y)

    # Which TAPs clear PTECT directly (no detour needed), split by side --
    # used below so a detouring TAP on a side that ALREADY has a direct
    # TAP reuses that TAP's own X for its vertical drop (perfectly
    # coincident, single bar) instead of defaulting to the die-edge
    # constant BUS_X_LO/BUS_X_HI, which would draw a SECOND, separate
    # vertical M2 bar a few um away -- overlapping the direct TAP's own
    # drop by (10 - |their X difference|)um without actually coinciding
    # with it. Confirmed by the user in KLayout: TAP0 (X=-807.3, direct)
    # and TAP1's old detour target BUS_X_LO=-812.0 produced exactly this,
    # a 5.3um partial overlap of two distinct 10um bars on the LEFT side.
    # The RIGHT side never showed this because BOTH TAP2 and TAP3 need
    # detours there (neither clears directly, per the same test below),
    # so they already shared the one BUS_X_HI target and coincided.
    # CLEARANCE TEST FIX (79.10, user KLayout review): this used to test
    # against BUS_W (10um) half-width, from when a "direct" drop was drawn
    # at the full bus width. Since 79.8 the entire TAP-to-bus run (direct
    # OR detour) is drawn at the narrower constant TAP_STUB_W (3.4) the
    # whole way, so testing against BUS_W was overly conservative -- it
    # forced TAP3 (X=801.9) into an unnecessary detour, even though a
    # TAP_STUB_W-wide drop clears PTECT's edge (800.0) with 0.2um to
    # spare (800.2 vs 800.0). That's what the user was pointing at: on the
    # right side neither TAP looked like it "extended" cleanly to the bus
    # the way TAP0 does on the left, because both were detouring to an
    # unrelated fallback X (813.0) instead of one of them dropping
    # directly. Testing against the real drawn width (TAP_STUB_W) lets
    # TAP3 clear directly, so TAP2's detour now reuses TAP3's exact X
    # (same direct_x_by_side mechanism as the left side's TAP0/TAP1) --
    # same clean single-bar merge pattern on both sides.
    def clears(tx):
        return (tx - TAP_STUB_W / 2 > PTECT_X_HI) or (tx + TAP_STUB_W / 2 < PTECT_X_LO)
    direct_x_by_side = {}
    for tap, tx in VSS_TAP_X.items():
        if clears(tx):
            direct_x_by_side["L" if tx < 0 else "R"] = tx

    # via_done: DRC-driven fix (79.8, 3rd pass). Both TAP2 and TAP3 need a
    # detour on the RIGHT side (neither clears PTECT directly there), so
    # both fell back to the same default detour_x=BUS_X_HI -- and each
    # independently called multi_via() at that shared (x, VSS_SAFE_Y) /
    # (x, VSS_BUS_Y) point, placing TWO separate via arrays almost exactly
    # on top of each other (same intended reasoning as the direct_x_by_side
    # fix for the parallel-bar case: a shared X should mean ONE bar with
    # ONE set of vias, not two independent, near-coincident copies). Real
    # DRC caught this directly: 2 of the V1.S1 violations were a 0.15um
    # gap between a TAP0-direct via and a TAP1-detour via that had both
    # landed at the same X for exactly this reason. Fix: track which
    # (x, y) junctions already got a via and skip placing a second one --
    # the wire itself still merges cleanly (same net, same layer, already
    # coincident by construction), it just doesn't need a duplicate via.
    via_done = set()
    def via_once(cx, cy, **kw):
        key = (round(cx, 3), round(cy, 3))
        if key in via_done:
            return
        via_done.add(key)
        multi_via(cx, cy, **kw)
    def via_once_v(cx, cy, **kw):
        key = (round(cx, 3), round(cy, 3))
        if key in via_done:
            return
        via_done.add(key)
        multi_via_v(cx, cy, **kw)

    # DRC fix (79.8, 5th pass): the right-side detour's fallback target
    # (BUS_X_HI=812.0, used when no direct-clearing TAP exists on that
    # side to reuse -- true for both TAP2 and TAP3 here) puts a single
    # via's own landing pad (3.4 wide, so its left edge lands at
    # 812.0-1.7=810.3) only 1.3um from VDD's own pre-existing native
    # strap at TAP3 (right edge at X=809.0) -- under the real 2.0um M2
    # Smin. Bumped the fallback specifically for the detour via/wire
    # (not the M1 bus bar's own span, which is unaffected -- M1 Smin is
    # much looser) so the via pad clears with margin: 813.0-1.7=811.3,
    # 2.3um from 809.0.
    VSS_DETOUR_FALLBACK_HI = 813.0
    for tap, tx in VSS_TAP_X.items():
        clears_direct = clears(tx)
        if not clears_direct:
            side = "L" if tx < 0 else "R"
            detour_x = direct_x_by_side.get(side, BUS_X_LO if tx < 0 else VSS_DETOUR_FALLBACK_HI)
            bwire("M2", tx, NATIVE_BOTTOM_Y, tx, VSS_SAFE_Y, w=TAP_STUB_W)  # narrow, and now entirely above PTECT's Y range too (79.12)
            via_once(tx, VSS_SAFE_Y, n=1)                          # M2(narrow) -> M1(horizontal), still near TAP
            bwire("M1", tx, VSS_SAFE_Y, detour_x, VSS_SAFE_Y, w=TAP_STUB_W)  # 79.12: narrowed from BUS_W, and moved to a Y with zero core-internal metal (see VSS_SAFE_Y comment)
            via_once(detour_x, VSS_SAFE_Y, n=1)                    # far end -- narrow M2 below, see next line
            # DRC fix (79.8, 4th pass): this vertical run used to widen to
            # BUS_W here. When detour_x reuses a direct-drop TAP's own X
            # (the direct_x_by_side case), that TAP's own stub is ALREADY
            # running TAP_STUB_W-wide through this exact X for this exact
            # Y range (it continues in one constant-width run all the way
            # to VSS_BUS_Y) -- widening the detour's copy to BUS_W here
            # merged into it and reproduced the same step-transition notch
            # (real DRC: M2 space violations right at Y=NATIVE_BOTTOM_Y,
            # -805.6..-802.1 and 806.8..811.0, exactly the widened detour
            # bumping into the TAP's own narrow stub). Kept at TAP_STUB_W
            # throughout -- already satisfies Wmin=3.0 on its own, and the
            # real 10um bus bar is the horizontal M1 run at VSS_BUS_Y,
            # unaffected by this.
            bwire("M2", detour_x, VSS_SAFE_Y, detour_x, VSS_BUS_Y - VIA_STACK_MARGIN, w=TAP_STUB_W)  # extended past the bus for the via stack (79.11)
            via_once_v(detour_x, VSS_BUS_Y, n=2)  # 2 vias stacked vertically, same reasoning as the VDD side
        else:
            bwire("M2", tx, NATIVE_BOTTOM_Y, tx, VSS_BUS_Y - VIA_STACK_MARGIN, w=TAP_STUB_W)  # constant width, extended past the bus for the via stack
            via_once_v(tx, VSS_BUS_Y, n=2)  # 2 vias stacked vertically -- doesn't fit side-by-side on TAP_STUB_W
        pwr_log.append((f"VSS_{tap}", tx, BOTTOM_Y))
    gx, gy = GIO_VSS_PIN
    bwire("M2", gx, VSS_BUS_Y, gx, gy)
    multi_via(gx, VSS_BUS_Y)  # M2 stub -> M1 bus junction -- was missing (an M1 box and M2
    # box merely touching at the same point isn't an electrical connection without a via).
    pwr_log.append(("VSS_GIO_pin", gx, gy))

    for name, x, y in pwr_log:
        print(f"{name:<14} at ({x:7.1f},{y:7.1f})")

    # ---- DIS chain (verbatim from v7) ----
    def connect_gio_to_gio(name, A, B, R):
        Ax, Ay, Ae, Al = A
        Bx, By, Be, Bl = B
        cur_net[0] = name
        NEAR_A = project_to_R(Ax, Ay, Ae, NEAR_R)
        NEAR_B = project_to_R(Bx, By, Be, NEAR_R)
        C = project_to_R(Ax, Ay, Ae, R)
        D = project_to_R(Bx, By, Be, R)
        s1 = perimeter_s(Ax, Ay, Ae, R); s2 = perimeter_s(Bx, By, Be, R)
        corners = ring_waypoints(s1, s2, R, None)
        ring_path = [NEAR_A, C] + corners + [D, NEAR_B]
        ring_seg_layers = [seg_layer(ring_path[k], ring_path[k + 1]) for k in range(len(ring_path) - 1)]

        n_vias = 0
        wire(idx_map[Al], Ax, Ay, NEAR_A[0], NEAR_A[1], idx_map_w[Al])
        if Al != ring_seg_layers[0]:
            via(NEAR_A[0], NEAR_A[1]); n_vias += 1
        for k in range(len(ring_path) - 1):
            a, b = ring_path[k], ring_path[k + 1]
            L = ring_seg_layers[k]
            wire(idx_map[L], a[0], a[1], b[0], b[1], idx_map_w[L])
            if k > 0 and ring_seg_layers[k - 1] != L:
                via(a[0], a[1]); n_vias += 1
        if ring_seg_layers[-1] != Bl:
            via(NEAR_B[0], NEAR_B[1]); n_vias += 1
        wire(idx_map[Bl], NEAR_B[0], NEAR_B[1], Bx, By, idx_map_w[Bl])
        return n_vias

    for name, A, B in DIS_LINKS:
        nv = connect_gio_to_gio(name, A, B, DIS_R)
        print(f"{name:<14} R={DIS_R:6.1f} vias={nv}")

    with open(OUT_GDS.replace(".gds", "_net_shapes.json"), "w") as f:
        json.dump(NET_SHAPES, f)

    layout.write(OUT_GDS)
    print("\nwrote", OUT_GDS)


if __name__ == "__main__":
    main()
