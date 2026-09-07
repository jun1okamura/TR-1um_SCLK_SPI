"""
route_top_pins_nrow_fm.py (section 47 originally, v6; STEP6 this session
for the v7/nrow_fm TRACK_PITCH=5.4 recipe -- user request)

Routes each top-level port of i2c_slave_async out to the block BBOX on
the current v7 recipe's final routed+ripup/reroute-fixed layout
(Layout/i2c_slave_async_nrow_fm_v7rr_routed.gds -- 0 shorts, DRC 0/0/0
as of design_notes section 54), and drops a 3x3um M1PIN/M2PIN marker
AT the BBOX edge, ready for the frame-connection step.

Per user instruction (this session, STEP6):
  - row0 ports: M2, straight down to the BBOX bottom edge (Y=0).
  - row3 ports: M2, straight up to the BBOX top edge (Y=core_h).
  - row1 ports (that have NO row0/row3 pin at all): M2 up into channel2
    (the channel between row1 and row2) -> via_1 -> M1 toward LARGER X,
    to the BBOX right edge (X=ROW_WIDTH_UM).
  - row2 ports (that have NO row0/row3 pin at all): M2 down into
    channel2 -> via_1 -> M1 toward SMALLER X, to the BBOX left edge
    (X=0).
  - Where the same port has pins in both row0 and row3, only row0's is
    wired (row0 takes priority); where a port has multiple pins in the
    same row, only the smallest-X one is wired. A port with ANY row0/
    row3 pin is wired via the M2/vertical path ONLY, even if it also
    happens to have row1/row2 pins (e.g. addr_ok: row0 AND row2 -- row0
    wins, no separate row2/M1 exit is drawn for it) -- "row1/2-only"
    means genuinely no row0/row3 presence at all.

Track/via/wire conventions are the same ones route_channels_nrow_fm.py
uses throughout (TRACK_PITCH grid inside a channel, via_1 PCell via
M1_PAD_SIZE pads, M1_TRUNK_WIDTH-wide M1, PAD_HALF-wide M2 stubs) so
this output is DRC-comparable with the rest of the design. Collision
checking is a straightforward geometric Region query against whatever
is ALREADY drawn (this script's own new wires are added one at a time,
so later wires also see earlier ones in the same run) -- not the
router's full net-aware bookkeeping, since this is a one-off exit-wiring
pass over pins that were previously dead ends (or already-routed nets
tapped at their own pin, not their far-away trunk), not a re-route.
"""
import json
import sys

import klayout.db as db

TECH_PY_DIR = "/sessions/dreamy-ecstatic-heisenberg/mnt/klayout/tech/python"
sys.path.insert(0, "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/script")
sys.path.insert(0, TECH_PY_DIR)
import pya  # noqa: E402
from cells import tr_1um  # noqa: E402
from highlight_top_pins_nrow_fm import (  # noqa: E402
    port_net_name, bus_bit_net_name, build_net_pins, SCALAR_PORTS, BUS_PORTS)

TOP_CELL_NAME = "i2c_slave_async_nrow_fm"
M1_LAYER = (13, 0)
M2_LAYER = (20, 0)
M1PIN_LAYER = (48, 1)
M2PIN_LAYER = (49, 1)
TXM1_LAYER = (48, 0)
TXM2_LAYER = (49, 0)

M1_TRUNK_WIDTH = 1.8
M1_PAD_SIZE = 3.4
PAD_HALF = M1_PAD_SIZE / 2.0
TRACK_PITCH = 5.4  # this session: was 4.0 (v6) -- must match the v7
                    # recipe's TRACK_PITCH (design_notes 47/48)
TRACK0_OFFSET = 2.0
M1_MIN_GAP = 1.4   # matches drc_check_nrow_fm.py's M1 space rule
M2_MIN_GAP = 2.0    # matches drc_check_nrow_fm.py's M2 space rule

EXTEND_UM = 0.0  # this session (user instruction, STEP6): pin marker
                  # sits exactly AT the BBOX edge -- was 10.0 (v6, 10um
                  # past the edge, for an eventual frame connection)
PIN_SIZE_UM = 3.0

PLACEMENT_JSON = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/placement_nrow_fm_v7_priomch.json"
IN_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v7rr_routed.gds"
OUT_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/steps_v7_v2/v7v2_step_6_top_pins_routed.gds"
CH_HEIGHTS = [131.60000000000002, 461.00000000000006, 417.8, 390.8, 153.20000000000002]  # v7 recipe, snapped (design_notes 54)
PIN_MAP_JSON = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/script/pin_map_nrow_fm_v7rr.json"
NET_SHAPES_JSON = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/script/net_shapes_nrow_fm_v7rr.json"


# top-level RTL port direction (as seen from the FRAME) -- NOT the same
# as the internal standard-cell pin's own direction (e.g. rw's driver is
# a DFF's Q OUTPUT pin, or an AND gate's Y OUTPUT pin, but the port
# itself is a top-level OUTPUT either way; sda_in's several fanout pins
# are all cell INPUT pins, matching the port's own declared INPUT).
PORT_DIR = {
    "rst_n": "INPUT", "scl": "INPUT", "sda_in": "INPUT", "sda_oe": "OUTPUT",
    "rx_valid": "OUTPUT", "addr_match": "OUTPUT", "rw": "OUTPUT", "busy": "OUTPUT",
}
for _i in range(8):
    PORT_DIR[f"tx_data[{_i}]"] = "INPUT"
    PORT_DIR[f"rx_data[{_i}]"] = "OUTPUT"


def port_to_net_name(port, resolver=None):
    """v40: the actual routable net name behind a top-level port string
    (e.g. "tx_data[7]" -> "tx_data[7]" itself is fine since BUS_PORT_NET_ALIAS
    has no tx_data entry, but "rw" -> "rw_bit", "rx_data[3]" -> "rx_data_r[3]")
    -- needed to look up net_shapes_log/pin_map, which are keyed by net,
    not by port.

    108.42 (V10): `resolver`, when given, is threaded through to
    port_net_name/bus_bit_net_name so this also picks up assign-chain
    aliases (e.g. V10's sda_oe -> _187_) beyond the static
    PORT_NET_ALIAS/BUS_PORT_NET_ALIAS dicts -- see
    highlight_top_pins_nrow_fm.py's 108.42 note."""
    if "[" in port:
        bus, idx = port[:-1].split("[")
        return bus_bit_net_name(bus, int(idx), resolver)
    return port_net_name(port, resolver)


def dedup_min_x(items):
    """items: [(port, direction, inst, pname, cx, cy), ...] -> one entry
    per port, the smallest-cx one (section 46's final list rule)."""
    best = {}
    for port, direc, inst, pname, cx, cy in items:
        if port not in best or cx < best[port][3]:
            best[port] = (direc, inst, pname, cx, cy)
    return sorted([(port,) + v for port, v in best.items()], key=lambda t: t[4])


def gather_pins(placement, ch_heights, row_h, resolver=None):
    net_pins, core_h = build_net_pins(placement, ch_heights, row_h)
    net_to_port = {}
    for port in SCALAR_PORTS:
        net_to_port[port_net_name(port, resolver)] = port
    for bus, w in BUS_PORTS.items():
        for i in range(w):
            net_to_port[bus_bit_net_name(bus, i, resolver)] = f"{bus}[{i}]"

    n_rows = len(placement["rows"])
    row_y0 = []
    y = 0.0
    for i in range(n_rows):
        y += ch_heights[i]
        row_y0.append(y)
        y += row_h

    by_row = {r: [] for r in range(n_rows)}
    for r, row_insts in enumerate(placement["rows"]):
        yoff = row_y0[r]
        for inst in row_insts:
            for pname, pinfo in inst["pins"].items():
                if pinfo["use"] in ("POWER", "GROUND"):
                    continue
                net = pinfo["net"]
                if net not in net_to_port:
                    continue
                port = net_to_port[net]
                for layer, x0, y0, x1, y1 in pinfo["rects"]:
                    if layer != "M2":
                        continue
                    cx = (x0 + x1) / 2.0
                    cy = (y0 + y1) / 2.0 + yoff
                    by_row[r].append((port, PORT_DIR[port], inst["name"], pname, cx, cy))

    # v37 (design_notes, this session, user instruction: "ROW1/2だけに
    # あるピンは..." -- pins that exist ONLY in row1/row2): the v6
    # version only excluded row0 ports from row3_list, but never
    # excluded a port from row1_list/row2_list just because it ALSO had
    # a row0/row3 pin -- e.g. addr_ok (row0 AND row2) would have gotten
    # BOTH a row0 M2-down exit AND a separate row2 M1-left exit. Per
    # this session's more precise instruction, a port with ANY row0/
    # row3 pin is wired via the M2/vertical path ONLY -- row1_list/
    # row2_list must exclude every port that's ALSO in row0_ports or
    # row3_ports_all, not just the row0/row3 duplication between
    # themselves.
    row0_ports = {item[0] for item in by_row[0]}
    row3_ports_all = {item[0] for item in by_row[3]}
    vertical_ports = row0_ports | row3_ports_all
    row0_list = dedup_min_x(by_row[0])
    row3_list = dedup_min_x([item for item in by_row[3] if item[0] not in row0_ports])
    row1_list = dedup_min_x([item for item in by_row[1] if item[0] not in vertical_ports])
    row2_list = dedup_min_x([item for item in by_row[2] if item[0] not in vertical_ports])
    return row0_list, row1_list, row2_list, row3_list, row_y0, core_h


def main(placement_json=PLACEMENT_JSON, in_gds=IN_GDS, out_gds=OUT_GDS, ch_heights=CH_HEIGHTS,
         net_shapes_json=NET_SHAPES_JSON, net_file=None):
    resolver = None
    if net_file:
        from netlist_parser import _build_alias_resolver
        resolver = _build_alias_resolver(open(net_file).read())

    placement = json.load(open(placement_json))
    row_h = placement["row_height"]
    row_width = placement["row_width"]
    row0_list, row1_list, row2_list, row3_list, row_y0, core_h = gather_pins(
        placement, ch_heights, row_h, resolver)

    # v40 (design_notes, this session, user request "STEP6" -- third fix):
    # rw_bit/rx_valid/addr_ok/busy/rst_n/tx_data[7]/rx_data_r[*] are all
    # ALREADY routed nets (>=2 pins, per route_channels_nrow_fm.py) whose
    # own pre-existing M2 stub runs from their row0/row3/row1/row2 pin
    # down/up toward the SAME channel/margin this script now searches --
    # e.g. rw_bit's own stub already reaches (35.1, 12.8), deep inside
    # channel0. Since the collision checks below have no net-awareness
    # (raw region overlap against the whole M2/M1 layer), that pin's own
    # pre-existing metal read as a blocking "collision" against itself,
    # forcing the search to hunt for an alternate X -- confirmed via GDS
    # audit as the reason EVERY row0/row3/row1/row2 port failed to find
    # a clear path at its own natural X even in the comparatively open
    # channel/margin territory (v39 wasn't enough on its own). Loading
    # net_shapes (the exact same per-net-tagged M1/M2 log
    # route_channels_nrow_fm.py itself writes, design_notes 38.x) and
    # subtracting a net's OWN shapes from its own collision probes --
    # same fix pattern as that router's channel_clear v18 -- lets these
    # checks correctly ignore a net's pre-existing metal while still
    # catching a real collision against any OTHER net.
    try:
        net_shapes_log = json.load(open(net_shapes_json))
    except FileNotFoundError:
        net_shapes_log = {}

    layout = db.Layout()
    layout.read(in_gds)
    dbu = layout.dbu
    top = layout.cell(TOP_CELL_NAME)
    m1_idx = layout.layer(*M1_LAYER)
    m2_idx = layout.layer(*M2_LAYER)
    m1pin_idx = layout.layer(*M1PIN_LAYER)
    m2pin_idx = layout.layer(*M2PIN_LAYER)
    txm1_idx = layout.layer(*TXM1_LAYER)
    txm2_idx = layout.layer(*TXM2_LAYER)

    tr_1um("TR-1um")
    via_lib = pya.Library.library_by_name("TR-1um", "*")
    via_decl = via_lib.layout().pcell_declaration("via_1")

    def um(v):
        return int(round(v / dbu))

    def place_via(cx, cy):
        pcell_idx = layout.add_pcell_variant(
            via_lib, via_decl.id(), {"x": M1_PAD_SIZE, "y": M1_PAD_SIZE, "x0": "c", "y0": "c"})
        top.insert(db.CellInstArray(pcell_idx, db.Trans(db.Vector(um(cx), um(cy)))))

    def m1_box(x0, y0, x1, y1):
        top.shapes(m1_idx).insert(db.Box(um(x0), um(y0), um(x1), um(y1)))

    def m2_box(x0, y0, x1, y1):
        top.shapes(m2_idx).insert(db.Box(um(x0), um(y0), um(x1), um(y1)))

    own_region_cache = {}

    def own_region(net, layer):
        """v40: net's own already-drawn shapes on `layer` ("M1"/"M2"),
        from net_shapes_log -- subtracted from collision probes below so
        a net's own pre-existing metal is never mistaken for a foreign
        obstruction (route_channels_nrow_fm.py's channel_clear v18)."""
        key = (net, layer)
        if key not in own_region_cache:
            boxes = [db.Box(um(x0), um(y0), um(x1), um(y1))
                     for lyr, x0, y0, x1, y1 in net_shapes_log.get(net, ()) if lyr == layer]
            own_region_cache[key] = db.Region(boxes)
        return own_region_cache[key]

    def _exclude_own(hits, own):
        """v40b: net_shapes_log only records this script's/the router's own
        drawn wire stubs, NOT the originating standard-cell's physical pin
        shape -- confirmed via direct geometry query: rw_bit's stub
        (33.4,12.8)-(36.8,159.6) sits with its bottom edge exactly touching
        an untracked pin box (33.4,11.1)-(36.8,12.8), so a plain Region
        subtract left that pin box behind as a false-positive self-collision.
        Flood-merge `own` outward (grown by a tiny epsilon so touching,
        zero-gap shapes count as connected) against whatever is actually in
        `hits`, a few rounds, before subtracting -- picks up the whole
        physically-contiguous same-net blob local to this probe."""
        cur = own
        for _ in range(5):
            grown = cur.sized(10)  # 0.01um -- far below M1/M2_MIN_GAP, only
                                    # bridges true touching/overlapping shapes
            touching = hits.interacting(grown)
            merged = cur + touching
            merged.merge()
            if merged.count() == cur.count() and abs(merged.area() - cur.area()) < 1:
                break
            cur = merged
        return hits - cur

    def m1_clear(x0, y0, x1, y1, gap=M1_MIN_GAP, exclude_net=None):
        probe = db.Box(um(x0 - gap), um(y0), um(x1 + gap), um(y1))
        hits = db.Region(top.begin_shapes_rec_overlapping(m1_idx, probe))
        if exclude_net is not None:
            own = own_region(exclude_net, "M1")
            if not own.is_empty():
                hits = _exclude_own(hits, own)
        return hits.is_empty()

    def m2_clear(x0, y0, x1, y1, gap=M2_MIN_GAP, exclude_net=None, extra_own=None):
        # v49 (design_notes 69/70): `extra_own`, if given, is a Region
        # representing the CURRENT pin's own physical source-pin footprint
        # (as placed by gen_placement_nrow_fm.py, NOT something this
        # router itself has drawn). own_region()/net_shapes_log only
        # records wires THIS SCRIPT/route_channels_nrow_fm.py drew for a
        # net -- for a "stub" net (<2 internal standard-cell pins, e.g.
        # tx_data[0], never registered in net_shapes_log/pin_map at all,
        # design_notes 69.2(A)), own_region() is always empty, so a probe
        # that starts only SELF_MARGIN=3.0um away from the pin (minus the
        # M2_MIN_GAP=2.0um the probe itself grows by) can reach back
        # ~1.0um into the pin's own PAD_HALF=1.7um-radius pad and report a
        # false self-collision on every single candidate track --
        # regardless of what real, external routing is actually nearby.
        # Root-caused via direct GDS query (design_notes 70): this is
        # exactly what made every one of channel2's 37 tracks look
        # blocked for tx_data[0] even below Y=514 (the real, external
        # `_074_` obstruction only starts at Y=514).
        probe = db.Box(um(x0 - gap), um(y0 - gap), um(x1 + gap), um(y1 + gap))
        hits = db.Region(top.begin_shapes_rec_overlapping(m2_idx, probe))
        own = db.Region()
        if exclude_net is not None:
            r = own_region(exclude_net, "M2")
            if not r.is_empty():
                own = own + r
        if extra_own is not None and not extra_own.is_empty():
            own = own + extra_own
        if not own.is_empty():
            hits = _exclude_own(hits, own)
        return hits.is_empty()

    def find_clear_x_vertical(x_start, y_lo, y_hi, half, dogleg_y=None,
                               exclude_net=None, max_shift=200.0, step=1.0,
                               extra_own=None):
        """Search for an X (starting at x_start, then +-step, +-2*step, ...)
        where a half-width-wide M2 column from y_lo to y_hi is fully clear
        of existing M2 (own DRC margin baked in). Returns the found X, or
        None if nothing within +-max_shift works.

        v38 (design_notes, this session, user request "STEP6"): if a
        candidate X != x_start is picked, the caller draws a WIDE
        horizontal "dogleg" bar connecting x_start to that X, at
        `dogleg_y` (the pin's own Y +- half) -- see the row0/row3 loops
        below. That Y band sits right inside row0/row3's densely packed
        standard-cell area, where MANY unrelated nets' own M2 stubs
        terminate only 2.0um (M2_MIN_GAP) apart. The ORIGINAL version of
        this search validated only the vertical column at the candidate
        X, never the horizontal dogleg itself -- confirmed via GDS diff
        as the exact mechanism of a 14-net short chain found this
        session (addr_ok's dogleg, drawn from its natural x=450.9 to a
        found clear x=477.9, swept straight through several unrelated
        row0 nets' stub gaps and bridged them all into one shorted
        blob). `dogleg_y`, if given as (y_lo, y_hi) for that bar, makes
        the candidate search ALSO require the full dogleg span (from
        x_start to the candidate, at that Y band) to be clear before
        accepting it -- so a landlocked dogleg is never drawn in the
        first place, mirroring the same "validate the whole path before
        committing" fix applied throughout route_channels_nrow_fm.py
        this session (e.g. v30's lookahead)."""
        def ok(x):
            if not m2_clear(x - half, y_lo, x + half, y_hi, exclude_net=exclude_net, extra_own=extra_own):
                return False
            if dogleg_y is not None and abs(x - x_start) > 1e-6:
                dog_lo, dog_hi = dogleg_y
                dx0, dx1 = min(x, x_start) - half, max(x, x_start) + half
                if not m2_clear(dx0, dog_lo, dx1, dog_hi, exclude_net=exclude_net, extra_own=extra_own):
                    return False
            return True
        if ok(x_start):
            return x_start
        n = int(max_shift / step)
        for s in range(1, n + 1):
            for cand in (x_start + s * step, x_start - s * step):
                if ok(cand):
                    return cand
        return None

    def add_m2_pin(cx, cy, label, edge):
        half = PIN_SIZE_UM / 2.0
        top.shapes(m2pin_idx).insert(db.Box(um(cx - half), um(cy - half), um(cx + half), um(cy + half)))
        # v48 (this session, KLayout LVS): the text used to sit half+1.0um
        # OUTSIDE the pin box on the "right" edge case -- fine cosmetically
        # in a viewer, but GDS-text-based pin recognition (KLayout's LVS
        # extractor) requires the label to fall INSIDE (or touching) the
        # shape it names. With pins on a 5.4um pitch (closely stacked on
        # the same edge, e.g. the rx_data[] run), an outside-the-box label
        # can land in the gap between two DIFFERENT pins' boxes -- or
        # entirely miss its own -- so the extractor can't resolve a name
        # for that pin at all (falls back to an anonymous net). Put the
        # label dead center of its own box so it can never miss.
        top.shapes(txm2_idx).insert(db.Text(label, db.Trans(um(cx), um(cy))))

    def add_m1_pin(cx, cy, label):
        half = PIN_SIZE_UM / 2.0
        top.shapes(m1pin_idx).insert(db.Box(um(cx - half), um(cy - half), um(cx + half), um(cy + half)))
        # v48: same fix as add_m2_pin above -- was cy+half+1.0 (always
        # outside/above the box), now dead center so it always lies
        # inside the pin shape it labels.
        top.shapes(txm1_idx).insert(db.Text(label, db.Trans(um(cx), um(cy))))

    def _collision_count(x0, y_track, x1, half1, gap=M1_MIN_GAP, exclude_net=None):
        y0, y1 = y_track - half1, y_track + half1
        probe = db.Box(um(min(x0, x1) - gap), um(y0), um(max(x0, x1) + gap), um(y1))
        hits = db.Region(top.begin_shapes_rec_overlapping(m1_idx, probe))
        if exclude_net is not None:
            own = own_region(exclude_net, "M1")
            if not own.is_empty():
                hits = _exclude_own(hits, own)
        return hits.count()

    report = []
    SELF_MARGIN = 3.0  # excludes the pin's own already-drawn rect from the
                        # row1/row2 M2-into-channel2 clear-path search below
                        # (that path stays at a FIXED X the whole way, so
                        # it never needs the row0/row3 leg-split fix -- v39)

    # v39 (design_notes, this session, user request "STEP6" -- second
    # fix, after v38): even with the dogleg itself checked, a search that
    # STARTS from inside row0/row3's own densely-packed interior (many
    # other nets' pin stubs only M2_MIN_GAP=2.0um apart, everywhere) has
    # to sweep a WIDE horizontal dogleg just to find one clear column,
    # and at THIS design's density that search can fail outright across
    # the whole +-200um window. Splitting into two legs -- (1) fixed-X,
    # pin -> row boundary, UNCHECKED (the same assumption every other
    # pin-to-trunk connection in route_channels_nrow_fm.py already
    # relies on for this exact "escape the row" segment -- see e.g. pass
    # 0's per-row-local stub leg 1), then (2) row boundary -> BBOX edge,
    # entirely inside channel0/channel4 (the bottom/top margins) --
    # confines the live-checked search + any dogleg to the comparatively
    # open margin territory instead of the row interior, mirroring the
    # split that already works for PER_ROW_LOCAL_NETS.
    row0_bound = row_y0[0]              # row0's own bottom edge = channel0's top edge
    row3_bound = row_y0[3] + row_h       # row3's own top edge = channel4's bottom edge

    # ---- row0: M2 straight down, 10um past Y=0 ----
    for port, direc, inst, pname, cx, cy in row0_list:
        net = port_to_net_name(port, resolver)
        half = PAD_HALF
        y_end = -EXTEND_UM
        if cy - half > row0_bound:
            m2_box(cx - half, row0_bound, cx + half, cy + half)  # leg 1 (unchecked)
        # 108.50 (V10, this session): same self-collision bug the v49/v50
        # fix solved for route_row1_row2 (design_notes 69/70) -- for a
        # true 1-pin "stub" net (e.g. tx_data[i]), own_region() is always
        # empty (net_shapes_log only tracks nets route_channels_nrow_fm.py
        # itself routed), so exclude_net alone can't stop the pin's OWN
        # physical pad -- and, when it's close enough to row0_bound to
        # straddle it, leg 1's own just-drawn bar -- from reading as a
        # "collision" against every single candidate X, including x_start
        # itself, in BOTH the vertical-column check and (since one dogleg
        # endpoint is always cx) EVERY dogleg. Root-caused via a direct
        # GDS query on the V10 squeezed layout (LVS: sda_oe's row2
        # equivalent of this class of bug shorted it onto the clock trunk
        # via the "least-bad" track fallback; row0/row3 has no such
        # fallback -- find_clear_x_vertical simply returns None forever
        # and the caller draws straight through at cx regardless,
        # silently leaving whatever real short/DRC risk was actually
        # there, if any, undetected by pin_map-based verify_connectivity
        # since stub nets are excluded from pin_map too). Fix: build the
        # same kind of self_own region row1/row2 already uses (own pad +
        # leg 1's own extent, since leg 1 itself is also untracked new
        # geometry) and thread it through as extra_own.
        self_own = db.Region(db.Box(um(cx - half - 0.2), um(min(cy, row0_bound) - half - 0.2),
                                     um(cx + half + 0.2), um(max(cy, row0_bound) + half + 0.2)))
        found_x = find_clear_x_vertical(cx, y_end, row0_bound, half,
                                         dogleg_y=(row0_bound - half, row0_bound + half),
                                         exclude_net=net, extra_own=self_own)
        ok = found_x is not None
        fx = found_x if ok else cx
        if fx != cx:
            m2_box(min(fx, cx) - half, row0_bound - half, max(fx, cx) + half, row0_bound + half)
        m2_box(fx - half, y_end, fx + half, row0_bound + half)
        add_m2_pin(fx, y_end + PIN_SIZE_UM / 2.0, port, "bottom")
        report.append((port, direc, "row0", "M2 down", cx, cy, fx, y_end, ok))

    # ---- row3: M2 straight up, 10um past Y=core_h ----
    for port, direc, inst, pname, cx, cy in row3_list:
        net = port_to_net_name(port, resolver)
        half = PAD_HALF
        y_end = core_h + EXTEND_UM
        if cy + half < row3_bound:
            m2_box(cx - half, cy - half, cx + half, row3_bound)  # leg 1 (unchecked)
        # 108.50: same fix as row0 above -- see that loop's comment.
        self_own = db.Region(db.Box(um(cx - half - 0.2), um(min(cy, row3_bound) - half - 0.2),
                                     um(cx + half + 0.2), um(max(cy, row3_bound) + half + 0.2)))
        found_x = find_clear_x_vertical(cx, row3_bound, y_end, half,
                                         dogleg_y=(row3_bound - half, row3_bound + half),
                                         exclude_net=net, extra_own=self_own)
        ok = found_x is not None
        fx = found_x if ok else cx
        if fx != cx:
            m2_box(min(fx, cx) - half, row3_bound - half, max(fx, cx) + half, row3_bound + half)
        m2_box(fx - half, row3_bound - half, fx + half, y_end)
        add_m2_pin(fx, y_end - PIN_SIZE_UM / 2.0, port, "top")
        report.append((port, direc, "row3", "M2 up", cx, cy, fx, y_end, ok))

    # ---- row1: M2 up into channel2, via_1, M1 right, 10um past X=row_width ----
    ch2_lo, ch2_hi = row_y0[1] + row_h, row_y0[2]  # channel2 band (between row1 and row2)
    n_ch2_tracks = int((ch2_hi - ch2_lo - 2 * TRACK0_OFFSET) // TRACK_PITCH) + 1

    def ch2_track_y(idx):
        return ch2_lo + TRACK0_OFFSET + idx * TRACK_PITCH

    used_ch2_idx = set()

    def claim_ch2_track(from_bottom, exclude=()):
        order = range(n_ch2_tracks) if from_bottom else range(n_ch2_tracks - 1, -1, -1)
        for idx in order:
            if idx not in used_ch2_idx and idx not in exclude:
                return idx
        return None

    x_right_end = row_width + EXTEND_UM
    half2 = PAD_HALF
    half1 = M1_TRUNK_WIDTH / 2.0

    # v49 (design_notes 69/70, tx_data[0]<->_074_ short fix): if NO track
    # is fully clear at the pin's own natural X, try a small lateral M2
    # jog (mirrors row0/row3's find_clear_x_vertical dogleg, leg-split +
    # validated dogleg span) before falling back to the guaranteed-
    # nonzero-collision "least collision" choice. Root cause (design_notes
    # 69): a narrow (one placement-grid column wide), unrelated net's M2
    # stub can land at the EXACT same X as this pin and block the
    # vertical rise on every track above it, even though the M1 run
    # itself (checked at the pin's ORIGINAL x, since a few-um shift in
    # the rise's X barely changes an M1 run's clearance over the whole
    # channel width) is fully free on many tracks just past that one
    # obstruction. This jog only ever fires when the direct-X search
    # above already failed for every unclaimed track, so it changes
    # nothing about the ~150 nets that already route cleanly.
    def route_row1_row2(pin_list, from_bottom, x_end, direction_label):
        for port, direc, inst, pname, cx, cy in pin_list:
            net = port_to_net_name(port, resolver)
            # v50 (design_notes 70): the pin's own physical source-pin
            # footprint, ALWAYS excluded regardless of whether `net` has
            # a net_shapes_log entry (own_region()'s only source, empty
            # for "stub" nets -- design_notes 69.2(A)/70). Without this,
            # a stub net's own m2_clear probes below can self-collide
            # with their own starting pad (SELF_MARGIN=3.0 minus the
            # probe's own M2_MIN_GAP=2.0 widening leaves only 1.0um
            # clearance from cy, less than the pad's own PAD_HALF=1.7um
            # radius), reporting EVERY track blocked regardless of any
            # real external routing -- confirmed root cause of the
            # tx_data[0]<->_074_ short surviving the v49 jog fix on the
            # first attempt.
            self_own = db.Region(db.Box(um(cx - half2 - 0.2), um(cy - half2 - 0.2),
                                         um(cx + half2 + 0.2), um(cy + half2 + 0.2)))
            tried = set()
            result = None
            for _attempt in range(n_ch2_tracks):
                idx = claim_ch2_track(from_bottom=from_bottom, exclude=tried)
                if idx is None:
                    break
                tried.add(idx)
                track_y = ch2_track_y(idx)
                if from_bottom:
                    ok1 = m2_clear(cx - half2, cy + SELF_MARGIN, cx + half2, track_y - half2,
                                    exclude_net=net, extra_own=self_own)
                    ok2 = m1_clear(cx, track_y - half1, x_end, track_y + half1, exclude_net=net)
                else:
                    ok1 = m2_clear(cx - half2, track_y + half2, cx + half2, cy - SELF_MARGIN,
                                    exclude_net=net, extra_own=self_own)
                    ok2 = m1_clear(x_end, track_y - half1, cx, track_y + half1, exclude_net=net)
                if ok1 and ok2:
                    used_ch2_idx.add(idx)
                    result = (idx, track_y, cx, True)
                    break
            if result is None:
                jog_order = range(n_ch2_tracks) if from_bottom else range(n_ch2_tracks - 1, -1, -1)
                for idx in jog_order:
                    if idx in used_ch2_idx:
                        continue
                    track_y = ch2_track_y(idx)
                    if from_bottom:
                        y_lo, y_hi = cy + SELF_MARGIN, track_y - half2
                        dog_y = (cy + SELF_MARGIN - half2, cy + SELF_MARGIN + half2)
                    else:
                        y_lo, y_hi = track_y + half2, cy - SELF_MARGIN
                        dog_y = (cy - SELF_MARGIN - half2, cy - SELF_MARGIN + half2)
                    jog_x = find_clear_x_vertical(cx, y_lo, y_hi, half2, dogleg_y=dog_y,
                                                   exclude_net=net, max_shift=60.0, step=1.0,
                                                   extra_own=self_own)
                    if jog_x is None:
                        continue
                    if from_bottom:
                        ok2j = m1_clear(jog_x, track_y - half1, x_end, track_y + half1, exclude_net=net)
                    else:
                        ok2j = m1_clear(x_end, track_y - half1, jog_x, track_y + half1, exclude_net=net)
                    if ok2j:
                        used_ch2_idx.add(idx)
                        result = (idx, track_y, jog_x, True)
                        break
            if result is None:
                # every track had a real collision against pre-existing routing
                # (channel2 has zero spare margin at minheight -- section 43.8),
                # and no lateral jog within 60um could sidestep it either;
                # still MUST pick a track not already used by one of THIS
                # script's own earlier wires, or two different new signals
                # would short into each other. Least-bad choice: the track
                # with the FEWEST touching pre-existing shapes.
                if from_bottom:
                    idx = min((i for i in range(n_ch2_tracks) if i not in used_ch2_idx),
                              key=lambda i: _collision_count(cx, ch2_track_y(i), x_end, half1, exclude_net=net))
                else:
                    idx = min((i for i in range(n_ch2_tracks) if i not in used_ch2_idx),
                              key=lambda i: _collision_count(x_end, ch2_track_y(i), cx, half1, exclude_net=net))
                used_ch2_idx.add(idx)
                result = (idx, ch2_track_y(idx), cx, False)
            idx, track_y, via_x, ok = result
            if from_bottom:
                self_y_lo, self_y_hi = cy - half2, cy + SELF_MARGIN + half2
                dog_y_lo, dog_y_hi = cy + SELF_MARGIN - half2, cy + SELF_MARGIN + half2
                rise_y_lo, rise_y_hi = cy + SELF_MARGIN - half2, track_y + half2
            else:
                self_y_lo, self_y_hi = cy - SELF_MARGIN - half2, cy + half2
                dog_y_lo, dog_y_hi = cy - SELF_MARGIN - half2, cy - SELF_MARGIN + half2
                rise_y_lo, rise_y_hi = track_y - half2, cy - SELF_MARGIN + half2
            m2_box(cx - half2, self_y_lo, cx + half2, self_y_hi)
            if via_x != cx:
                m2_box(min(cx, via_x) - half2, dog_y_lo, max(cx, via_x) + half2, dog_y_hi)
            m2_box(via_x - half2, rise_y_lo, via_x + half2, rise_y_hi)
            place_via(via_x, track_y)
            if from_bottom:
                m1_box(via_x, track_y - half1, x_end, track_y + half1)
            else:
                m1_box(x_end, track_y - half1, via_x, track_y + half1)
            add_m1_pin(x_end - PIN_SIZE_UM / 2.0 if from_bottom else x_end + PIN_SIZE_UM / 2.0, track_y, port)
            report.append((port, direc, direction_label,
                           "M2 up -> via -> M1 right" if from_bottom else "M2 down -> via -> M1 left",
                           cx, cy, x_end, track_y, ok))

    route_row1_row2(row1_list, True, x_right_end, "row1")

    # ---- row2: M2 down into channel2, via_1, M1 left, 10um past X=0 ----
    x_left_end = -EXTEND_UM
    route_row1_row2(row2_list, False, x_left_end, "row2")

    layout.write(out_gds)
    print(f"wrote {out_gds}")
    n_clear = sum(1 for r in report if r[-1])
    print(f"{len(report)} port(s) wired, {n_clear} with a pre-existing-geometry-clear path, "
          f"{len(report) - n_clear} flagged for review")
    for port, direc, row, path, x0, y0, x1, y1, ok in report:
        status = "OK" if ok else "CHECK"
        print(f"  [{status}] {port:14s} {direc:7s} {row:5s} {path:26s} "
              f"({x0:8.1f},{y0:8.1f}) -> ({x1:8.1f},{y1:8.1f})")
    return report


if __name__ == "__main__":
    main()
