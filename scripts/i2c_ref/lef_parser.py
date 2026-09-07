"""
lef_parser.py

Minimal LEF reader for LEF/TR-1um_STDCELL.lef -- pulls out per-MACRO
SIZE, FOREIGN (physical GDS cell name), and per-PIN DIRECTION/USE/PORT
geometry (list of (layer, x0, y0, x1, y1) rects). No dependency on any
particular LEF library; just enough regex/line-scanning to parse the
file this project's own gen_lef.py produces.

This is the authoritative source for the new (section 35) placement /
routing scripts -- geometry should come from here, not by re-deriving
positions from each cell's GDS every time.
"""
import re
from pathlib import Path

# 2026-09-02: was hardcoded to a Claude-sandbox absolute path
# (/sessions/dreamy-ecstatic-heisenberg/mnt/...), which only ever
# happened to work when this script was run from inside that sandbox --
# broke the first time the user ran the yosys->netlist chain locally on
# their own Mac. Made portable (relative to this file's own location,
# same pattern already used by insert_row_buffers.py's _REPO_ROOT).
LEF_PATH = str(Path(__file__).resolve().parent.parent / "LEF" / "TR-1um_STDCELL.lef")


def parse_lef(path=LEF_PATH):
    text = open(path).read()
    macros = {}
    for m in re.finditer(r"^MACRO (\S+)\n(.*?)\n\s*END\s+\1\s*$", text, re.M | re.S):
        name = m.group(1)
        body = m.group(2)

        size_m = re.search(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;", body)
        w, h = float(size_m.group(1)), float(size_m.group(2))

        foreign_m = re.search(r"FOREIGN\s+(\S+)\s+[\d.-]+\s+[\d.-]+\s*;", body)
        foreign = foreign_m.group(1) if foreign_m else name

        pins = {}
        for pm in re.finditer(r"^\s*PIN (\S+)\n(.*?)\n\s*END\s+\1\s*$", body, re.M | re.S):
            pname = pm.group(1)
            pbody = pm.group(2)
            direction = re.search(r"DIRECTION\s+(\S+)\s*;", pbody).group(1)
            use = re.search(r"USE\s+(\S+)\s*;", pbody).group(1)
            rects = []
            cur_layer = None
            for line in pbody.splitlines():
                line = line.strip()
                lm = re.match(r"LAYER\s+(\S+)\s*;", line)
                if lm:
                    cur_layer = lm.group(1)
                    continue
                rm = re.match(r"RECT\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*;", line)
                if rm and cur_layer:
                    x0, y0, x1, y1 = (float(v) for v in rm.groups())
                    rects.append((cur_layer, x0, y0, x1, y1))
            pins[pname] = {"direction": direction, "use": use, "rects": rects}

        macros[name] = {"size": (w, h), "foreign": foreign, "pins": pins}
    return macros


if __name__ == "__main__":
    macros = parse_lef()
    for name, m in macros.items():
        print(name, m["size"], "foreign=" + m["foreign"], "pins=" + ",".join(m["pins"]))
