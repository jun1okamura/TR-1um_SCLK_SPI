"""
highlight_top_pins_nrow_fm.py (section 46, user request "STEP7で止めて、
トップピンの位置をハイライトしてください")

Marks every physical location where one of i2c_slave_async's TOP-LEVEL
ports (VDD, GND, rst_n, scl, sda_in, sda_oe, tx_data[7:0], rx_data[7:0],
rx_valid, addr_match, rw, busy) touches M2 inside the core, on top of an
already-routed GDS (default: v6's step7 -- the minheight-compressed
core, section 43.8 -- chosen because it's the last checkpoint before the
geometric squeeze, section 44, which the user wants reviewed before that
further compaction).

Two different situations, both handled:
  1. A port whose net has exactly ONE pin in the whole design (e.g. each
     tx_data[i] bit, sda_oe's driver "sda_oe_r", addr_match's driver
     "addr_ok", rw's driver "rw_bit") -- a true dead-end stub, never
     routed by route_channels_nrow_fm.py (it only routes nets with >=2
     pins). This IS the one and only physical connection point for that
     signal.
  2. A port whose net fans out/in to MULTIPLE cells (rst_n, scl, sda_in,
     rx_valid, busy) -- already routed internally as a normal
     row-only/adjacent-pair/high-FO net. There's no single "the" pin;
     every one of that net's pins is a legal place to tap a frame
     connection since M1/M2 is one continuous conductor. All of them
     are marked, so the user can pick whichever is closest to the row
     they want to exit through.

Port -> underlying net name mapping is NOT always the port name itself:
Yosys emitted trailing `assign <port> = <internal_net>;` aliases for
sda_oe, addr_match, rw, and rx_data (see i2c_slave_async_net_v6.v's
final `assign` block) -- this script's PORT_NET_MAP encodes that.
VDD/GND are handled separately (see below) since they're excluded from
this project's net_pins by design (POWER/GROUND-use pins never appear
in net_pins, section 35.9 -- supplied only via the continuous TAP2/TAP3
M2 strap mesh, not per-cell wiring), so "the pin location" for VDD/GND
is instead the TAP column X positions at the very top and bottom margin
edges (Y=0 and Y=core_height) -- the natural, nearest-to-frame place to
tap the power mesh.

Output: two new layers on a duplicated copy of the input GDS (never
modifies the input file):
  (260, 0): a small diamond marker (via_1-sized box, rotated look is
            skipped for GDS simplicity -- just an easily-spottable box)
            at each signal pin's exact M2 location.
  (260, 1): a text label (the TOP-LEVEL port name, not the internal net
            name) at each marker.
  (260, 2): VDD/GND tap-mesh marker boxes at top/bottom margin TAP
            column X positions.
  (260, 3): "VDD"/"GND" text labels for those.
"""
import json
import sys

import klayout.db as db

TOP_CELL_NAME = "i2c_slave_async_nrow_fm"
M2_LAYER = (20, 0)
MARKER_LAYER_BASE = 260

TAP_GND_X_LOCAL = (1.0, 4.4)
TAP_VDD_X_LOCAL = (6.4, 9.8)
TAP_WIDTH_UM = 10.8

MARKER_SIZE_UM = 6.0  # a bit bigger than a via pad (3.4um) so it stands out

# port -> underlying net name, where different from the port name itself
# (see i2c_slave_async_net_v6.v's trailing `assign` block).
#
# v9 FIX (design_notes.md 78.7, this session): "sda_oe": "sda_oe_r" was
# correct for v6's RTL (which had `assign sda_oe = sda_oe_r;`), but v9's
# RTL (src/i2c_slave_async_net_v9_rowbuf.v) no longer aliases sda_oe --
# its driving DFFRB's QB pin connects DIRECTLY to net "sda_oe" (line 894:
# ".QB(sda_oe)"), while "sda_oe_r" is now a SEPARATE internal-only net
# (the same flip-flop's non-inverted Q output, line 893: ".Q(sda_oe_r)"),
# confirmed via direct grep. Leaving the stale v6-era alias in place
# caused route_top_pins_nrow_fm.py's gather_pins() (which imports
# port_net_name from this module) to capture the WRONG pin for the
# "sda_oe" port -- it grabbed the Q/sda_oe_r pin's physical location
# instead of QB/sda_oe's, and routed+labeled a BBOX-edge exit there.
# Root-caused directly from a KLayout LVS net cross-reference the user
# ran (layout/step8/LVS_error1.lvsdb): net pair layout='sda_oe' <->
# schematic='SDA_OE_R' (Match -- i.e. the labeled "sda_oe" wire really
# IS electrically sda_oe_r), plus a SEPARATE pair layout='$I1117' <->
# schematic='SDA_OE' (Match but anonymous -- the real sda_oe/QB net was
# never given a labeled edge pin at all, since "sda_oe" as a net name
# was never a key in net_to_port under the old, wrong alias).
PORT_NET_ALIAS = {
    "addr_match": "addr_ok",
    "rw": "rw_bit",
}
BUS_PORT_NET_ALIAS = {
    "rx_data": "rx_data_r",  # rx_data[i] -> rx_data_r[i]
}

SCALAR_PORTS = ["rst_n", "scl", "sda_in", "sda_oe", "rx_valid", "addr_match", "rw", "busy"]
BUS_PORTS = {"tx_data": 8, "rx_data": 8}

# 108.42 (V10, this session): this v6/v9-era static PORT_NET_ALIAS table
# has needed a manual update every single time the RTL's synthesis
# happened to route a port through a different `assign port = net;`
# alias chain (v6: sda_oe -> sda_oe_r; v9: no alias at all, direct QB
# pin; v10: sda_oe -> _187_, rx_valid -> _154_, both anonymous
# Yosys-numbered nets from a DIFFERENT DFFRB/gate than either prior
# case) -- each drift silently broke gather_pins() until someone
# noticed a "missing pin(s)" warning or a mis-routed exit. Rather than
# add yet another one-off dict entry, `resolver` (optional, an
# alias-resolving callable such as netlist_parser's own
# _build_alias_resolver(text) union-find `find`) lets these two
# functions resolve straight from the CURRENT netlist's actual `assign`
# chains -- the exact same resolution netlist_parser.parse_netlist()
# already applies when building the pin-net names baked into the
# placement JSON these scripts read. Passing no resolver preserves the
# old static-dict-only behavior exactly (v9's call sites are
# unaffected).
def port_net_name(port, resolver=None):
    net = PORT_NET_ALIAS.get(port, port)
    return resolver(net) if resolver else net


def bus_bit_net_name(bus, i, resolver=None):
    base = BUS_PORT_NET_ALIAS.get(bus, bus)
    net = f"{base}[{i}]"
    return resolver(net) if resolver else net


def build_net_pins(placement, ch_heights, row_h):
    """Same construction as route_channels_nrow_fm.py's net_pins, kept
    independent here (script's own placement->absolute-Y mapping) so this
    tool never needs to import/monkeypatch the router module."""
    n_rows = len(placement["rows"])
    row_y0 = []
    y = 0.0
    for i in range(n_rows):
        y += ch_heights[i]
        row_y0.append(y)
        y += row_h
    core_h = y + ch_heights[n_rows]

    net_pins = {}
    for r, row_insts in enumerate(placement["rows"]):
        yoff = row_y0[r]
        for inst in row_insts:
            for pname, pinfo in inst["pins"].items():
                if pinfo["use"] in ("POWER", "GROUND"):
                    continue
                for layer, x0, y0, x1, y1 in pinfo["rects"]:
                    if layer != "M2":
                        continue
                    net_pins.setdefault(pinfo["net"], []).append(
                        (inst["name"], pname, (x0 + x1) / 2.0, y0 + yoff, y1 + yoff))
    return net_pins, core_h


def find_tap_x_positions(placement):
    """Every row's TAP2 instance X (local, relative to row start -- same
    for every row by construction, section 38.6), used for VDD/GND
    marker X positions."""
    xs = set()
    for inst in placement["rows"][0]:
        if inst["type"] == "TAP2":
            xs.add(round(inst["x"], 3))
    return sorted(xs)


def main(placement_json, in_gds, out_gds, ch_heights, row_h=None, net_file=None):
    resolver = None
    if net_file:
        from netlist_parser import _build_alias_resolver
        resolver = _build_alias_resolver(open(net_file).read())

    placement = json.load(open(placement_json))
    if row_h is None:
        row_h = placement["row_height"]
    net_pins, core_h = build_net_pins(placement, ch_heights, row_h)

    layout = db.Layout()
    layout.read(in_gds)
    dbu = layout.dbu
    top = layout.cell(TOP_CELL_NAME)

    marker_idx = layout.layer(MARKER_LAYER_BASE, 0)
    text_idx = layout.layer(MARKER_LAYER_BASE, 1)
    pwr_marker_idx = layout.layer(MARKER_LAYER_BASE, 2)
    pwr_text_idx = layout.layer(MARKER_LAYER_BASE, 3)

    def um(v):
        return int(round(v / dbu))

    def add_marker(x, y, label):
        half = MARKER_SIZE_UM / 2.0
        box = db.Box(um(x - half), um(y - half), um(x + half), um(y + half))
        top.shapes(marker_idx).insert(box)
        t = db.Text(label, db.Trans(um(x + half + 1.0), um(y)))
        top.shapes(text_idx).insert(t)

    n_marked = 0
    missing = []

    for port in SCALAR_PORTS:
        net = port_net_name(port, resolver)
        pads = net_pins.get(net)
        if not pads:
            missing.append(port)
            continue
        for inst_name, pname, cx, y0, y1 in pads:
            cy = (y0 + y1) / 2.0
            add_marker(cx, cy, port)
            n_marked += 1

    for bus, width in BUS_PORTS.items():
        for i in range(width):
            port = f"{bus}[{i}]"
            net = bus_bit_net_name(bus, i, resolver)
            pads = net_pins.get(net)
            if not pads:
                missing.append(port)
                continue
            for inst_name, pname, cx, y0, y1 in pads:
                cy = (y0 + y1) / 2.0
                add_marker(cx, cy, port)
                n_marked += 1

    # VDD/GND: TAP column X positions at the very top and bottom margin
    # edges -- nearest-to-frame tap points on the continuous power mesh.
    tap_xs = find_tap_x_positions(placement)
    n_pwr = 0
    for tap_x0 in tap_xs:
        for label, (lx0, lx1) in (("GND", TAP_GND_X_LOCAL), ("VDD", TAP_VDD_X_LOCAL)):
            cx = tap_x0 + (lx0 + lx1) / 2.0
            for cy in (0.0, core_h):
                half = MARKER_SIZE_UM / 2.0
                box = db.Box(um(cx - half), um(cy - half), um(cx + half), um(cy + half))
                top.shapes(pwr_marker_idx).insert(box)
                t = db.Text(label, db.Trans(um(cx + half + 1.0), um(cy)))
                top.shapes(pwr_text_idx).insert(t)
                n_pwr += 1

    layout.write(out_gds)
    print(f"wrote {out_gds}")
    print(f"marked {n_marked} signal pin location(s) across {len(SCALAR_PORTS) + sum(BUS_PORTS.values())} "
          f"top-level ports on layer ({MARKER_LAYER_BASE},0)/({MARKER_LAYER_BASE},1)")
    print(f"marked {n_pwr} VDD/GND tap-mesh location(s) (top+bottom margin x {len(tap_xs)} TAP column(s)) "
          f"on layer ({MARKER_LAYER_BASE},2)/({MARKER_LAYER_BASE},3)")
    if missing:
        print(f"WARNING: {len(missing)} port(s) had no matching net in net_pins (check PORT_NET_ALIAS): {missing}")


if __name__ == "__main__":
    PLACEMENT_JSON = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/placement_nrow_fm_v6.json"
    IN_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/steps_v6/v6_step_7_minheight_compressed.gds"
    OUT_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/steps_v6/v6_step_7_top_pins_highlighted.gds"
    CH_HEIGHTS = [92.0, 260.0, 240.0, 224.0, 36.0]  # section 43.8, v6 minheight
    main(PLACEMENT_JSON, IN_GDS, OUT_GDS, CH_HEIGHTS)
