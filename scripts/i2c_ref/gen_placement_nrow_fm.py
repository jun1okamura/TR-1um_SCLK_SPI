"""
gen_placement_nrow_fm.py

Section 38: N-row (target N=4, to fit the user's ~1700um row-width goal)
generalization of gen_placement_2row_fm.py. Changes from the 2-row FM
trial:

  1. Row assignment comes from fm_partition.fm_multiway_partition()
     (recursive bisection, N must be a power of 2) instead of a single
     bipartition -- see fm_partition.py's docstring for why recursive
     bisection was chosen over a direct k-way FM.
  2. FILL2/FILL3 cells are no longer packed as one block at the end of
     each TAP gap -- they're split into several smaller chunks and
     interspersed every INSERT_PERIOD real cells (`pack_row_distributed`
     replaces `pack_row`). This is a user request: spreading FILL columns
     across the row means their X positions -- guaranteed free of any
     real cell's M1/M2 (FILL2/3 carry only a VDD/GND M1 rail, section
     35.10) -- are available throughout the row as safe M2 jog corridors
     for the channel router (route_channels_nrow_fm.py), rather than
     being clumped in a few widely-spaced locations.
  3. (v2, user request) Fixed row width TARGET_ROW_WIDTH_UM = 5.4*300 =
     1620um (was a computed 1436.4um). The user observed spanning nets
     jogging outside the actual standard-cell area on the left edge --
     traced to route_channels_nrow_fm.py's find_row_clear_x search
     wandering to negative X when the area right around a pin's own
     position was too crowded (a real bug there, fixed separately), but
     also motivated by wanting more legitimate on-grid FILL real estate
     near the row edges for that search to land on instead. Two related
     changes here:
       a. FILL insertion now includes position 0 of every gap (right
          after the TAP cell, before the first real cell) -- previously
          FILL only ever appeared *between or after* groups of real
          cells, never at a gap's leading edge.
       b. (superseded by v3, point 4 below) Originally each row got a
          small row-specific extra leading FILL block (STAGGER_TRACKS)
          to shift its cell sequence by a row-specific offset. Kept
          only in git history now.

  4. (v3, user request) Left/right anchored packing by row parity, to
     directly attack the "case 2" short pattern (design_notes 37.4,
     38.5, 38.6) at its structural source rather than just perturbing
     it. Root-caused with the user in this session: row-only/
     adjacent-pair nets' straight M2 stubs are only ever checked for
     collision against their OWN net/channel bookkeeping, never against
     OTHER nets -- so two nets in DIFFERENT rows that happen to share
     both an absolute pin X (common on a shared 5.4um grid with ~50%
     occupancy) AND a channel (i.e. the rows are adjacent -- row r and
     row r+1 always share channel r+1) will have their stubs physically
     merge into one polygon, an invisible-to-DRC short.

     Measured before this change: adjacent (channel-sharing) row pairs
     had just as much X overlap as non-adjacent pairs (e.g. row0/row1:
     99/124 shared X, vs row0/row2 non-adjacent: 104 shared X) --
     confirming the overlap is essentially the statistical consequence
     of independent ~50%-dense placements on a shared 300-slot grid,
     not a systematic pattern the old leading-stagger could meaningfully
     touch.

     Fix: since TAP column X positions are fixed by construction
     (TAP_INTERVAL_TRACKS is constant per gap regardless of gap
     content), each of the N_GAPS gaps has a fixed [TAP_i, TAP_(i+1)]
     span for every row. Within each gap, EVEN rows (0, 2, ...) now
     pack all their real cells contiguously right after the leading TAP
     ("anchor=left"), with all of that gap's slack FILL trailing before
     the next TAP; ODD rows (1, 3, ...) do the mirror image
     ("anchor=right"): slack FILL leads, real cells trail contiguously
     up to the next TAP. Adjacent rows always differ in parity, so this
     puts their real-cell (and hence real-pin) X ranges at opposite
     ends of every gap. Per gap, if the two rows' real-cell demand in
     that gap sums to <= TAP_INTERVAL_TRACKS, their real-pin X sets
     become provably disjoint (no shared X is even possible, regardless
     of internal cell content) -- eliminating case 2 between that row
     pair for that gap entirely, not just reducing its odds. Where
     combined demand exceeds the gap budget, only the excess is forced
     to overlap in the middle.

     This replaces the old `insert_period`-based multi-point FILL
     distribution (spreading small FILL chunks every 6 real cells) with
     one contiguous FILL block per gap -- trading the "FILL corridors
     scattered throughout the row" property (§38.1) for the stronger
     interval-separation guarantee, since the router's row-crossing
     search (ROW_X_TRIES=200, i.e. +-1080um) has ample range to reach a
     large contiguous FILL block instead of a nearby small one. The
     user considered and declined per-cell X-mirroring as an additional
     measure (design_notes 38.7): plain interval separation already
     gives the provable guarantee for gaps where it applies, and
     per-cell mirroring has a documented history of causing new,
     unrelated DRC/short problems elsewhere (design_notes 22.6)
     for no proven benefit on top of that guarantee.

Output schema changes from the 2-row trial's {"row1":[...],"row2":[...]}
to {"rows": [[...row0 items...], [...row1...], ...]} (a list, so N is not
hardcoded in the consumer scripts).
"""
import json
import sys
from collections import deque

sys.path.insert(0, "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/script")
from lef_parser import parse_lef  # noqa: E402
from netlist_parser import parse_netlist  # noqa: E402
from fm_partition import fm_multiway_partition, classify_multirow_nets  # noqa: E402
from gen_placement_2row import fill_combo, TRACK_UM, TAP_CELL  # noqa: E402

N_ROWS = 4
OUT_JSON = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/LEF/placement_nrow_fm.json"

# Fixed row width target (user request, v2-v7): 5.4um x 300 grid steps
# (1620.0um), derived by solving for TAP_INTERVAL_TRACKS given N_GAPS.
#
# V8 (design_notes.md section 77.6/77.11, user request): TAP columns
# increased from 3 to 5 (N_GAPS 2->4) to strengthen the power mesh -- more,
# closer-spaced VDD/GND straps across the row. Was N_GAPS=2 (3 TAP
# columns) from the 1436.4um version through v7. Unlike N_GAPS=2 (which
# divides 1620um evenly: TAP_INTERVAL_TRACKS=147 exactly), N_GAPS=4 does
# NOT divide 1620um evenly ((1620 - 5*10.8)/4/5.4 = 72.5, not an integer)
# -- so for v8 this is inverted: TAP_INTERVAL_TRACKS is now the fixed
# integer input and TARGET_ROW_WIDTH_UM is DERIVED from it, landing very
# close to (but not exactly) the original 1620um/300-track target.
# v9 (design_notes.md section 77.33-77.40, user request): N_GAPS=3
# (4 TAP columns), unequal per-gap split [97,97,98] tracks -- hits
# TARGET_ROW_WIDTH_UM=1620.0um exactly (see _tap_tracks's docstring).
# This is the currently-adopted configuration; v8's N_GAPS=4/
# TAP_INTERVAL_TRACKS=115 (row width 2538.0um) values are kept only in
# git history/this comment for provenance -- restore them if a v8-style
# run is ever needed again.
N_GAPS = 3  # 4 TAP columns
TAP_WIDTH_UM = 10.8  # TAP2's own width
TAP_INTERVAL_TRACKS = [97, 97, 98]
def _tap_tracks(gap_i):
    """TAP_INTERVAL_TRACKS may be a single int (uniform gap width, the
    original v2-v8 behavior -- required whenever N_GAPS divides
    1620um/300-tracks evenly, e.g. N_GAPS=2 -> 147 tracks/gap exactly)
    or a per-gap list/tuple of length N_GAPS (design_notes.md section
    77.33/77.34: for N_GAPS values that do NOT divide 300 tracks evenly
    -- e.g. N_GAPS=3 -> 292 total gap-tracks needed, not divisible by 3;
    N_GAPS=4 -> 290 total, not divisible by 4 -- a uniform per-gap
    value can only ever get CLOSE to TARGET_ROW_WIDTH_UM, never exactly
    equal to it. Splitting the (always-integer, since TAP_WIDTH_UM is
    itself a whole number of tracks) total gap-track budget UNEVENLY
    across gaps hits the target exactly instead, since fill_combo()
    (gen_placement_2row.FILL2/FILL3) can absorb any non-1 per-gap slack
    regardless of how that slack varies gap-to-gap. TAP column X
    positions stay identical across every ROW either way (uniform or
    per-gap list) because every row consumes the SAME gap-track
    sequence -- only different GAPS may now differ from each other,
    never the same gap between two different rows -- so the TAP power
    mesh straps (route_channels_nrow_fm.py) still line up vertically
    exactly as before; nothing about that alignment depended on all
    gaps being equal to EACH OTHER, only on each gap being equal across
    rows, which is preserved."""
    if isinstance(TAP_INTERVAL_TRACKS, (list, tuple)):
        assert len(TAP_INTERVAL_TRACKS) == N_GAPS, (
            f"TAP_INTERVAL_TRACKS list has {len(TAP_INTERVAL_TRACKS)} entries, "
            f"expected N_GAPS={N_GAPS}")
        return TAP_INTERVAL_TRACKS[gap_i]
    return TAP_INTERVAL_TRACKS


def _total_gap_tracks():
    if isinstance(TAP_INTERVAL_TRACKS, (list, tuple)):
        return sum(TAP_INTERVAL_TRACKS)
    return N_GAPS * TAP_INTERVAL_TRACKS


TARGET_ROW_WIDTH_UM = (N_GAPS + 1) * TAP_WIDTH_UM + _total_gap_tracks() * TRACK_UM
assert abs((N_GAPS + 1) * TAP_WIDTH_UM + _total_gap_tracks() * TRACK_UM - TARGET_ROW_WIDTH_UM) < 1e-6

# Priority M2 corridor width (user request, this session -- see the
# pack_row_distributed module comment below for the full rationale):
# one FILL2 (2 tracks) reserved at BOTH edges of every gap, in every
# row, unconditionally. _gaps_needed's dry-run budget is reduced by
# the same amount so its "does N_GAPS suffice" check stays honest.
PRIORITY_FILL_TRACKS = 2  # FILL2 = 10.8um = 2 tracks

# Targeted X-decoupling nudges (design_notes.md section 77.40, Task
# #20, user request): {instance_name: extra_tracks}. Splits that
# instance's gap's anchor-side slack FILL budget into two pieces --
# `extra_tracks` worth is inserted immediately BEFORE the named
# instance (shifting it, and every real cell packed after it within
# the same gap, to the right by extra_tracks*TRACK_UM), and the
# remaining slack is placed at the gap's normal anchor position same
# as before. Gap width (and therefore every TAP column's X, and hence
# the cross-row power mesh alignment) is completely unaffected -- only
# the split point of the SAME total slack within that one gap moves.
#
# v9 remaining short (scl_row0 <-> _051_, design_notes.md 77.37-77.41):
# root cause, FINAL (77.41, after two earlier, superseded explanations
# in 77.37/77.39 -- both corrected following the user's own review of
# the routed GDS in KLayout, which is what eventually surfaced the real
# mechanism below): row0's "_276_" (a DFFRB whose CK pin is driven by
# scl_row0) and row1's "_176_" (a MUX2 whose Y pin drives _051_) land
# their pins at nearly the same absolute X (~1223.1) by coincidence of
# the FM-partition placement + each cell's own internal pin offset.
# scl_row0 is a genuinely 3-row-spanning net (pins in rows 0, 1, 3 --
# confirmed via pin_map cross-referenced against each pin's own
# instance's row in placement_nrow_fm_v9.json), so it is drawn by pass 2
# (spanning nets), which runs AFTER pass 1 (row-only/adjacent-pair,
# where the simple 2-pin "_051_" is drawn). By the time scl_row0's row0
# pin is processed, "_051_" already occupies column X=1223.1 in channel
# 1 -- route_channels_nrow_fm.py's own live collision check (pass 2's
# final-run channel_clear, around line 2143) correctly DETECTS this and
# tries to escape via find_channel_clear_x + draw_jog, exactly the
# mechanism point 7's module docstring describes. The escape itself is
# what fails: draw_jog's departure-leg search (near the pin's own Y)
# can't find ANY nearby track with a clear vertical exit at x=1223.1,
# nor is a direct M1 bridge to the far clear_x it found (x=585.9) safe
# either -- so it falls through to draw_jog's last-resort fallback
# (route_channels_nrow_fm.py ~line 1605), which explicitly logs
# "may leave a short to investigate" and draws anyway. This is a
# genuine, self-documented capacity/search limitation in the router's
# jog logic for this specific spot, not a modeling bug like the two
# Fixer issues found earlier this session (design_notes.md 77.38) --
# confirmed by reproducing the exact same warning message, at the exact
# same (x, y), from a clean re-route of the unmodified v9 placement.
#
# Fix: rather than teach draw_jog a smarter escape (bigger change, more
# risk elsewhere -- see the v27/v28/v32/v32b history in
# route_channels_nrow_fm.py for how delicate that logic already is),
# remove the X coincidence that triggers the collision check in the
# first place, at the placement level -- "_276_" sits in row0's gap 2
# (X=[1080.0,1609.2]), which has ~264.6um (49 tracks) of trailing
# FILL3/FILL2 slack after its real-cell block ends, ample budget to
# nudge from.
#
# Every nudge also drags "u_buf_sda_in_row0" (the next real cell in the
# same gap) forward by the same amount, which can create NEW coincidental
# X collisions of its own -- confirmed empirically by sweeping the nudge
# amount and re-running the full placement+route+auto-fix pipeline for
# each: 20 tracks -> 2 new unresolved shorts elsewhere; 14 -> 4; 12 -> 4;
# 11 -> 3; 10 -> 1 new unresolved short; 8 -> 3; 9 tracks -> ZERO
# warnings from draw_jog, 0 DRC violations, and only 2 conflicts (both
# ordinary M1-trunk cases the existing Fixer already resolves
# automatically) -- verified via drc_check_nrow_fm.py +
# verify_connectivity_nrow_fm.py: "ALL NETS FULLY CONNECTED, NO SHORTS
# DETECTED". This is a placement-level search problem (which nudge
# amount avoids every other net's own column), not something to solve
# analytically -- 9 tracks is an empirically-found value for the CURRENT
# v9 netlist/placement and would need re-sweeping if the netlist changes.
NUDGE_BEFORE = {"_276_": 9}  # 9 tracks = 48.6um -- see comment above



def _gaps_needed(cell_queue, widths):
    """Local copy of gen_placement_2row.py's dry-run gap counter, using
    THIS file's TAP_INTERVAL_TRACKS (that function reads its own module's
    global, so importing it directly would silently use 111 tracks, not
    130 -- see the note above).

    When TAP_INTERVAL_TRACKS is a per-gap list (see _tap_tracks()), this
    dry run doesn't know in advance which gap index each bucket will
    become, so it conservatively uses the SMALLEST gap's budget for
    every bucket -- guaranteed to never UNDER-count the number of gaps
    actually needed (it may over-count by at most a little if gaps are
    very uneven, which only makes the N_GAPS<=... assert in main() a
    bit more conservative, never unsafe)."""
    min_gap_tracks = (min(TAP_INTERVAL_TRACKS) if isinstance(TAP_INTERVAL_TRACKS, (list, tuple))
                       else TAP_INTERVAL_TRACKS)
    q = deque(cell_queue)
    n_gaps = 0
    while q:
        gap_tracks_left = min_gap_tracks - 2 * PRIORITY_FILL_TRACKS
        seg = []
        while q:
            typ, name, pins = q[0]
            w_tracks = round(widths[typ] / TRACK_UM)
            if w_tracks > gap_tracks_left:
                break
            seg.append(q.popleft())
            gap_tracks_left -= w_tracks
        if not seg:
            raise SystemExit("a single cell is wider than one TAP gap -- widen TAP_INTERVAL_TRACKS")
        if gap_tracks_left == 1:
            typ, name, pins = seg.pop()
            q.appendleft((typ, name, pins))
        n_gaps += 1
    return n_gaps


def split_fill_evenly(total_tracks, n_parts):
    """Split total_tracks into n_parts chunks, each individually fillable
    by fill_combo (0 or >=2 -- never exactly 1), as evenly as possible.

    PRIOR VERSION (buggy): built an n_parts-way even split first (which,
    whenever total_tracks < 2*n_parts, necessarily contains several 1's),
    then tried to fix each 1 by borrowing from a neighbor in a single
    forward pass. That pass could shove a 1 rightward past an
    already-visited index and never revisit it -- confirmed in practice
    (3 tracks / 7 parts produced [0,2,0,0,0,1,0], and since fill_combo(1)
    is None, that track silently vanished, shrinking the row's total
    width by one track without any error). Fixed by only ever using as
    many active (non-zero) slots as total_tracks can support at >=2
    each -- constructed directly, so a 1 can never appear in the first
    place instead of being patched up after the fact."""
    n_parts = max(1, n_parts)
    if total_tracks <= 0:
        return [0] * n_parts
    assert total_tracks != 1, "cannot split exactly 1 track across FILL2/3 combos"
    active = max(1, min(n_parts, total_tracks // 2))
    base, rem = divmod(total_tracks, active)
    parts = [base + (1 if i < rem else 0) for i in range(active)]
    parts += [0] * (n_parts - active)
    return parts


# Priority M2 corridor (user request, this session): a FILL2-only
# (no real-cell M1/M2 obstruction) vertical strip immediately touching
# every TAP column, present in EVERY row at an X position fixed by the
# TAP grid alone (independent of row content/anchor) -- so a straight
# M2 wire can cross ALL n_rows+1 channels through this X without ever
# passing through a real standard cell.
#
# Per row, there are n_gaps+1 TAP columns: TAP_0 (row's left edge),
# TAP_1..TAP_(n_gaps-1) (interior), TAP_n_gaps (row's right edge). At
# each TAP's "inside" neighbor(s) (the side(s) that have gap content
# next to them -- an edge TAP only has one, an interior TAP has two,
# i.e. "真ん中は両隣、端は内側に") we unconditionally reserve one
# FILL2 (PRIORITY_FILL_TRACKS tracks) BEFORE doing the normal
# anchor-based real-cell/slack-FILL packing for the rest of that gap.
# This costs 2*PRIORITY_FILL_TRACKS tracks off every gap's budget
# (one reserved FILL2 at the gap's leading edge, right after the
# left TAP; one at its trailing edge, right before the right TAP),
# same in every row, so it doesn't disturb the TAP grid itself.
#
# FILL2 is 10.8um (2 tracks) wide; with n_gaps=2 there are 4 such
# reserved corridors per row (TAP_0-right, TAP_1-left, TAP_1-right,
# TAP_2-left) -- each wide enough for ~2 side-by-side M2 wires at the
# router's track pitch, giving 8 total straight-through M2 lanes
# (user's target), reserved for the scl_buf/sda_in_buf per-row-local
# spine nets that need to cross every channel without deviation.
PRIORITY_FILL_CELL = "FILL2"


def pack_row_distributed(cell_queue, widths, n_gaps, anchor="left",
                          priority_fill_tracks=0, priority_fill_cell=PRIORITY_FILL_CELL,
                          nudge_before=None):
    """Pack a row's cells into n_gaps TAP-bounded gaps of fixed size
    (TAP_INTERVAL_TRACKS each, so TAP column X positions are identical
    for every row regardless of content -- required for the TAP power
    mesh straps in route_channels_nrow_fm.py to line up across rows).

    anchor="left": within every gap, real cells are packed contiguously
    right after the leading TAP cell; all of that gap's slack FILL2/3
    trails afterward, right up to the next TAP.
    anchor="right": the mirror image -- slack FILL leads (right after
    the TAP), real cells are packed contiguously up to the next TAP.

    Calling this with anchor="left" for even rows and anchor="right"
    for odd rows (see main()) is this module's v3 case-2 mitigation --
    see the module docstring, point 4, for the full rationale.

    priority_fill_tracks: if >0, reserve this many tracks of
    `priority_fill_cell` at BOTH the leading and trailing edge of
    EVERY gap (see module comment above the function) -- placed before
    the anchor-based real-cell/slack-FILL packing, and NOT part of
    that packing's own slack-FILL budget (i.e. real cells only ever
    compete for the tracks left over after this reservation).

    nudge_before: optional {instance_name: extra_tracks} (module-level
    NUDGE_BEFORE, Task #20/design_notes.md 77.40) -- for any real cell
    matching a key here, `extra_tracks` of FILL is inserted immediately
    before it within its gap's contiguous real-cell block, funded by
    reducing that SAME gap's own anchor-slack by the same amount (so
    total gap width, and every TAP column's X, is unchanged -- only
    where inside the gap the slack/real-cell split falls moves)."""
    nudge_before = nudge_before or {}
    placed = []
    x = 0.0
    tap_idx = 0
    fill_idx = 0
    pri_idx = 0

    def place(typ, name, w, pins=None):
        nonlocal x
        placed.append({"name": name, "type": typ, "x": x, "width": w, "pins": pins or {}})
        x += w

    def place_fill_combo(tracks):
        nonlocal fill_idx
        combo = fill_combo(tracks)
        assert combo is not None, (
            f"fill_combo({tracks}) is None -- an unfillable amount was requested; "
            f"this must never happen (see split_fill_evenly's docstring for the "
            f"bug this guards against)")
        for fill_typ in combo:
            place(fill_typ, f"FILL_{fill_idx}", widths[fill_typ])
            fill_idx += 1

    def place_priority_fill():
        nonlocal pri_idx
        if priority_fill_tracks <= 0:
            return
        w = widths[priority_fill_cell]
        assert round(w / TRACK_UM) == priority_fill_tracks, (
            f"{priority_fill_cell} width {w}um is {round(w / TRACK_UM)} tracks, "
            f"not the requested priority_fill_tracks={priority_fill_tracks}")
        place(priority_fill_cell, f"FILLPRI_{pri_idx}", w)
        pri_idx += 1

    # Consume the cell queue gap-by-gap, but in an order that fills the
    # anchor-preferred END OF THE WHOLE ROW first: left anchor consumes
    # gap 0, 1, ... (as before); right anchor consumes the LAST gap
    # first, spilling overflow backward into earlier gaps. Consuming
    # gap-by-gap in row order (0, 1, ...) regardless of anchor would
    # always saturate gap 0 first (whichever end it's anchored to
    # within that gap) and leave gap 1+ nearly empty -- wasting the
    # far gap's capacity and defeating the whole-row interval
    # separation this is meant to provide (measured: this bug made the
    # first version of this function barely move the needle -- see
    # design_notes 38.7).
    gap_order = list(range(n_gaps)) if anchor == "left" else list(reversed(range(n_gaps)))
    gap_segs = {}
    gap_slack = {}
    for gap_i in gap_order:
        gap_budget = _tap_tracks(gap_i) - 2 * priority_fill_tracks
        assert gap_budget > 0, (
            f"priority_fill_tracks={priority_fill_tracks} (x2, both gap edges) leaves no "
            f"budget out of gap {gap_i}'s TAP_INTERVAL_TRACKS={_tap_tracks(gap_i)} for real cells")
        gap_tracks_left = gap_budget
        seg = []
        while cell_queue:
            typ, name, pins = cell_queue[0]
            w_tracks = round(widths[typ] / TRACK_UM)
            if w_tracks > gap_tracks_left:
                break
            seg.append(cell_queue.popleft())
            gap_tracks_left -= w_tracks

        if gap_tracks_left == 1 and seg:
            typ, name, pins = seg.pop()
            cell_queue.appendleft((typ, name, pins))
            gap_tracks_left += round(widths[typ] / TRACK_UM)

        gap_segs[gap_i] = seg
        gap_slack[gap_i] = gap_tracks_left

    # Emit in true left-to-right gap order regardless of consumption
    # order above, so TAP columns stay at their fixed row-wide X
    # positions.
    for gap_i in range(n_gaps):
        place(TAP_CELL, f"TAP_{tap_idx}", widths[TAP_CELL])
        tap_idx += 1
        place_priority_fill()  # reserved corridor, gap's leading edge

        seg = gap_segs[gap_i]
        slack = gap_slack[gap_i]
        nudge_total = sum(nudge_before.get(name, 0) for _typ, name, _pins in seg)
        assert nudge_total <= slack, (
            f"gap {gap_i}: nudge_before requests {nudge_total} tracks but only "
            f"{slack} tracks of slack are available in this gap")
        anchor_slack = slack - nudge_total
        assert anchor_slack != 1, (
            f"gap {gap_i}: nudge_before leaves exactly 1 track of anchor slack, "
            f"which fill_combo cannot fill -- adjust nudge_before's amount by +-1")
        if anchor == "right" and anchor_slack > 0:
            place_fill_combo(anchor_slack)
        for typ, name, pins in seg:
            extra = nudge_before.get(name, 0)
            if extra:
                place_fill_combo(extra)
            place(typ, name, widths[typ], pins)
        if anchor == "left" and anchor_slack > 0:
            place_fill_combo(anchor_slack)

        place_priority_fill()  # reserved corridor, gap's trailing edge

    place(TAP_CELL, f"TAP_{tap_idx}", widths[TAP_CELL])
    assert not cell_queue, f"{len(cell_queue)} cells left over -- n_gaps too small for this row"

    return placed, x


def main(net_file=None, out_json=OUT_JSON, part_json=None, reorder_row_tail=None):
    """net_file: override netlist_parser's default NET_PATH (section 40 --
    used to point this at i2c_slave_async_net_v4.v instead of the
    canonical i2c_slave_async_net.v, without touching netlist_parser.py's
    own default used elsewhere).

    part_json: path to a precomputed {instance_name: row_index} JSON
    (written by insert_row_buffers.py's ROW_ASSIGNMENT_JSON) -- if given,
    SKIPS fm_multiway_partition entirely and uses this mapping as-is.
    See insert_row_buffers.py's docstring/comment for why: re-running the
    recursive-bisection FM partition on a netlist that only differs by a
    handful of newly-inserted buffer instances was found to reshuffle the
    row assignment of many unrelated, unchanged instances (section 40),
    defeating row-aware buffer insertion's whole purpose.

    reorder_row_tail (108.33, V10): optional {row_idx: [instance_name,
    ...]} -- moves the named instances (which must already be assigned
    to that row) to the END of that row's cell queue, in the given
    order, before gap-filling. _gaps_needed/pack_row_distributed fill
    each row's TAP-bounded gaps strictly SEQUENTIALLY in queue order
    (gap0 until full, then gap1, ...), so several large same-width cells
    that land consecutively in the row's natural (netlist-declaration)
    order all pack into the SAME gap with zero slack between them --
    confirmed on V10's row2, where 5 consecutive 97.2um MUXDFFRB cells
    (all from the same DFF_GROUPS cluster, sclN_txreg) filled gap1
    completely (502.2um budget, 502.2um used, exactly 0 slack), leaving
    no FILL-only crossing point anywhere in a contiguous 486um span --
    route_channels_nrow_fm.py's row/channel clearance search then had no
    valid X at all near that span, a hard routing failure NUDGE_BEFORE
    can't fix (nudging needs slack in the SAME gap, and there was none).
    Moving 1-2 of those cells to the row's queue tail pushes them into a
    LATER gap that still has slack, freeing room in the original gap for
    a natural FILL break -- a bin-packing-level fix, not a placement
    nudge."""
    macros = parse_lef()
    net = parse_netlist(path=net_file) if net_file else parse_netlist()
    instances = net["instances"]

    row_h = macros["INV_X1"]["size"][1]
    widths = {name: m["size"][0] for name, m in macros.items()}
    for name, m in macros.items():
        assert m["size"][1] == row_h, f"{name} height {m['size'][1]} != {row_h}"

    if part_json:
        with open(part_json) as f:
            part = json.load(f)
        missing = [name for _t, name, _p in instances if name not in part]
        assert not missing, f"part_json missing rows for instances: {missing[:10]}"
        print(f"using precomputed row assignment from {part_json} ({len(part)} instances)")
    else:
        part = fm_multiway_partition(instances, widths, N_ROWS)
    counts = classify_multirow_nets(instances, part, N_ROWS)
    print(f"net classification: row-only={counts['row_only']}, "
          f"adjacent-pair={counts['adjacent_pair']}, spanning(3+ or non-adjacent)={counts['spanning']}")

    rows_cells = [[] for _ in range(N_ROWS)]
    for typ, name, pins in instances:
        rows_cells[part[name]].append((typ, name, pins))

    if reorder_row_tail:
        for r, tail_names in reorder_row_tail.items():
            tail_set = set(tail_names)
            missing = tail_set - {n for _t, n, _p in rows_cells[r]}
            assert not missing, f"reorder_row_tail: {missing} not assigned to row{r}"
            by_name = {n: (t, n, p) for t, n, p in rows_cells[r]}
            head = [item for item in rows_cells[r] if item[1] not in tail_set]
            tail = [by_name[n] for n in tail_names]
            rows_cells[r] = head + tail
            print(f"reorder_row_tail: moved {len(tail_names)} instance(s) to the end of "
                  f"row{r}'s queue: {tail_names}")

    for r, cells in enumerate(rows_cells):
        w = sum(widths[t] for t, _n, _p in cells)
        print(f"row{r}: {len(cells)} cells, natural width {w:.1f} um")

    n_gaps = max(_gaps_needed(cells, widths) for cells in rows_cells)
    assert n_gaps <= N_GAPS, (
        f"row content needs {n_gaps} gaps but TAP_INTERVAL_TRACKS was derived for "
        f"N_GAPS={N_GAPS} to hit TARGET_ROW_WIDTH_UM={TARGET_ROW_WIDTH_UM} -- "
        f"either raise TARGET_ROW_WIDTH_UM or raise N_GAPS")
    placed_rows = []
    widths_out = []
    for r, cells in enumerate(rows_cells):
        anchor = "left" if r % 2 == 0 else "right"
        placed, w = pack_row_distributed(deque(cells), widths, N_GAPS, anchor=anchor,
                                          priority_fill_tracks=PRIORITY_FILL_TRACKS,
                                          nudge_before=NUDGE_BEFORE)
        placed_rows.append(placed)
        widths_out.append(w)
    for r, w in enumerate(widths_out):
        assert abs(w - widths_out[0]) < 1e-6, f"row{r} width {w} != row0 width {widths_out[0]}"
        assert abs(w - TARGET_ROW_WIDTH_UM) < 1e-6, f"row{r} width {w} != target {TARGET_ROW_WIDTH_UM}"
    row_width = widths_out[0]

    # LEF pin name -> netlist/.lib pin name, for cells where the GDS's own
    # text-label marker (which gen_lef.py uses verbatim as the LEF pin
    # name) diverges from the liberty/synthesis pin name. Currently only
    # DFFR: the GDS marks the reset pin "RB" (matching the physical
    # active-low reset-bar convention) but TR1um_5_stdcell.lib/the
    # synthesized netlist use "RSTB" (section 39.2's rename). Without this
    # alias, pinmap.get("RB") always misses (the netlist has no "RB" key),
    # so every DFFR's reset pin was silently dropped from every placement
    # JSON built before this fix -- confirmed missing from
    # pin_map_nrow_fm_v4.json (0 entries for any DFFR-RSTB net), meaning
    # the async reset network was never actually routed in any prior GDS
    # despite 0 DRC / 0 shorts (the connectivity checker only checks pins
    # present in pin_map, so an entirely-missing pin was never examined,
    # not correctly verified).
    PIN_NAME_ALIASES = {"DFFRB": {"RB": "RSTB"}}

    def attach_pins(placed_list, cell_list):
        by_name = {name: pins for _typ, name, pins in cell_list}
        for item in placed_list:
            if item["type"] in (TAP_CELL, "FILL2", "FILL3"):
                item["pins"] = {}
                continue
            pinmap = by_name[item["name"]]
            lef_pins = macros[item["type"]]["pins"]
            aliases = PIN_NAME_ALIASES.get(item["type"], {})
            resolved = {}
            for pname, pinfo in lef_pins.items():
                net_name = pinmap.get(aliases.get(pname, pname))
                if net_name is None:
                    continue
                resolved[pname] = {"net": net_name, "direction": pinfo["direction"],
                                    "use": pinfo["use"], "rects": pinfo["rects"]}
            item["pins"] = resolved
        return placed_list

    def with_pins(placed, row_idx):
        out = []
        for item in placed:
            pins_abs = {}
            for pname, pinfo in item["pins"].items():
                rects = [(layer, item["x"] + x0, y0, item["x"] + x1, y1)
                         for layer, x0, y0, x1, y1 in pinfo["rects"]]
                pins_abs[pname] = {"net": pinfo["net"], "direction": pinfo["direction"],
                                    "use": pinfo["use"], "rects": rects}
            out.append({"name": item["name"], "type": item["type"], "row": row_idx,
                        "x": item["x"], "width": item["width"], "height": row_h,
                        "pins": pins_abs})
        return out

    rows_out = []
    for r, (placed, cells) in enumerate(zip(placed_rows, rows_cells)):
        placed = attach_pins(placed, cells)
        rows_out.append(with_pins(placed, r))

    result = {"row_height": row_h, "row_width": row_width, "n_rows": N_ROWS, "rows": rows_out}
    with open(out_json, "w") as f:
        json.dump(result, f, indent=1)

    print(f"wrote {out_json}")
    print(f"row width (all rows, by construction) = {row_width:.1f} um")
    for r, placed in enumerate(rows_out):
        n_tap = sum(1 for p in placed if p["type"] == TAP_CELL)
        n_fill = sum(1 for p in placed if p["type"] in ("FILL2", "FILL3"))
        n_real = len(placed) - n_tap - n_fill
        print(f"row{r}: {len(placed)} placed ({n_real} cells + {n_tap} TAP2 + {n_fill} FILL, "
              f"in {n_fill} FILL instance(s) spread across the row)")


if __name__ == "__main__":
    # optional CLI overrides (section 40): net_file out_json part_json
    _args = sys.argv[1:]
    _net_file = _args[0] if len(_args) > 0 and _args[0] != "-" else None
    _out_json = _args[1] if len(_args) > 1 and _args[1] != "-" else OUT_JSON
    _part_json = _args[2] if len(_args) > 2 and _args[2] != "-" else None
    main(net_file=_net_file, out_json=_out_json, part_json=_part_json)
