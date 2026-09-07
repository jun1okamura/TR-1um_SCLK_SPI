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
