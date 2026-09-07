"""port_rules.py -- source-level patches applied by port_i2c_scripts.py.

Kept in their own module because they contain triple-quoted Python code,
which cannot be embedded inside the porter's own string literals.

SQUEEZE_PIN_PROTECT: teach squeeze_channels_nrow_fm.py to keep the Y
intervals occupied by top-level PIN markers and their TXM1/TXM2 labels.
Without it the "collapse the headroom above the last claimed track" rule
flattens every top-edge pin to zero height (the bottom-edge ones, drawn
from y=0 into the core, survive), which also drags each label off the pin
it names -- breaking gen_lef.py's and the LVS extraction's "text label
inside the PIN shape" convention.
"""

_HELPERS = '''V1_LAYER = (19, 0)

# PIN markers and their TXM1/TXM2 labels must survive compaction: they are
# drawn ON the core boundary, so as far as the routed geometry is concerned
# their whole Y span is unused headroom.
PIN_PROTECT_LAYERS = [(48, 1), (49, 1), (48, 0), (49, 0)]


def collect_protect_y(layout, top, layers=PIN_PROTECT_LAYERS):
    """Merged, sorted Y intervals occupied by the TOP CELL's own PIN markers
    and pin labels.  Cell-internal pins live inside row bands, which are
    identity-mapped anyway, so only the top cell's own shapes are scanned."""
    want = set(layers)
    iv = []
    for li in layout.layer_indexes():
        li_info = layout.get_info(li)
        if (li_info.layer, li_info.datatype) not in want:
            continue
        for s in top.shapes(li).each():
            b = s.dbbox()
            if b.empty():
                continue
            iv.append((b.bottom, b.top))
    iv.sort()
    out = []
    for lo, hi in iv:
        if out and lo <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _collapse_to(breakpoints, cur_old, cur_new, target_old, protect_y):
    """Advance the map from cur_old up to target_old, collapsing it to zero
    height EXCEPT across a protected interval, which stays identity."""
    for p0, p1 in protect_y:
        lo, hi = max(p0, cur_old), min(p1, target_old)
        if hi - lo <= 1e-9:
            continue
        if lo - cur_old > 1e-9:
            cur_old = lo
            breakpoints.append((cur_old, cur_new))
        cur_old = hi
        cur_new += (hi - lo)
        breakpoints.append((cur_old, cur_new))
    if target_old - cur_old > 1e-9:
        cur_old = target_old
        breakpoints.append((cur_old, cur_new))
    return cur_old, cur_new


def compute_kept_indices('''

SQUEEZE_PIN_PROTECT = [
    ("V1_LAYER = (19, 0)\n\n\ndef compute_kept_indices(", _HELPERS),

    ("def build_y_map(ch_y0, ch_heights, row_y0, row_h, n_rows, n_ch,\n"
     "                 track_pitch, track0_offset, kept_by_channel):",
     "def build_y_map(ch_y0, ch_heights, row_y0, row_h, n_rows, n_ch,\n"
     "                 track_pitch, track0_offset, kept_by_channel, protect_y=()):"),

    ("            gap = slice_lo - prev_track_top\n"
     "            if gap > 1e-9:\n"
     "                cur_new += 0.0  # collapse: old advances, new does not\n"
     "            cur_old = slice_lo\n"
     "            breakpoints.append((cur_old, cur_new))",
     "            # collapse the gap before this track's slice, except where a\n"
     "            # PIN marker/label sits (protect_y keeps that part identity)\n"
     "            cur_old, cur_new = _collapse_to(breakpoints, cur_old, cur_new,\n"
     "                                            slice_lo, protect_y)"),

    ("        band_hi = ch_y0[c] + ch_heights[c]\n"
     "        if band_hi - cur_old > 1e-9:\n"
     "            cur_old = band_hi\n"
     "            breakpoints.append((cur_old, cur_new))\n"
     "        else:\n"
     "            cur_old = band_hi",
     "        band_hi = ch_y0[c] + ch_heights[c]\n"
     "        cur_old, cur_new = _collapse_to(breakpoints, cur_old, cur_new,\n"
     "                                        band_hi, protect_y)\n"
     "        cur_old = band_hi"),

    ("    y_map, new_core_h = build_y_map(ch_y0, ch_heights, row_y0, row_h, n_rows, n_ch,\n"
     "                                     track_pitch, track0_offset, kept_by_channel)\n"
     "    old_core_h = ch_y0[-1] + ch_heights[-1]\n"
     '    print(f"core height: {old_core_h:.1f} um -> {new_core_h:.1f} um "\n'
     '          f"(-{old_core_h - new_core_h:.1f} um, -{100*(old_core_h-new_core_h)/old_core_h:.1f}%)")\n'
     "\n"
     "    layout = db.Layout()\n"
     "    layout.read(in_gds)\n"
     "    dbu = layout.dbu\n"
     "    top = layout.cell(TOP_CELL_NAME)",
     "    layout = db.Layout()\n"
     "    layout.read(in_gds)\n"
     "    dbu = layout.dbu\n"
     "    top = layout.cell(TOP_CELL_NAME)\n"
     "\n"
     "    protect_y = collect_protect_y(layout, top)\n"
     "    if protect_y:\n"
     '        print(f"protecting {len(protect_y)} Y interval(s) holding PIN "\n'
     '              f"markers/labels: "\n'
     '              + ", ".join(f"{a:.1f}-{b:.1f}" for a, b in protect_y))\n'
     "\n"
     "    y_map, new_core_h = build_y_map(ch_y0, ch_heights, row_y0, row_h, n_rows, n_ch,\n"
     "                                     track_pitch, track0_offset, kept_by_channel,\n"
     "                                     protect_y=protect_y)\n"
     "    old_core_h = ch_y0[-1] + ch_heights[-1]\n"
     '    print(f"core height: {old_core_h:.1f} um -> {new_core_h:.1f} um "\n'
     '          f"(-{old_core_h - new_core_h:.1f} um, -{100*(old_core_h-new_core_h)/old_core_h:.1f}%)")'),
]

# --------------------------------------------------------------------------
# HIGHLIGHT_PORTS_FROM_NETLIST -- derive the top-level port lists from the
# design instead of hardcoding the I2C design's names.
#
# highlight_top_pins_nrow_fm.py (which route_top_pins_nrow_fm.py's
# gather_pins imports) carried:
#
#     SCALAR_PORTS = ["rst_n", "scl", "sda_in", ...]   <- I2C port names
#     BUS_PORTS    = {"tx_data": 8, "rx_data": 8}
#
# gather_pins only ever looks for ports listed in those two containers, so on
# any other design every scalar port is silently skipped.  That is exactly
# what happened here: the tx_data/rx_data buses came out (that dict happened
# to match bit for bit) while all nine scalar ports were left unrouted,
# taking the BUFTH input nets sclk/cs_n/sdio_in with them.
#
# The replacement reads the port declarations out of the gate-level netlist,
# so the lists always match the design being routed.  Whole-signal
# `assign port = net;` aliases are picked up too: the union-find resolver in
# netlist_parser handles scalars, but a BUS alias (assign rx_data = rx_data_r)
# has to be applied bit by bit, which is what BUS_PORT_NET_ALIAS is for.
# --------------------------------------------------------------------------

_PORT_SCAN = '''import re as _re


def _scan_ports(net_path):
    """(port_alias, bus_alias, scalar_ports, bus_ports, port_dir) read from
    the gate-level netlist's own port declarations."""
    text = open(net_path).read()
    scalars, buses, direction = [], {}, {}
    for m in _re.finditer(
            r"^\\s*(input|output|inout)\\s+(?:wire\\s+|reg\\s+)?"
            r"(?:\\[\\s*(\\d+)\\s*:\\s*(\\d+)\\s*\\]\\s*)?([A-Za-z_][\\w$, ]*);",
            text, _re.M):
        kind, hi, lo, names = m.group(1), m.group(2), m.group(3), m.group(4)
        for n in (x.strip() for x in names.split(",") if x.strip()):
            direction[n] = "INPUT" if kind == "input" else "OUTPUT"
            if hi is None:
                scalars.append(n)
            else:
                buses[n] = abs(int(hi) - int(lo)) + 1
    port_alias, bus_alias = {}, {}
    for m in _re.finditer(r"assign\\s+([A-Za-z_][\\w$]*)\\s*=\\s*"
                          r"([A-Za-z_][\\w$]*)\\s*;", text):
        lhs, rhs = m.group(1), m.group(2)
        if lhs in buses:
            bus_alias[lhs] = rhs
        elif lhs in scalars:
            port_alias[lhs] = rhs
    for b, w in buses.items():
        for i in range(w):
            direction[f"{b}[{i}]"] = direction[b]
    return port_alias, bus_alias, scalars, buses, direction


(PORT_NET_ALIAS, BUS_PORT_NET_ALIAS,
 SCALAR_PORTS, BUS_PORTS, PORT_DIR_DERIVED) = _scan_ports(_cfg.NET_PATH)
'''

HIGHLIGHT_PORTS_FROM_NETLIST = [
    ('PORT_NET_ALIAS = {\n'
     '    "addr_match": "addr_ok",\n'
     '    "rw": "rw_bit",\n'
     '}\n'
     'BUS_PORT_NET_ALIAS = {\n'
     '    "rx_data": "rx_data_r",  # rx_data[i] -> rx_data_r[i]\n'
     '}\n'
     '\n'
     'SCALAR_PORTS = ["rst_n", "scl", "sda_in", "sda_oe", "rx_valid", '
     '"addr_match", "rw", "busy"]\n'
     'BUS_PORTS = {"tx_data": 8, "rx_data": 8}\n',
     _PORT_SCAN),
]
