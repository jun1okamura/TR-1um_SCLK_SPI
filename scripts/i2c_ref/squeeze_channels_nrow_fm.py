"""
squeeze_channels_nrow_fm.py (section 44, user request "既存配線はそのままに、
未使用のM1チャネル分のY軸を圧縮できますか")

A POST-ROUTE geometric compaction pass: unlike compress_channels_nrow_fm.py
(section 41, which re-runs placement+routing at a smaller CH_HEIGHTS), this
script takes an ALREADY-ROUTED GDS and physically removes genuinely-unused
Y-slices from inside each channel, shifting everything above back down --
without re-deriving or re-drawing a single net's path.

Why this is safe (and where the real risk is):
  Every track index's physical Y is currently ch_y0[c] + TRACK0_OFFSET +
  idx*TRACK_PITCH -- a uniform, evenly-spaced grid. Removing an index that
  NOTHING claims (neither a real net's via/trunk, via `channel_used_x`, nor
  a HIGH_FO_GUARD_TRACKS/PER_ROW_GUARD_TRACKS guard slot, via `guard_idx`,
  both dumped by route_channels_nrow_fm.py's `compaction_info_path`) and
  compacting the remaining indices down is, in isolation, just deleting
  empty space -- nothing physically there needs to move in X, only in Y,
  and every M1/M2 box's Y-extent transforms independently and correctly
  under a monotonic piecewise map (see `build_y_map` below).

  The one real hazard: closing a gap between two indices that were NOT
  adjacent in the original numbering makes them adjacent in the compacted
  one. Every original claim was only ever safety-checked (via `collides()`,
  MIN_VIA_X_SEP) against its ORIGINAL immediate neighbors at claim time --
  never against indices further away. So before finalizing the compaction,
  this script re-validates every NEWLY-adjacent pair that both have real
  via data (`used_x`) and backs off (leaves exactly one spacer index) where
  MIN_VIA_X_SEP would be violated. A guard slot never needs this check on
  either side: it has no via of its own, and it always stays immediately
  next to the exact net it was reserved for regardless of what gets
  compacted further away -- so the guard's whole protective purpose (never
  let ANY via within one track pitch of a wide trunk) survives compaction
  unconditionally as long as the guard index itself is never removed.

Usage: see main() at the bottom for the exact v6 invocation.
"""
import json
import sys

import klayout.db as db

TOP_CELL_NAME = "i2c_slave_async_nrow_fm"
M1_LAYER = (13, 0)
M2_LAYER = (20, 0)
V1_LAYER = (19, 0)


def compute_kept_indices(budget, guard_idx, used_x, min_via_x_sep):
    """Returns the sorted list of ORIGINAL indices to keep for one channel
    (everything else is squeezed out). `used_x`: {str(idx): [cx, ...]}."""
    guard_set = set(guard_idx)
    used_set = {int(k) for k in used_x}
    protected = sorted(guard_set | used_set)

    kept = []
    last_idx = None
    last_via = None  # via-x list of the last KEPT index, or None if it was a guard
    for idx in protected:
        cur_via = used_x.get(str(idx))
        if kept and cur_via is not None and last_via is not None:
            was_adjacent = (idx - last_idx == 1)
            if not was_adjacent:
                min_dist = min(abs(a - b) for a in cur_via for b in last_via)
                if min_dist < min_via_x_sep:
                    # unsafe to fully close this gap -- keep one spacer
                    # (any removable index strictly between last_idx and idx;
                    # they're by definition free of both a guard and a via)
                    kept.append(last_idx + 1)
        kept.append(idx)
        last_idx = idx
        last_via = cur_via
    return kept


def build_y_map(ch_y0, ch_heights, row_y0, row_h, n_rows, n_ch,
                 track_pitch, track0_offset, kept_by_channel):
    """Builds a sorted list of (old_y0, old_y1, new_y0) breakpoints
    describing a monotonic, piecewise-linear map old_y -> new_y:
      - identity (slope 1) through every ROW band and every KEPT track's
        own 1-track-pitch slice within a channel
      - collapsed to zero width (slope 0, i.e. a single point) through
        every REMOVED track's slice
    Returns a function y -> new_y."""
    breakpoints = []  # [(old_y, new_y), ...] strictly increasing in old_y;
                       # between two consecutive breakpoints the map is
                       # linear with slope 0 or 1 (only 0 or 1 ever occurs
                       # here since we never rescale kept material, only
                       # delete gaps)
    cur_new = 0.0
    cur_old = 0.0
    breakpoints.append((0.0, 0.0))

    for c in range(n_ch):
        budget = int(round((ch_heights[c] - 2 * track0_offset) / track_pitch)) + 1
        kept = sorted(kept_by_channel[c])
        # channel band: [ch_y0[c], ch_y0[c]+ch_heights[c]]
        band_lo = ch_y0[c]
        band_hi = ch_y0[c] + ch_heights[c]
        # advance identity up to band_lo (should already be cur_old==band_lo
        # by construction, since rows/channels are contiguous)
        assert abs(cur_old - band_lo) < 1e-6, (c, cur_old, band_lo)
        prev_track_top = band_lo  # running old-Y cursor within this channel
        for idx in kept:
            track_y = band_lo + track0_offset + idx * track_pitch
            slice_lo = track_y - track_pitch / 2.0
            slice_hi = track_y + track_pitch / 2.0
            # v8 fix (this session, design_notes 77.x): TRACK0_OFFSET
            # (2.0) < TRACK_PITCH/2 (2.7) means the FIRST kept index's
            # nominal slice can start slightly BELOW band_lo, and/or the
            # LAST kept index's slice can end slightly ABOVE band_hi --
            # both bleed into the neighboring ROW band, which is
            # supposed to be pure identity-mapped (untouched) territory.
            # Confirmed via GDS forensics as the exact mechanism behind
            # every post-squeeze M2 Smin violation this session: a
            # cell's own physical pin (moved by the RIGID per-instance
            # transform, using ONLY the row's own identity shift) versus
            # a routed wire's endpoint at the exact same pre-squeeze Y
            # (moved by this independent per-point y_map query) landing
            # 1.2-2.7um apart post-squeeze, because that Y fell inside a
            # track slice that had silently grown past the channel/row
            # boundary and stolen some of the row's "identity" span for
            # itself. Clip every slice to the channel's own true bounds
            # before it can affect the map at all -- a track's real
            # extent was never actually outside the channel to begin
            # with.
            slice_lo = max(slice_lo, band_lo)
            slice_hi = min(slice_hi, band_hi)
            if slice_hi - slice_lo < 1e-9:
                continue
            # gap BEFORE this track's slice (relative to prev_track_top) is
            # collapsed to zero width
            gap = slice_lo - prev_track_top
            if gap > 1e-9:
                cur_new += 0.0  # collapse: old advances, new does not
            cur_old = slice_lo
            breakpoints.append((cur_old, cur_new))
            # this track's own slice is kept at identity (slope 1)
            cur_old = slice_hi
            cur_new += (slice_hi - slice_lo)
            breakpoints.append((cur_old, cur_new))
            prev_track_top = slice_hi
        # remainder of the channel (from the last kept track's slice top to
        # the channel's own top edge) is also collapsed -- nothing needed
        # there (headroom above the topmost claimed track), EXCEPT the
        # channel's own top edge must land exactly on the next row's
        # bottom edge, so just jump cur_old there with no new-Y advance.
        band_hi = ch_y0[c] + ch_heights[c]
        if band_hi - cur_old > 1e-9:
            cur_old = band_hi
            breakpoints.append((cur_old, cur_new))
        else:
            cur_old = band_hi

        # row c (if any) sits directly above this channel -- identity.
        # NOTE: row_y0[c] is the row's BOTTOM edge (== this channel's own
        # top edge, ch_y0[c]+ch_heights[c]), not its top -- matches
        # route_channels_nrow_fm.py's own construction (row_y0.append(y)
        # happens right after y += CH_HEIGHTS[i], before y += row_h).
        if c < n_rows:
            row_lo = row_y0[c]
            row_hi = row_y0[c] + row_h
            assert abs(row_lo - cur_old) < 1e-6, (c, row_lo, cur_old)
            cur_old = row_hi
            cur_new += row_h
            breakpoints.append((cur_old, cur_new))

    # dedupe (keep last value for repeated old_y, e.g. zero-width gaps
    # collapsing to a single breakpoint)
    dedup = []
    for old_y, new_y in breakpoints:
        if dedup and abs(dedup[-1][0] - old_y) < 1e-9:
            dedup[-1] = (old_y, new_y)
        else:
            dedup.append((old_y, new_y))
    breakpoints = dedup

    def y_map(y):
        # binary search would be nicer; linear is plenty fast here (few
        # hundred breakpoints, thousands of calls)
        for i in range(len(breakpoints) - 1):
            oy0, ny0 = breakpoints[i]
            oy1, ny1 = breakpoints[i + 1]
            if oy0 - 1e-6 <= y <= oy1 + 1e-6:
                if oy1 - oy0 < 1e-9:
                    return ny0
                frac = (y - oy0) / (oy1 - oy0)
                return ny0 + frac * (ny1 - ny0)
        # beyond the last breakpoint: identity offset by the final shift
        oy_last, ny_last = breakpoints[-1]
        return y + (ny_last - oy_last)

    total_new_height = breakpoints[-1][1]
    return y_map, total_new_height


def main(in_gds, compaction_info_path, out_gds, pin_map_in=None, pin_map_out=None,
         net_shapes_in=None, net_shapes_out=None, force_jog_events_in=None,
         force_jog_events_out=None):
    info = json.load(open(compaction_info_path))
    ch_y0 = info["ch_y0"]
    ch_heights = info["ch_heights"]
    row_y0 = info["row_y0"]
    row_h = info["row_h"]
    n_rows = info["n_rows"]
    n_ch = info["n_ch"]
    track_pitch = info["track_pitch"]
    track0_offset = info["track0_offset"]
    min_via_x_sep = info["min_via_x_sep"]

    kept_by_channel = {}
    for ch in info["channels"]:
        c = ch["channel"]
        kept_by_channel[c] = compute_kept_indices(
            ch["budget"], ch["guard_idx"], ch["used_x"], min_via_x_sep)
        n_removed = ch["budget"] - len(kept_by_channel[c])
        print(f"channel{c}: budget={ch['budget']} keeping={len(kept_by_channel[c])} "
              f"removing={n_removed} tracks ({n_removed * track_pitch:.1f} um)")

    y_map, new_core_h = build_y_map(ch_y0, ch_heights, row_y0, row_h, n_rows, n_ch,
                                     track_pitch, track0_offset, kept_by_channel)
    old_core_h = ch_y0[-1] + ch_heights[-1]
    print(f"core height: {old_core_h:.1f} um -> {new_core_h:.1f} um "
          f"(-{old_core_h - new_core_h:.1f} um, -{100*(old_core_h-new_core_h)/old_core_h:.1f}%)")

    layout = db.Layout()
    layout.read(in_gds)
    dbu = layout.dbu
    top = layout.cell(TOP_CELL_NAME)

    MFG_GRID_UM = 0.05  # v8 fix (this session): y_map()'s breakpoint
                         # accumulation (repeated float += over ~170+
                         # breakpoints for a design this size) drifts off
                         # the true manufacturing grid by a few
                         # thousandths of a um -- individually invisible,
                         # but the real KLayout DRC deck (drc.lydrc)
                         # flags every one as an OFFGRID 0.050 violation
                         # (340 found on V8's squeezed output, confirmed
                         # via direct .lyrdb inspection: e.g. a mapped
                         # Y=920.843 instead of a 0.05-multiple). Snap the
                         # mapped value to the true process grid, not
                         # just the layout's internal dbu (0.001um, far
                         # finer than the real grid) -- this is what
                         # every OTHER coordinate in this codebase
                         # (TRACK_PITCH=5.4, TRACK0_OFFSET=2.0, PAD sizes,
                         # etc.) already implicitly satisfies by
                         # construction; only squeeze's per-point y_map()
                         # evaluation was never snapped back to it.

    def um_map(y_dbu):
        y_new_um = y_map(y_dbu * dbu)
        y_new_um = round(y_new_um / MFG_GRID_UM) * MFG_GRID_UM
        return int(round(y_new_um / dbu))

    # 1. remap every top-level shape's Y coordinates, layer by layer.
    for li in layout.layer_indexes():
        shapes = top.shapes(li)
        new_polys = []
        to_delete = []
        for shape in shapes.each():
            if shape.is_box():
                b = shape.box
                new_box = db.Box(b.left, um_map(b.bottom), b.right, um_map(b.top))
                new_polys.append(new_box)
                to_delete.append(shape)
            elif shape.is_polygon() or shape.is_path() or shape.is_text():
                poly = shape.polygon if shape.is_polygon() else (
                    shape.path.polygon() if shape.is_path() else None)
                if poly is not None:
                    pts = [db.Point(pt.x, um_map(pt.y)) for pt in poly.each_point_hull()]
                    new_polys.append(db.Polygon(pts))
                    to_delete.append(shape)
                elif shape.is_text():
                    t = shape.text
                    nt = db.Text(t.string, db.Trans(t.x, um_map(t.y)))
                    new_polys.append(nt)
                    to_delete.append(shape)
        for shape in to_delete:
            shapes.erase(shape)
        for p in new_polys:
            shapes.insert(p)

    # 2. remap every top-level cell instance's placement Y (std cells +
    # via_1 PCell instances) -- a single point transform per instance,
    # safe because no instance ever straddles a channel/row boundary (all
    # cells live entirely within one row band; the router only ever
    # inserts via_1 instances at a single (x, track_y) point).
    for inst in list(top.each_inst()):
        t = inst.trans
        new_y = um_map(t.disp.y)
        new_trans = db.Trans(t.rot, t.is_mirror(), t.disp.x, new_y)
        inst.trans = new_trans

    layout.write(out_gds)
    print(f"wrote {out_gds}")

    if pin_map_in and pin_map_out:
        # verify_connectivity_nrow_fm.py's locate_m1() needs each pin's
        # exact post-squeeze (x, y) to find it on the moved M1 -- remap Y
        # through the SAME y_map used for the geometry itself (X is
        # untouched by this whole operation).
        pin_map = json.load(open(pin_map_in))
        new_pin_map = {
            net: [[inst, pin, x, y_map(y)] for inst, pin, x, y in pins]
            for net, pins in pin_map.items()
        }
        with open(pin_map_out, "w") as f:
            json.dump(new_pin_map, f, indent=1)
        print(f"wrote {pin_map_out}")

    if net_shapes_in and net_shapes_out:
        net_shapes = json.load(open(net_shapes_in))
        new_net_shapes = {
            net: [[kind, x0, y_map(y0), x1, y_map(y1)] for kind, x0, y0, x1, y1 in boxes]
            for net, boxes in net_shapes.items()
        }
        with open(net_shapes_out, "w") as f:
            json.dump(new_net_shapes, f)
        print(f"wrote {net_shapes_out}")

    if force_jog_events_in and force_jog_events_out:
        events = json.load(open(force_jog_events_in))
        new_events = []
        for ev in events:
            ev2 = dict(ev)
            for key in ("jog_y", "track_y", "channel_entry_y"):
                if key in ev2:
                    ev2[key] = y_map(ev2[key])
            new_events.append(ev2)
        with open(force_jog_events_out, "w") as f:
            json.dump(new_events, f, indent=1)
        print(f"wrote {force_jog_events_out}")

    return new_core_h


if __name__ == "__main__":
    IN_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v6_minheight_routed.gds"
    COMPACTION_INFO = "/tmp/v6_compaction_info.json"
    OUT_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v6_squeezed_routed.gds"
    PIN_MAP_IN = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v6_minheight_pin_map.json"
    PIN_MAP_OUT = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v6_squeezed_pin_map.json"
    NET_SHAPES_IN = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v6_minheight_net_shapes.json"
    NET_SHAPES_OUT = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v6_squeezed_net_shapes.json"
    FJ_IN = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v6_minheight_force_jog_events.json"
    FJ_OUT = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_v6_squeezed_force_jog_events.json"
    main(IN_GDS, COMPACTION_INFO, OUT_GDS, PIN_MAP_IN, PIN_MAP_OUT,
         NET_SHAPES_IN, NET_SHAPES_OUT, FJ_IN, FJ_OUT)
