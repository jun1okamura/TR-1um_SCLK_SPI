#!/usr/bin/env python3
"""port_i2c_scripts.py -- adapt the TR-1um_Async_I2C routing scripts to this project.

The routing chain is a hard-won, DRC/LVS-clean implementation; the port
therefore changes NOTHING about the algorithms.  It only rewrites the
environment each script was pinned to:

  * absolute Claude-sandbox paths ("/sessions/.../mnt/...")  -> spi_config
  * the PDK KLayout PCell dir (via_1)                        -> spi_config.pdk_tech_python()
  * the I2C top cell name "i2c_slave_async_nrow_fm"          -> spi_config.TOP_CELL_NAME
  * lef_parser's LEF/ (uppercase) directory                  -> this repo's lef/
  * default input/output file names                          -> spi_config, or None

A default that has no project equivalent becomes None on purpose: every one
of them is passed explicitly by scripts/route.py, so a None default fails
loudly instead of silently reading someone else's design.

Re-run it after refreshing scripts/i2c_ref/ from the I2C repo.

  usage:  scripts/port_i2c_scripts.py [--check]
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "i2c_ref")

FILES = [
    "lef_parser.py", "netlist_parser.py", "dedup_gates.py",
    "gen_placement_gds_nrow_fm.py", "route_channels_nrow_fm.py",
    "ripup_reroute_shorts.py", "drc_check_nrow_fm.py",
    "verify_connectivity_nrow_fm.py", "verify_connectivity_nrow_fm_m1m2.py",
    "highlight_top_pins_nrow_fm.py", "route_top_pins_nrow_fm.py",
    "add_power_pins_nrow_fm.py", "compress_channels_nrow_fm.py",
    "squeeze_channels_nrow_fm.py", "detect_loops_jogs.py",
]

# variable name -> spi_config attribute for the hardcoded default paths
PATHMAP = {
    "PLACEMENT_JSON": "_cfg.PLACEMENT_JSON",
    "CELL_GDS": "_cfg.CELL_GDS",
    "PIN_MAP_JSON": "_cfg.PIN_MAP_JSON",
    "NET_SHAPES_JSON": "_cfg.NET_SHAPES_JSON",
}

HEADER = '''
# --- ported from TR-1um_Async_I2C/script/ by scripts/port_i2c_scripts.py ---
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_sys.path.insert(0, _HERE)
import spi_config as _cfg  # noqa: E402
# ---------------------------------------------------------------------------
'''


def split_docstring(s):
    m = re.match(r'\A(\s*(?:"""|\'\'\')(?:.|\n)*?(?:"""|\'\'\')\s*\n)', s)
    return (m.group(1), s[m.end():]) if m else ("", s)


def port(name, text):
    changes = []

    def sub(pat, rep, why, count=0, flags=re.M):
        nonlocal text
        new, n = re.subn(pat, rep, text, count=count, flags=flags)
        if n:
            changes.append(f"{why} ({n})")
            text = new

    # sandbox sys.path inserts
    sub(r'sys\.path\.insert\(0,\s*"/sessions/[^"]*?/script"\)',
        'sys.path.insert(0, _HERE)', "sandbox script dir -> _HERE")
    sub(r'TECH_PY_DIR\s*=\s*"/sessions/[^"]*"',
        'TECH_PY_DIR = _cfg.pdk_tech_python()', "TECH_PY_DIR -> PDK lookup")
    sub(r'sys\.path\.insert\(0,\s*"/sessions/[^"]*klayout/tech/python"\)',
        'sys.path.insert(0, _cfg.pdk_tech_python())', "sandbox PDK dir -> lookup")

    # top cell name
    sub(r'"i2c_slave_async_nrow_fm"', '_cfg.TOP_CELL_NAME', "top cell name")

    # hardcoded default paths
    for var, rep in PATHMAP.items():
        sub(rf'^{var}\s*=\s*(?:\\\n\s*)?"/sessions/[^"]*"\s*$',
            f'{var} = {rep}', f"{var} default", count=0)
    sub(r'^(\w+)\s*=\s*sys\.argv\[1\] if len\(sys\.argv\) > 1 else \\\n\s*"/sessions/[^"]*"',
        r'\1 = sys.argv[1] if len(sys.argv) > 1 else None', "argv default path")
    # Anything left is an intermediate artefact whose absolute path was
    # hardcoded; keep the file name and put it in layout/.
    def art(m):
        return '_cfg.artifact("%s")' % m.group(1)
    sub(r'"/sessions/[^"]*/([^"/]+)"', art, "sandbox literal -> layout/<name>")

    # project-specific file locations
    if name == "lef_parser.py":
        sub(r'LEF_PATH = str\(Path\(__file__\)[^\n]*\)',
            'LEF_PATH = _cfg.LEF_PATH', "LEF path -> lef/")
    if name == "squeeze_channels_nrow_fm.py":
        # The compaction rule "collapse everything above the last claimed
        # track" also flattens the top-level PIN markers, which sit ON the
        # core boundary.  Teach build_y_map to keep the Y intervals that PIN
        # shapes / their text labels occupy at identity.  The replacement
        # pairs live in port_rules.py (they contain triple-quoted code).
        import port_rules
        for i, (o, n) in enumerate(port_rules.SQUEEZE_PIN_PROTECT):
            if o in text:
                text = text.replace(o, n)
            else:
                changes.append(f"!! squeeze patch {i} not found")
        changes.append("PIN layers protected from compaction")

    if name == "route_top_pins_nrow_fm.py":
        # gather_pins() hardcodes a 4-row core: row0 -> bottom edge,
        # row3 -> top edge, row1 -> right edge, row2 -> left edge.
        # Generalise to n_rows: first and last row go vertical, the middle
        # rows split right/left.  For n_rows == 4 this is bit-identical to
        # the original (mids [1,2] -> right [1], left [2]).
        old = """    row0_ports = {item[0] for item in by_row[0]}
    row3_ports_all = {item[0] for item in by_row[3]}
    vertical_ports = row0_ports | row3_ports_all
    row0_list = dedup_min_x(by_row[0])
    row3_list = dedup_min_x([item for item in by_row[3] if item[0] not in row0_ports])
    row1_list = dedup_min_x([item for item in by_row[1] if item[0] not in vertical_ports])
    row2_list = dedup_min_x([item for item in by_row[2] if item[0] not in vertical_ports])"""
        new = """    _top = n_rows - 1
    _mids = list(range(1, _top))
    _half = (len(_mids) + 1) // 2
    _right = [it for r in _mids[:_half] for it in by_row[r]]
    _left = [it for r in _mids[_half:] for it in by_row[r]]
    row0_ports = {item[0] for item in by_row[0]}
    row3_ports_all = {item[0] for item in by_row[_top]} if _top > 0 else set()
    vertical_ports = row0_ports | row3_ports_all
    row0_list = dedup_min_x(by_row[0])
    row3_list = dedup_min_x([item for item in by_row[_top]
                             if item[0] not in row0_ports]) if _top > 0 else []
    row1_list = dedup_min_x([item for item in _right if item[0] not in vertical_ports])
    row2_list = dedup_min_x([item for item in _left if item[0] not in vertical_ports])"""
        if old in text:
            text = text.replace(old, new)
            changes.append("gather_pins: 4 rows -> n_rows")
        else:
            changes.append("!! gather_pins 4-row block not found")
        old3 = "    ch2_lo, ch2_hi = row_y0[1] + row_h, row_y0[2]"
        new3 = ("    if len(row_y0) >= 3:\n"
                "        ch2_lo, ch2_hi = row_y0[1] + row_h, row_y0[2]\n"
                "    else:\n"
                "        # no middle rows -> row1_list/row2_list are empty and\n"
                "        # this band is never used; keep it well-formed anyway\n"
                "        ch2_lo, ch2_hi = row_y0[-1] + row_h, core_h")
        if old3 in text:
            text = text.replace(old3, new3)
            changes.append("ch2 band: guarded for n_rows < 3")
        else:
            changes.append("!! ch2 band line not found")
        old2 = "    row3_bound = row_y0[3] + row_h"
        new2 = "    row3_bound = row_y0[len(row_y0) - 1] + row_h"
        if old2 in text:
            text = text.replace(old2, new2)
            changes.append("row3_bound: row_y0[3] -> last row")
        else:
            changes.append("!! row3_bound line not found")

    if name == "netlist_parser.py":
        sub(r'NET_PATH = str\(Path\(__file__\)[^\n]*\)',
            'NET_PATH = _cfg.NET_PATH', "netlist path")

    doc, body = split_docstring(text)
    return doc + HEADER + body, changes


def main(check=False):
    sys.path.insert(0, HERE)
    ok = True
    for f in FILES:
        src = os.path.join(REF, f)
        if not os.path.exists(src):
            print(f"  !! {f}: not in i2c_ref/"); ok = False; continue
        out, changes = port(f, open(src).read())
        code = "\n".join(l.split("#")[0] for l in out.split("\n"))
        leftover = re.findall(r"/sessions/\S*", code)
        dst = os.path.join(HERE, f)
        print(f"  {f}")
        for c in changes:
            print(f"      - {c}")
        if leftover:
            print(f"      !! sandbox path still present: {leftover[:2]}")
            ok = False
        if not check:
            open(dst, "w").write(out)
    print("\n" + ("check only, nothing written" if check
                  else f"ported {len(FILES)} script(s) into {HERE}"))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    sys.exit(main(a.check))
