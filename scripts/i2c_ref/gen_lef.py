"""
gen_lef.py

Trial LEF generator for the next-generation (5.4um grid) standard cell
library (design_notes.md section 35). Reads a cell's physical GDS
(TR-1um_STDCELL.gds under LEF/) and emits a LEF MACRO definition by
extracting geometry directly -- no hand-entered coordinates -- so the
LEF stays in sync with the GDS as cells are iterated on.

Conventions relied on (established this session, see design_notes.md
section 35 and the INV_X1 review thread):
  - prBoundary (235/0) defines the macro SIZE.
  - M1PIN (48/1) marks the exact M1-layer pin ports (VDD/GND).
  - M2PIN (49/1) marks the exact M2-layer pin ports (signal I/O).
  - TXM1 (48/0) / TXM2 (49/0) text labels give the pin names, matched
    to the M1PIN/M2PIN region containing that label's point.
  - OBS = (M1 region - M1 pin regions) union (M2 region - M2 pin
    regions): everything that is real metal but NOT an exposed pin
    port is emitted as an obstruction so the P&R router knows not to
    route through it.
  - SITE width is fixed at 5.4um (the same grid as the M2 track
    pitch/pin grid, see section 35.1) -- deliberately the same number,
    not a coincidence: placement-row snapping and pin-track spacing
    share one grid.

Pin DIRECTION/USE cannot be derived from geometry alone, so a small
per-cell, per-pin metadata table is kept below (PIN_META). Extend this
table as more cells are added.
"""
import sys
import klayout.db as db

GDS_PATH = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/TR-1um_STDCELL.gds"
LEF_OUT = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/TR-1um_STDCELL.lef"

M1_LAYER = (13, 0)
M2_LAYER = (20, 0)
M1PIN_LAYER = (48, 1)
M2PIN_LAYER = (49, 1)
TXM1_LAYER = (48, 0)
TXM2_LAYER = (49, 0)
PR_LAYER = (235, 0)

SITE_NAME = "CoreSite"
SITE_WIDTH_UM = 5.4  # == M2 track pitch, section 35.1

# direction/use metadata per cell -- geometry alone can't tell us this.
# Input pin names / functions cross-checked against script/gen_liberty.py's
# COMB_CELLS table (comb_cell() always names the output pin "Y"; MUX2's
# select input is "S", matching COMB_CELLS' ("MUX2", ..., ["A","B","S"], ...)).
_PWR = {"VDD": ("INOUT", "POWER"), "GND": ("INOUT", "GROUND")}


def _gate_meta(*inputs):
    m = {p: ("INPUT", "SIGNAL") for p in inputs}
    m["Y"] = ("OUTPUT", "SIGNAL")
    m.update(_PWR)
    return m


PIN_META = {
    "INV_X1":   _gate_meta("A"),
    "DEL1":     _gate_meta("A"),
    "NAND2":    _gate_meta("A", "B"),
    "NOR2":     _gate_meta("A", "B"),
    "AND2_X1":  _gate_meta("A", "B"),
    "OR2":      _gate_meta("A", "B"),
    # XOR2/XNOR2 (design_notes 77.24/77.26): the user added physical GDS
    # cells (+ xschem schematic/symbol/extracted) for these directly into
    # the shared TR-1um_STDCELL.gds library this session -- confirmed via
    # direct query: PR boundary 27.0um (5 tracks) x 64.8um, on-grid, same
    # row height as every other cell. Previously excluded from ABC
    # synthesis via a trimmed liberty copy (design_notes 77.24) since they
    # didn't physically exist yet; that restriction is no longer needed.
    "XOR2":     _gate_meta("A", "B"),
    "XNOR2":    _gate_meta("A", "B"),
    "NAND3":    _gate_meta("A", "B", "C"),
    "NOR3":     _gate_meta("A", "B", "C"),
    "OR3":      _gate_meta("A", "B", "C"),
    # AND3_X1/AND4_X1: same story as XOR2/XNOR2 above -- user-added this
    # session (design_notes 77.26). PR boundary 27.0um/5tracks (AND3_X1)
    # and 32.4um/6tracks (AND4_X1), both 64.8um tall, on-grid.
    "AND3_X1":  _gate_meta("A", "B", "C"),
    "NAND4":    _gate_meta("A", "B", "C", "D"),
    "NOR4":     _gate_meta("A", "B", "C", "D"),
    "OR4":      _gate_meta("A", "B", "C", "D"),
    "AND4_X1":  _gate_meta("A", "B", "C", "D"),
    "MUX2":     _gate_meta("A", "B", "S"),
    # DFFR: async-reset D flip-flop (section 35.8's 20-transistor TG-based
    # design). CK is the clock pin (marked USE CLOCK, not plain SIGNAL, so
    # P&R/CTS tools recognize it); D/RSTB are ordinary signal inputs; Q/QB
    # are both real outputs (QB is tapped directly from the slave latch's
    # own NAND2, not a separately buffered copy -- see section 35.8).
    # NOTE (section 42.9): the GDS marker was originally labeled "RB",
    # mismatching TR1um_5_stdcell.lib/the synthesized netlist's "RSTB"
    # pin name (section 39.2's rename) -- this silently dropped every
    # DFFR's reset-pin connection from every placement JSON built before
    # the fix (worked around at the P&R-script level first via a
    # PIN_NAME_ALIASES translation in gen_placement_nrow_fm.py, then
    # fixed at the source: the user relabeled the GDS marker itself to
    # "RSTB", matching the .lib/netlist -- the PIN_META key below follows
    # suit so gen_lef.py's own name_for_polygon lookup (which matches
    # PIN_META keys against the GDS's own text labels) still succeeds.
    "DFFRB": {
        "D":    ("INPUT",  "SIGNAL"),
        "CK":   ("INPUT",  "CLOCK"),
        "RSTB": ("INPUT",  "SIGNAL"),
        "Q":    ("OUTPUT", "SIGNAL"),
        "QB":   ("OUTPUT", "SIGNAL"),
        **_PWR,
    },
    # DFF: reset-less D flip-flop (no RST pin at all -- the master/slave
    # TG-based cell with the RST transistors removed, added this session to
    # let non-critical registers skip DFFR's RST-tree loading; see
    # design_notes.md DFFR-minimization discussion). D/CK inputs, Q/QB
    # outputs, same pin naming and row height (64.8um) as DFFR so it drops
    # into the same placement/routing flow. CKP/CKB/QM/QS text labels seen
    # in the GDS are internal-node labels only (no M1PIN/M2PIN marker
    # geometry backs them) -- confirmed by point-in-polygon match against
    # the 2 M1PIN (vdd/gnd) + 4 M2PIN (CK/D/QB/Q) marker polygons actually
    # present, so they are correctly excluded as non-ports here.
    "DFF": {
        "D":   ("INPUT",  "SIGNAL"),
        "CK":  ("INPUT",  "CLOCK"),
        "Q":   ("OUTPUT", "SIGNAL"),
        "QB":  ("OUTPUT", "SIGNAL"),
        **_PWR,
    },
    # DFFS: async-SET D flip-flop (V8, design_notes.md section 77.14 --
    # user added the physical layout to TR-1um_STDCELL.gds so V8's
    # walking-one bit_walk[7] register, which resets to 1, can map
    # directly to a real SET-type cell instead of needing DFFR + polarity
    # inversion, design_notes.md section 77.9). Same TG-based
    # master/slave structure as DFFRB/DFF, mirrored for an active-HIGH
    # SET instead of active-LOW RSTB (confirmed from the GDS text labels:
    # D/CK/SET/Q/QB M2PIN-backed ports, vdd/gnd M1PIN rail; CKP/CKB/QM/QS
    # are internal-node TXM1 labels only, same as DFF's own precedent,
    # correctly excluded since no M1PIN/M2PIN marker polygon backs them).
    "DFFS": {
        "D":   ("INPUT",  "SIGNAL"),
        "CK":  ("INPUT",  "CLOCK"),
        "SET": ("INPUT",  "SIGNAL"),
        "Q":   ("OUTPUT", "SIGNAL"),
        "QB":  ("OUTPUT", "SIGNAL"),
        **_PWR,
    },
    # RSLATCH (design_notes.md section 108.27/108.29): cross-coupled NOR2
    # SR latch, added by the user this session as a dedicated STDCELL
    # replacing the ad-hoc 2xNOR2 pattern used 3x in the current netlist
    # (sda_d/busy/rst_stretch latches). S/R inputs, Q/QB outputs -- no
    # clock, no reset pin (this is a level-sensitive latch, not an
    # edge-triggered flop). PR boundary 32.4um (6 tracks) x 64.8um,
    # on-grid, same row height as every other cell. NOTE: the GDS's M2PIN
    # text labels initially had two ports both marked "Q" (a mislabel --
    # the true 4th port, immediately left of R, is "S"); confirmed fixed
    # (S/R/Q/QB, 4 distinct labels) before generating this LEF entry.
    "RSLATCH": {
        "S":  ("INPUT",  "SIGNAL"),
        "R":  ("INPUT",  "SIGNAL"),
        "Q":  ("OUTPUT", "SIGNAL"),
        "QB": ("OUTPUT", "SIGNAL"),
        **_PWR,
    },
    # MUXDFFRB (design_notes.md section 108.27/108.29): MUX2 folded
    # directly into DFFRB's D input -- a load-enable flip-flop, added by
    # the user this session as a dedicated STDCELL replacing the 19
    # MUX2->DFFRB.D single-fanout sites found in the current netlist
    # (rx_data_0-7, txreg_0-7, phase_1, addr_match, rw). Pin set is
    # DFFRB's own (CK/RSTB/Q/QB, same direction/use as DFFRB) with the D
    # pin removed and replaced by the internal MUX2's A/B/S inputs (A/B
    # plain SIGNAL like any MUX2 input; S is the select, also SIGNAL --
    # matches MUX2's own PIN_META convention above). PR boundary 94.6um
    # (~17.5 tracks) x 64.8um, same row height as DFFRB. Internal CKP/CKB/
    # QM/QS TXM1 labels are DFFRB's usual internal master/slave-latch
    # node labels (no M1PIN/M2PIN marker backs them, so name_for_polygon
    # correctly never matches them to a port) -- same non-port precedent
    # already established for DFF/DFFS above.
    "MUXDFFRB": {
        "A":    ("INPUT",  "SIGNAL"),
        "B":    ("INPUT",  "SIGNAL"),
        "S":    ("INPUT",  "SIGNAL"),
        "CK":   ("INPUT",  "CLOCK"),
        "RSTB": ("INPUT",  "SIGNAL"),
        "Q":    ("OUTPUT", "SIGNAL"),
        "QB":   ("OUTPUT", "SIGNAL"),
        **_PWR,
    },
    "BUF_X1":   _gate_meta("A"),
    # BUFTH: hysteresis (Schmitt-trigger) buffer added this session by the
    # user directly in TR-1um_STDCELL.gds. Same single-input/single-output
    # pin shape as BUF_X1 (A in, Y out, plus VDD/GND) -- GDS confirms one
    # M2PIN each for A (x=6.4-9.8) and Y (x=28.0-31.4) at the same Y band
    # as every other combinational cell's signal pins, and the usual
    # split VDD/GND M1 rail pins. Physically wider than BUF_X1 (32.4um vs
    # 16.2um, prBoundary-derived) for the extra hysteresis transistors --
    # SIZE is read from the GDS directly, no manual entry needed here.
    "BUFTH":    _gate_meta("A"),
    # TAP2/TAP3 (section 35.3/35.9): dedicated power-tap filler cells, no
    # signal pins at all -- just VDD/GND, each with an M1 rail port plus
    # M2 strap-endcap ports at top and bottom (see the PIN-grouping note
    # in gen_macro_lef above).
    "TAP2": dict(_PWR),
    "TAP3": dict(_PWR),
    # FILL2/FILL3: wider filler cells (section 35 follow-up to the earlier
    # advice that a single fixed FILL1 width leaves leftover gaps -- see
    # the standard-cell-library critique discussion). VDD/GND M1 rail only,
    # no M2/via at all (pure density/rail-continuity filler, same role as
    # the existing FILL1).
    "FILL2": dict(_PWR),
    "FILL3": dict(_PWR),
}

# BUF_X2/X4/X16 are used by script/insert_buffers.py (section 18) as
# drive-strength variants, but no separate physical layout has been drawn
# for them -- the user is substituting BUF_X1's physical cell for all
# three until real drive-strength variants are drawn. Each alias below
# becomes its own LEF MACRO (so P&R can resolve the netlist's BUF_X2/X4/X16
# instance types), but its geometry/pins are read from BUF_X1's GDS cell
# and its FOREIGN statement points at BUF_X1 -- LEF's normal mechanism for
# "this macro name maps to that physical GDS structure". PIN_META is
# shared with BUF_X1 since the pin list is identical.
#
# NOTE: script/gen_liberty.py's COMB_CELLS table only defines BUF_X1 --
# BUF_X2/X4/X16 have no .lib timing entry. LEF aliasing alone is not
# enough for STA; either extend gen_liberty.py with matching entries (all
# pointing at the same placeholder BUF_X1 timing, since there is only one
# physical drive strength right now), or rename the netlist instances to
# BUF_X1 -- see design_notes.md section 35 follow-up note.
CELL_ALIASES = {
    "BUF_X2":  "BUF_X1",
    "BUF_X4":  "BUF_X1",
    "BUF_X16": "BUF_X1",
}
for _alias in CELL_ALIASES:
    PIN_META[_alias] = PIN_META["BUF_X1"]


def texts(cell, layer_idx):
    out = []
    it = cell.begin_shapes_rec(layer_idx)
    while not it.at_end():
        s = it.shape()
        if s.is_text():
            t = s.text
            out.append((t.string, t.x, t.y))  # dbu units
        it.next()
    return out


def region(cell, layer_idx):
    return db.Region(cell.begin_shapes_rec(layer_idx)).merged()


def fmt(v_dbu, dbu):
    return f"{v_dbu * dbu:.3f}".rstrip("0").rstrip(".") if "." in f"{v_dbu * dbu:.3f}" else f"{v_dbu * dbu:.3f}"


def gen_macro_lef(layout, cellname, indent="    ", gds_cellname=None):
    # gds_cellname lets a LEF MACRO (the name the netlist/P&R sees) be
    # backed by a *different* physical GDS cell (used for the BUF_X2/X4/X16
    # -> BUF_X1 aliasing above). Defaults to cellname when there's no alias.
    gds_cellname = gds_cellname or cellname
    dbu = layout.dbu
    cell = layout.cell(gds_cellname)
    if cell is None:
        raise SystemExit(f"cell {gds_cellname} not found in {GDS_PATH}")

    pr = region(cell, layout.layer(*PR_LAYER))
    bbox = pr.bbox()
    w = bbox.width() * dbu
    h = bbox.height() * dbu

    m1 = region(cell, layout.layer(*M1_LAYER))
    m2 = region(cell, layout.layer(*M2_LAYER))
    m1pin = region(cell, layout.layer(*M1PIN_LAYER))
    m2pin = region(cell, layout.layer(*M2PIN_LAYER))

    t1 = texts(cell, layout.layer(*TXM1_LAYER))
    t2 = texts(cell, layout.layer(*TXM2_LAYER))

    # GDS text labels for power pins are lower-case ("vdd"/"gnd", matching
    # this PDK's cell library convention -- see design_notes.md section 12:
    # "電源ピンラベルはGDS上vdd/gndと小文字（回路図/ネットリスト側はVDD/GND）"),
    # while the netlist/schematic side uses upper-case. Normalize here so the
    # LEF pin names match what the netlist expects.
    NAME_ALIASES = {"vdd": "VDD", "gnd": "GND"}

    def name_for_polygon(poly, text_list):
        for name, x, y in text_list:
            if poly.inside(db.Point(x, y)):
                return NAME_ALIASES.get(name, name)
        return None

    pins = []  # (name, layer_name, x0,y0,x1,y1) in um
    for poly in m1pin.each():
        name = name_for_polygon(poly, t1)
        b = poly.bbox()
        pins.append((name, "M1", b.left*dbu, b.bottom*dbu, b.right*dbu, b.top*dbu))
    for poly in m2pin.each():
        name = name_for_polygon(poly, t2)
        b = poly.bbox()
        pins.append((name, "M2", b.left*dbu, b.bottom*dbu, b.right*dbu, b.top*dbu))

    if any(p[0] is None for p in pins):
        raise SystemExit(f"{cellname}: could not match every pin marker to a text label: {pins}")

    obs_m1 = m1 - m1pin
    obs_m2 = m2 - m2pin

    meta = PIN_META.get(cellname)
    if meta is None:
        raise SystemExit(f"{cellname}: no PIN_META direction/use table defined -- add one before generating LEF")

    lines = []
    lines.append(f"MACRO {cellname}")
    lines.append(f"{indent}CLASS CORE ;")
    lines.append(f"{indent}FOREIGN {gds_cellname} 0.0 0.0 ;")
    lines.append(f"{indent}ORIGIN 0.0 0.0 ;")
    lines.append(f"{indent}SIZE {w:.3f} BY {h:.3f} ;")
    lines.append(f"{indent}SITE {SITE_NAME} ;")
    lines.append("")

    # Group fragments by pin name before emitting. A single logical pin can
    # be made of several disjoint marker polygons -- e.g. TAP2/TAP3's VDD
    # and GND each have one M1 rail rect *plus* one M2 strap-endcap rect at
    # each end the strap is contacted from (see design_notes.md section
    # 35.9: the M2 power strap runs the cell's full height so either an
    # above or below channel can tap it, with an endcap PIN marker at both
    # ends). LEF requires each PIN name to appear exactly once per MACRO;
    # multiple physical areas belong inside that one PIN's PORT as
    # additional LAYER/RECT groups, not as separate top-level PIN blocks.
    by_name = {}
    for name, layer_name, x0, y0, x1, y1 in pins:
        by_name.setdefault(name, []).append((layer_name, x0, y0, x1, y1))

    for name, frags in by_name.items():
        direction, use = meta[name]
        lines.append(f"{indent}PIN {name}")
        lines.append(f"{indent*2}DIRECTION {direction} ;")
        lines.append(f"{indent*2}USE {use} ;")
        lines.append(f"{indent*2}PORT")
        for layer_name, x0, y0, x1, y1 in frags:
            lines.append(f"{indent*3}LAYER {layer_name} ;")
            lines.append(f"{indent*4}RECT {x0:.3f} {y0:.3f} {x1:.3f} {y1:.3f} ;")
        lines.append(f"{indent*2}END")
        lines.append(f"{indent}END {name}")
        lines.append("")

    obs_lines = []
    for layer_name, obs_region in (("M1", obs_m1), ("M2", obs_m2)):
        if obs_region.count() == 0:
            continue
        obs_lines.append(f"{indent*2}LAYER {layer_name} ;")
        for poly in obs_region.each():
            pts = [(pt.x*dbu, pt.y*dbu) for pt in poly.each_point_hull()]
            pts_str = "  ".join(f"{x:.3f} {y:.3f}" for x, y in pts)
            obs_lines.append(f"{indent*3}POLYGON {pts_str} ;")
    if obs_lines:
        lines.append(f"{indent}OBS")
        lines.extend(obs_lines)
        lines.append(f"{indent}END")
        lines.append("")

    lines.append(f"END {cellname}")
    return "\n".join(lines)


def row_height_um(layout, cellname):
    dbu = layout.dbu
    cell = layout.cell(cellname)
    pr = region(cell, layout.layer(*PR_LAYER))
    return pr.bbox().height() * dbu


def main():
    layout = db.Layout()
    layout.read(GDS_PATH)

    site_height_um = row_height_um(layout, "INV_X1")

    body = []
    for cellname in PIN_META:
        gds_cellname = CELL_ALIASES.get(cellname, cellname)
        h = row_height_um(layout, gds_cellname)
        if abs(h - site_height_um) > 1e-6:
            raise SystemExit(
                f"{cellname}: row height {h} != {site_height_um} (SITE height) -- "
                f"all cells must share the same row-height grid (section 35.1)")
        body.append(gen_macro_lef(layout, cellname, gds_cellname=gds_cellname))

    header = f"""VERSION 5.8 ;
BUSBITCHARS "[]" ;
DIVIDERCHAR "/" ;
MANUFACTURINGGRID 0.05 ;

UNITS
    DATABASE MICRONS 1000 ;
END UNITS

SITE {SITE_NAME}
    CLASS CORE ;
    SIZE {SITE_WIDTH_UM:.3f} BY {site_height_um:.3f} ;
END {SITE_NAME}

"""

    footer = "\nEND LIBRARY\n"

    out = header + "\n".join(body) + footer
    with open(LEF_OUT, "w") as f:
        f.write(out)
    print(f"wrote {LEF_OUT}")
    print(out)


if __name__ == "__main__":
    main()
