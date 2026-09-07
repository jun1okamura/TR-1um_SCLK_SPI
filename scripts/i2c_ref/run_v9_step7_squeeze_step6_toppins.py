"""
run_v9_step7_squeeze_step6_toppins.py

STEP7 (channel squeeze) + STEP6 (top-pin extraction), applied to V9's
final, fully-converged route (layout/step8/v9_step_3_ripup_reroute.gds
-- DRC 0/short 0, design_notes.md 77.41/77.42), in the same order
v8's own proven recipe used
(run_v8_step7_squeeze_step6_toppins.py): squeeze first, THEN re-route
top pins against the new (smaller) BBOX. See that script's own
docstring for the full rationale of deriving compaction_info
empirically from the real M1 geometry rather than reusing a stale
routing-time dump.
"""
import json
import subprocess
import sys

sys.path.insert(0, "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/script")

BASE = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C"
SCRIPT = BASE + "/script"

PLACEMENT_JSON = BASE + "/LEF/placement_nrow_fm_v9.json"
IN_GDS = BASE + "/layout/step8/v9_step_3_ripup_reroute.gds"
PIN_MAP_IN = SCRIPT + "/pin_map_nrow_fm_v9final.json"
NET_SHAPES_IN = SCRIPT + "/net_shapes_nrow_fm_v9final.json"

CH_HEIGHTS = [131.6, 380.0, 380.0, 380.0, 153.2]
ROW_H = 64.8
N_ROWS = 4
N_CH = 5
ROW_WIDTH_UM = 1620.0
TRACK_PITCH = 5.4
TRACK0_OFFSET = 2.0
M1_PAD_SIZE = 3.4
MIN_VIA_X_SEP = M1_PAD_SIZE + 1.4
TRUNK_WIDTH_THRESHOLD = 30.0
HIGH_FO_GUARD_TRACKS = 1

TOP_CELL_NAME = "i2c_slave_async_nrow_fm"
M1_LAYER = (13, 0)

COMPACTION_INFO = SCRIPT + "/compaction_info_nrow_fm_v9_empirical.json"
SQUEEZED_GDS = BASE + "/layout/step8/v9_step_7_squeezed.gds"
SQUEEZED_PIN_MAP = SCRIPT + "/pin_map_nrow_fm_v9sq.json"
SQUEEZED_NET_SHAPES = SCRIPT + "/net_shapes_nrow_fm_v9sq.json"

TOP_PINS_OUT_GDS = BASE + "/layout/step8/v9_step_8_squeezed_top_pins_routed.gds"


def ch_y0_list():
    ch_y0 = []
    y = 0.0
    for i in range(N_ROWS):
        ch_y0.append(y)
        y += CH_HEIGHTS[i]
        y += ROW_H
    ch_y0.append(y)
    return ch_y0


def row_y0_list():
    row_y0 = []
    y = 0.0
    for i in range(N_ROWS):
        y += CH_HEIGHTS[i]
        row_y0.append(y)
        y += ROW_H
    return row_y0


def build_empirical_compaction_info():
    import klayout.db as db
    layout = db.Layout()
    layout.read(IN_GDS)
    dbu = layout.dbu
    top = layout.cell(TOP_CELL_NAME)
    m1_idx = layout.layer(*M1_LAYER)

    def um(v):
        return int(round(v / dbu))

    ch_y0 = ch_y0_list()
    half_track = TRACK_PITCH / 2.0

    channels = []
    for c in range(N_CH):
        budget = int(round((CH_HEIGHTS[c] - 2 * TRACK0_OFFSET) / TRACK_PITCH)) + 1
        band_lo = ch_y0[c]
        band_hi = ch_y0[c] + CH_HEIGHTS[c]
        used_x = {}
        guard = set()
        for idx in range(budget):
            track_y = band_lo + TRACK0_OFFSET + idx * TRACK_PITCH
            slice_lo = track_y - half_track
            slice_hi = track_y + half_track
            clip_lo = max(slice_lo, band_lo)
            clip_hi = min(slice_hi, band_hi)
            if clip_hi - clip_lo < 1e-6:
                continue
            band = db.Box(um(0.0), um(clip_lo), um(ROW_WIDTH_UM), um(clip_hi))
            hits = db.Region(top.begin_shapes_rec_touching(m1_idx, band)).merged()
            if hits.is_empty():
                continue
            xs = []
            is_trunk = False
            for poly in hits.each():
                bbox = poly.bbox()
                width_um = (bbox.right - bbox.left) * dbu
                cx = (bbox.left + bbox.right) / 2.0 * dbu
                xs.append(cx)
                if width_um >= TRUNK_WIDTH_THRESHOLD:
                    is_trunk = True
            used_x[str(idx)] = xs
            if is_trunk:
                if idx - HIGH_FO_GUARD_TRACKS >= 0:
                    guard.add(idx - HIGH_FO_GUARD_TRACKS)
                if idx + HIGH_FO_GUARD_TRACKS <= budget - 1:
                    guard.add(idx + HIGH_FO_GUARD_TRACKS)
        channels.append({"channel": c, "budget": budget, "guard_idx": sorted(guard), "used_x": used_x})
        print(f"  channel{c}: budget={budget} real-used={len(used_x)} guard(from-trunks)={len(guard)}")

    info = {
        "ch_y0": ch_y0,
        "ch_heights": CH_HEIGHTS,
        "row_y0": row_y0_list(),
        "row_h": ROW_H,
        "n_rows": N_ROWS,
        "n_ch": N_CH,
        "track_pitch": TRACK_PITCH,
        "track0_offset": TRACK0_OFFSET,
        "min_via_x_sep": MIN_VIA_X_SEP,
        "row_width_um": ROW_WIDTH_UM,
        "channels": channels,
    }
    with open(COMPACTION_INFO, "w") as f:
        json.dump(info, f, indent=1)
    print(f"wrote {COMPACTION_INFO}")
    return info


def verify(gds, pin_map, core_h, row_w, label):
    print(f"-- DRC ({label}) --")
    subprocess.run(["python3", SCRIPT + "/drc_check_nrow_fm.py", gds], check=True)
    print(f"-- connectivity ({label}) --")
    subprocess.run(["python3", SCRIPT + "/verify_connectivity_nrow_fm.py",
                     gds, pin_map, "0", str(core_h), str(row_w)], check=True)


def step_squeeze(info):
    import squeeze_channels_nrow_fm as SQ
    new_core_h = SQ.main(IN_GDS, COMPACTION_INFO, SQUEEZED_GDS,
                          pin_map_in=PIN_MAP_IN, pin_map_out=SQUEEZED_PIN_MAP,
                          net_shapes_in=NET_SHAPES_IN, net_shapes_out=SQUEEZED_NET_SHAPES)
    print(f"squeezed GDS -> {SQUEEZED_GDS} (new core_h={new_core_h:.1f}um)")
    return new_core_h


def build_y_map_with_channel_heights(ch_y0, ch_heights, row_y0, row_h, n_rows, n_ch,
                                      track_pitch, track0_offset, kept_by_channel):
    cur_new = 0.0
    cur_old = 0.0
    per_channel_new = []

    for c in range(n_ch):
        kept = sorted(kept_by_channel[c])
        band_lo = ch_y0[c]
        band_hi = ch_y0[c] + ch_heights[c]
        assert abs(cur_old - band_lo) < 1e-6, (c, cur_old, band_lo)
        new_lo = cur_new
        prev_track_top = band_lo
        for idx in kept:
            track_y = band_lo + track0_offset + idx * track_pitch
            slice_lo = max(track_y - track_pitch / 2.0, band_lo)
            slice_hi = min(track_y + track_pitch / 2.0, band_hi)
            if slice_hi - slice_lo < 1e-9:
                continue
            cur_old = slice_lo
            cur_old = slice_hi
            cur_new += (slice_hi - slice_lo)
            prev_track_top = slice_hi
        cur_old = band_hi
        new_hi = cur_new
        per_channel_new.append((new_lo, new_hi))

        if c < n_rows:
            cur_old = row_y0[c] + row_h
            cur_new += row_h

    return per_channel_new, cur_new


def compute_new_ch_heights(info):
    ch_y0 = info["ch_y0"]
    ch_heights = info["ch_heights"]
    row_y0 = info["row_y0"]
    row_h = info["row_h"]
    n_rows = info["n_rows"]
    n_ch = info["n_ch"]
    track_pitch = info["track_pitch"]
    track0_offset = info["track0_offset"]
    min_via_x_sep = info["min_via_x_sep"]

    import squeeze_channels_nrow_fm as SQ
    kept_by_channel = {}
    for ch in info["channels"]:
        c = ch["channel"]
        kept_by_channel[c] = SQ.compute_kept_indices(
            ch["budget"], ch["guard_idx"], ch["used_x"], min_via_x_sep)

    per_channel_new, new_core_h = build_y_map_with_channel_heights(
        ch_y0, ch_heights, row_y0, row_h, n_rows, n_ch, track_pitch, track0_offset, kept_by_channel)
    new_ch_heights = [hi - lo for lo, hi in per_channel_new]

    check_total = sum(new_ch_heights) + n_rows * row_h
    assert abs(check_total - new_core_h) < 1e-6, (check_total, new_core_h)
    return new_ch_heights, new_core_h


def step_top_pins(new_ch_heights):
    import route_top_pins_nrow_fm as T
    T.main(placement_json=PLACEMENT_JSON, in_gds=SQUEEZED_GDS, out_gds=TOP_PINS_OUT_GDS,
           ch_heights=new_ch_heights, net_shapes_json=SQUEEZED_NET_SHAPES)
    print(f"top-pin-routed GDS -> {TOP_PINS_OUT_GDS}")


if __name__ == "__main__":
    row_w = ROW_WIDTH_UM
    orig_core_h = sum(CH_HEIGHTS) + N_ROWS * ROW_H

    print("=== sanity: base v9 recipe is still 0-short / DRC-clean ===")
    verify(IN_GDS, PIN_MAP_IN, orig_core_h, row_w, "v9 (pre-squeeze)")

    print("\n=== build empirical compaction_info from real M1 geometry ===")
    info = build_empirical_compaction_info()

    print("\n=== squeeze away genuinely-empty interior tracks ===")
    new_core_h = step_squeeze(info)

    print("\n=== verify squeezed result ===")
    verify(SQUEEZED_GDS, SQUEEZED_PIN_MAP, new_core_h, row_w, "v9 squeezed")

    print("\n=== recompute new per-channel heights for top-pin re-routing ===")
    new_ch_heights, new_core_h2 = compute_new_ch_heights(info)
    print(f"new CH_HEIGHTS: {new_ch_heights}  (core_h={new_core_h2:.1f}um)")

    print("\n=== re-route top pins to the new (squeezed) BBOX ===")
    step_top_pins(new_ch_heights)

    print("\n=== verify top-pin-routed squeezed result ===")
    verify(TOP_PINS_OUT_GDS, SQUEEZED_PIN_MAP, new_core_h2, row_w, "v9 squeezed + top-pins")

    print(f"\ncore_h: {orig_core_h:.1f} -> {new_core_h2:.1f} um "
          f"(-{orig_core_h - new_core_h2:.1f} um, "
          f"-{100*(orig_core_h-new_core_h2)/orig_core_h:.1f}%)")
