"""spi_config.py -- one place for every project path and geometric constant.

The routing scripts under scripts/ are ports of TR-1um_Async_I2C/script/
(kept verbatim in scripts/i2c_ref/ for reference).  Those originals carry
hardcoded absolute paths and I2C-specific names; the port replaces each of
them with a lookup here, so the routing algorithms themselves stay
byte-for-byte what produced a DRC/LVS-clean I2C chip.  See scripts/PORTING.md.

Environment overrides:
  TR1UM_PDK   path to the TR-1um PDK checkout (for the via_1 PCell library)
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- design identity ----------------------------------------------------
TOP_CELL_NAME = "spi_slave_sclk_nrow_fm"     # the placed/routed core cell
CHIP_TOP_CELL = "tr_1um_3wire_SPI"           # gds.top_cell in info.yaml

# ---- inputs -------------------------------------------------------------
LEF_PATH = os.path.join(ROOT, "lef", "TR-1um_STDCELL.lef")
CELL_GDS = os.path.join(ROOT, "lef", "TR-1um_STDCELL.gds")
NET_PATH = os.path.join(ROOT, "layout", "spi_slave_sclk_net_pnr.v")
FRAME_GDS = os.path.join(ROOT, "lef", "TR-1um_frame_25x25.gds")
FRAME_CELL = "OSS_FRAME_GIO"                 # the 16-pad GIO ring

# ---- placement / routing artefacts --------------------------------------
LAYOUT = os.path.join(ROOT, "layout")
PLACEMENT_JSON = os.path.join(LAYOUT, "placement_nrow_fm.json")
PLACEMENT_GDS = os.path.join(LAYOUT, "step5", "route_step_1_placement.gds")
ROUTED_RAW_GDS = os.path.join(LAYOUT, "step6", "route_step_2_routed_raw.gds")
RIPUP_GDS = os.path.join(LAYOUT, "step7", "route_step_3_ripup_reroute.gds")
TOPPINS_GDS = os.path.join(LAYOUT, "step8", "route_step_4_top_pins.gds")
POWERPINS_GDS = os.path.join(LAYOUT, "step9", "route_step_5_power_pins.gds")

PIN_MAP_JSON = os.path.join(LAYOUT, "pin_map_nrow_fm.json")
NET_SHAPES_JSON = os.path.join(LAYOUT, "net_shapes_nrow_fm.json")
CHANNEL_USAGE_JSON = os.path.join(LAYOUT, "channel_usage_nrow_fm.json")
FORCE_JOG_EVENTS_JSON = os.path.join(LAYOUT, "force_jog_events_nrow_fm.json")
SQUEEZED_GDS = os.path.join(LAYOUT, "step10", "route_step_6_squeezed.gds")
PIN_MAP_RR_JSON = os.path.join(LAYOUT, "pin_map_nrow_fm_rr.json")
PIN_MAP_SQ_JSON = os.path.join(LAYOUT, "pin_map_nrow_fm_sq.json")
NET_SHAPES_SQ_JSON = os.path.join(LAYOUT, "net_shapes_nrow_fm_sq.json")
NET_SHAPES_RR_JSON = os.path.join(LAYOUT, "net_shapes_nrow_fm_rr.json")
COMPACTION_INFO_JSON = os.path.join(LAYOUT, "compaction_info_nrow_fm.json")

# ---- geometry (must match scripts/place.py) -----------------------------
N_ROWS = 2
ROW_WIDTH_UM = 1620.0
ROW_HEIGHT_UM = 64.8
TRACK_PITCH = 5.4

# Channel budget used FOR ROUTING (bottom margin first).  scripts/place.py's
# own estimate counts only net crossings, but the router's jog mechanism
# claims a FRESH track per row-crossing, so routing starts from a generous
# budget and the compaction stage shrinks it afterwards -- exactly the I2C
# flow (its V10 ran [131.6, 700, 1000, 700, 153.2] for 4 rows, then squeezed).
ROUTE_CH_HEIGHTS = [140.0, 900.0, 160.0]


# ---- chip-level assembly (core inside the GIO pad ring) -----------------
CHIP = os.path.join(LAYOUT, "chip")

# Radius at which the GIO's own routable P/HIZ/OUT terminals sit, measured
# from the chip centre.  The I2C project used this same number as the outer
# wall of the top-level routing channel (design_notes.md 78.3).
GIO_PIN_RADIUS = 921.7

# Radius of the ring's innermost real geometry -- the wall the top-level
# channel actually stops at.  Measured off lef/TR-1um_frame_25x25.gds across
# the core's own x-span; scripts/assemble_top.py re-checks it every run.
GIO_INNER_WALL = 920.0

# The core is centred in X: its native bbox spans -6.3..1626.3, so an offset of
# -810.0 puts it at +-816.3, symmetric about the chip axis.  Same value the I2C
# chip used, and the reason this project fixed the row width at 1620 um.
CORE_OFFSET_X = -810.0

# Y is set so the channel between the core's top edge and that wall is
# TOP_CHANNEL_UM wide.  CORE_OFFSET_Y is derived in chip_geometry() rather than
# written down, so it follows the core if its height ever changes.
#
# 90, not 80: the core was nudged 10 um further from the ring at the user's
# request, which is the same thing as widening the top channel by 10.  That
# corridor was the tightest of the five (12 nets in the 14 tracks 80 um buys)
# and now has 16.  The channels below the core are unaffected -- they are
# PTECT_CHANNEL_UM, a separate knob, so this move does not drag them along.
TOP_CHANNEL_UM = 90.0

# Unused area below the core is claimed by a PTECT box (layer 63/1), sized to
# leave the same channel width against the wall -- the I2C flow's own rule,
# where "about 120 um of channel" meant a PTECT edge 121.7 um inside the
# terminal ring (design_notes.md 78.3).
PTECT_LAYER = (63, 1)
# A real channel, not the I2C flow's 10 um gap: this core puts 12 ports on its
# bottom edge, where the I2C core (4 rows, ports on its left and right edges)
# put none.  The same width is left below the PTECT box, against the wall.
PTECT_CHANNEL_UM = 80.0
PTECT_X0, PTECT_X1 = -800.0, 800.0


def core_bbox_um(gds=None, cell=None):
    """(left, bottom, right, top) of the routed core in its own coordinates."""
    import klayout.db as db
    ly = db.Layout()
    ly.read(gds or SQUEEZED_GDS)
    c = ly.cell(cell or TOP_CELL_NAME)
    if c is None:
        raise SystemExit(f"{cell or TOP_CELL_NAME} not found in {gds or SQUEEZED_GDS}")
    b = c.bbox()
    return (b.left * ly.dbu, b.bottom * ly.dbu, b.right * ly.dbu, b.top * ly.dbu)


def chip_geometry(gds=None, cell=None):
    """Everything the chip-level assembly and router need to agree on."""
    l, bot, r, t = core_bbox_um(gds, cell)
    off_y = GIO_INNER_WALL - TOP_CHANNEL_UM - t
    core = (CORE_OFFSET_X + l, off_y + bot, CORE_OFFSET_X + r, off_y + t)
    ptect_top = core[1] - PTECT_CHANNEL_UM
    ptect_bottom = -(GIO_INNER_WALL - PTECT_CHANNEL_UM)
    return {
        "core_offset": (CORE_OFFSET_X, round(off_y, 3)),
        "core_native_bbox": (l, bot, r, t),
        "core_chip_bbox": tuple(round(v, 3) for v in core),
        "ptect_box": (PTECT_X0, round(ptect_bottom, 3), PTECT_X1, round(ptect_top, 3)),
        # to the wall (what the router may use) / to the terminal ring
        "channel_top": (round(GIO_INNER_WALL - core[3], 3),
                        round(GIO_PIN_RADIUS - core[3], 3)),
        "channel_bottom": (round(ptect_bottom + GIO_INNER_WALL, 3),
                           round(ptect_bottom + GIO_PIN_RADIUS, 3)),
        "channel_left": (round(core[0] + GIO_INNER_WALL, 3),
                         round(core[0] + GIO_PIN_RADIUS, 3)),
        "channel_right": (round(GIO_INNER_WALL - core[2], 3),
                          round(GIO_PIN_RADIUS - core[2], 3)),
    }


def pdk_tech_python():
    """directory holding the PDK's KLayout PCell package (`from cells import
    tr_1um`), which every via in the router is an instance of."""
    env = os.environ.get("TR1UM_PDK")
    cands = []
    if env:
        cands.append(os.path.join(env, "libs.tech", "klayout", "tech", "python"))
        cands.append(env)
    cands += [
        os.path.expanduser("~/Dropbox/91_OpenPDK/TR-1um/libs.tech/klayout/tech/python"),
        os.path.join(os.path.dirname(ROOT), "TR-1um", "libs.tech", "klayout", "tech", "python"),
        os.path.expanduser("~/TR-1um/libs.tech/klayout/tech/python"),
    ]
    for c in cands:
        if os.path.isdir(os.path.join(c, "cells")):
            return c
    raise SystemExit(
        "TR-1um PDK KLayout PCell package not found.\n"
        "  set TR1UM_PDK to the PDK checkout, e.g.\n"
        "    export TR1UM_PDK=~/Dropbox/91_OpenPDK/TR-1um\n"
        f"  tried: {cands}")


def artifact(basename):
    """Any intermediate file the ported I2C scripts used to hardcode an
    absolute sandbox path for now lands in layout/ under the same name."""
    return os.path.join(LAYOUT, basename)


def channel_heights(placement_json=PLACEMENT_JSON):
    import json
    return json.load(open(placement_json))["ch_heights"]
