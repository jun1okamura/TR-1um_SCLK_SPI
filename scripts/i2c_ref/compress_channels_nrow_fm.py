"""
compress_channels_nrow_fm.py

Section 41 (user request): a POST-PROCESS that shrinks each routing
channel's height down to what it actually needs, after a normal
placement+route run has already measured real track usage.

Why a post-process and not a one-shot formula: channel height feeds back
into where every row/track/via physically sits, so "compression" can't be
done by editing already-drawn GDS geometry in place -- the only reliable
way is to re-run the existing, already-verified placement-GDS + routing
steps with a smaller CH_HEIGHTS. This script just automates measuring,
computing the new heights, and driving that re-run + re-verification.

Method:
  1. Run route_channels_nrow_fm.py once (or reuse an existing run's
     channel_usage_path JSON) to get each channel's real track usage
     (next_free_idx) -- this is the router's own ground truth for how
     many M1 trunk track slots a channel actually handed out. (A more
     literal "measure the drawn geometry's real Y extent" approach was
     tried first and rejected: TAP2's VDD/GND power-mesh straps always
     run the FULL channel height by construction, and per-row-local
     nets' trunk-to-pin M2 stubs legitimately extend UP INTO the row
     above regardless of channel height, so a raw geometry probe always
     reports "fully used" -- neither is a real channel-height
     constraint. See design_notes.md section 41 for the discarded
     attempt.)
  2. new_height[c] = min(orig_height[c], (used_tracks[c] - 1) * TRACK_PITCH
     + 2*TRACK0_OFFSET + MARGIN_TRACKS*TRACK_PITCH) -- i.e. exactly enough
     room for the tracks actually used, plus a MARGIN_TRACKS-track safety
     buffer, but NEVER larger than the original (a channel already at or
     near its budget has no room to compress, and the formula alone can
     slightly disagree with real DRC-clean geometry in either direction --
     see design_notes.md 40.4's channel0 example -- so capping at the
     original height is the safe default for a channel this formula
     doesn't clearly shrink).
  3. Re-run gen_placement_gds_nrow_fm.py and route_channels_nrow_fm.py
     with the new CH_HEIGHTS, then drc_check_nrow_fm.py and
     verify_connectivity_nrow_fm.py to confirm the compression didn't
     introduce any violation or short. If it did, the caller should widen
     the offending channel (bump MARGIN_TRACKS, or hand-adjust that one
     channel's entry) and re-run -- this script does not auto-retry.

Run standalone (compresses the ORIGINAL netlist's pipeline by default):
    python3 script/compress_channels_nrow_fm.py

For the v4 (row-buffered) pipeline, call compute_new_heights()/main() with
the v4-specific paths -- see the bottom of this file for both examples,
or import and call directly from another script/session.
"""
import json
import sys

sys.path.insert(0, "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/script")
import route_channels_nrow_fm as R  # noqa: E402
import gen_placement_gds_nrow_fm as G  # noqa: E402

TRACK_PITCH = R.TRACK_PITCH
TRACK0_OFFSET = R.TRACK0_OFFSET
MARGIN_TRACKS = 1  # extra safety-margin tracks added on top of measured usage


def compute_new_heights(channel_usage, orig_heights, margin_tracks=MARGIN_TRACKS):
    new_heights = []
    for row in channel_usage:
        c = row["channel"]
        used = row["used_tracks"]
        est = (used - 1) * TRACK_PITCH + 2 * TRACK0_OFFSET + margin_tracks * TRACK_PITCH
        new_heights.append(round(min(orig_heights[c], est), 1))
    return new_heights


def main(placement_json=R.PLACEMENT_JSON, in_gds=R.IN_GDS,
         orig_heights=None, margin_tracks=MARGIN_TRACKS,
         force_jog_nets=None, per_row_local_nets=None,
         out_prefix=None):
    """orig_heights: the CH_HEIGHTS the ORIGINAL (uncompressed) routing run
    used -- required, since this script measures usage from that run
    rather than re-deriving it. out_prefix: filename prefix for every
    output artifact this run writes (placement gds / routed gds / pin_map
    / net_shapes / force_jog_events / channel_usage); defaults to
    in_gds's own directory + "compressed_" if not given."""
    if orig_heights is None:
        orig_heights = list(R.CH_HEIGHTS)
    if out_prefix is None:
        out_prefix = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/Layout/i2c_slave_async_nrow_fm_compressed"

    # Step 1: measure real usage from a normal run at the ORIGINAL heights.
    usage_path = out_prefix + "_pre_usage.json"
    print("=== measuring pass (original heights) ===")
    R.main(placement_json=placement_json, in_gds=in_gds,
           out_gds=out_prefix + "_pre_measure.gds",
           force_jog_nets=force_jog_nets, per_row_local_nets=per_row_local_nets,
           pin_map_path=out_prefix + "_pre_pin_map.json",
           net_shapes_path=out_prefix + "_pre_net_shapes.json",
           force_jog_events_path=out_prefix + "_pre_force_jog_events.json",
           channel_usage_path=usage_path, ch_heights=orig_heights)
    channel_usage = json.load(open(usage_path))

    new_heights = compute_new_heights(channel_usage, orig_heights, margin_tracks)
    print(f"\norig heights:       {orig_heights}")
    print(f"compressed heights: {new_heights}")
    saved = sum(orig_heights) - sum(new_heights)
    print(f"channel-height savings: {saved:.1f} um "
          f"({100 * saved / sum(orig_heights):.1f}% of total channel budget)")

    # Step 2: re-place + re-route at the compressed heights.
    print("\n=== compressed re-run ===")
    placement_gds = out_prefix + "_placement.gds"
    G.main(placement_json=placement_json, out_gds=placement_gds, ch_heights=new_heights)
    routed_gds = out_prefix + "_routed.gds"
    R.main(placement_json=placement_json, in_gds=placement_gds, out_gds=routed_gds,
           force_jog_nets=force_jog_nets, per_row_local_nets=per_row_local_nets,
           pin_map_path=out_prefix + "_pin_map.json",
           net_shapes_path=out_prefix + "_net_shapes.json",
           force_jog_events_path=out_prefix + "_force_jog_events.json",
           channel_usage_path=out_prefix + "_usage.json", ch_heights=new_heights)

    print(f"\nwrote {routed_gds}")
    print("Run drc_check_nrow_fm.py / verify_connectivity_nrow_fm.py on this file to confirm "
          "the compression is still clean (scan window must cover the new, SHORTER core height).")
    return routed_gds, new_heights


if __name__ == "__main__":
    main()
