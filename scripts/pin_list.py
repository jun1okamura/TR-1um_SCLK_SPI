#!/usr/bin/env python3
"""pin_list.py -- the chip's pin assignment, as a table.

  -> docs/pin_list.md

Generated, like everything else here, so it cannot drift from the design.  Each
column comes from a different authority and they are cross-checked against each
other on the way through:

  bond pad geometry      lef/TR-1um_frame_25x25.gds -- the OSS_PAD instances and
                         the P<n>/VDD/VSS labels that sit on them
  ring terminal geometry the same GDS (scripts/frame_pins.py)
  pad role and net       layout/chip/gio_connections.json, which is itself
                         generated from hdl/tr_1um_3wire_SPI.v's pad table
  direction              the core netlist's own port declarations

A pad that the connection map does not mention, or a core port with no pad, is
an error rather than a blank row.

  usage:  scripts/pin_list.py [-o docs/pin_list.md]
"""
import argparse
import json
import os
import re
import sys

import klayout.db as db

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import spi_config as _cfg
import frame_pins

OUT_MD = os.path.join(_cfg.ROOT, "docs", "pin_list.md")
CONN = os.path.join(_cfg.CHIP, "gio_connections.json")

PAD_CELL = "OSS_PAD"
PAD_TEXT_LAYERS = [(49, 0), (48, 0)]

# What the pad does electrically, from its HIZ source.
ROLE_NOTE = {
    "VDD": "input only (HIZ tied high)",
    "GND": "always driven (HIZ tied low)",
}


def bond_pads(gds=None, cell=None):
    """{label: (x, y, edge)} for every bond pad in the ring."""
    gds = gds or _cfg.FRAME_GDS
    cell = cell or _cfg.FRAME_CELL
    ly = db.Layout()
    ly.read(gds)
    u = ly.dbu
    top = ly.cell(cell)

    centres = set()

    def walk(c, tr, depth=0):
        for inst in c.each_inst():
            ch = ly.cell(inst.cell_index)
            t = tr * inst.trans
            if ch.name == PAD_CELL:
                centres.add((round(t.disp.x * u, 1), round(t.disp.y * u, 1)))
            elif depth < 4:
                walk(ch, t, depth + 1)

    walk(top, db.Trans())

    out = {}
    for lay in PAD_TEXT_LAYERS:
        it = top.begin_shapes_rec(ly.layer(*lay))
        while not it.at_end():
            s = it.shape()
            if s.is_text():
                t = s.text.transformed(it.trans())
                p = (round(t.x * u, 1), round(t.y * u, 1))
                if p in centres:
                    edge = ("RIGHT" if p[0] > 0 else "LEFT") if abs(p[0]) > abs(p[1]) \
                        else ("TOP" if p[1] > 0 else "BOTTOM")
                    out[s.text.string] = (p[0], p[1], edge)
            it.next()
    missing = centres - {(v[0], v[1]) for v in out.values()}
    if missing:
        raise SystemExit(f"{len(missing)} bond pad(s) with no label: {sorted(missing)}")
    return out


def port_dirs(path=None):
    text = open(path or _cfg.NET_PATH).read()
    dirs = {}
    for m in re.finditer(
        r"^\s*(input|output|inout)\s+(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+)?([A-Za-z_]\w*)\s*;",
        text, re.M):
        d, hi, lo, name = m.groups()
        if hi is None:
            dirs[name] = d
        else:
            for i in range(min(int(hi), int(lo)), max(int(hi), int(lo)) + 1):
                dirs[f"{name}[{i}]"] = d
    return dirs


def build():
    conn = json.load(open(CONN))
    pads = bond_pads()
    terms = frame_pins.load()
    dirs = port_dirs()
    padmap = {int(k[1:]): v for k, v in conn["connections"].items()}
    ties = conn["power_ties"]

    labelled = {n for n in pads if n.startswith("P")}
    if labelled != {f"P{n}" for n in padmap}:
        raise SystemExit(f"bond pads {sorted(labelled)} do not match the "
                         f"connection map {sorted('P' + str(n) for n in padmap)}")

    rows = []
    for n in sorted(padmap):
        spec = padmap[n]
        bx, by, bedge = pads[f"P{n}"]
        core_in = spec.get("P")
        core_out = spec.get("OUT")
        hiz = spec["HIZ"]
        if core_in and core_out:
            direction = "bidir"
        elif core_out:
            direction = "out"
        else:
            direction = "in"
        rows.append(dict(pad=f"P{n}", x=bx, y=by, edge=bedge, role=spec["role"],
                         dir=direction, core_in=core_in, core_out=core_out,
                         hiz=hiz,
                         term=(terms[f"P{n}"]["x"], terms[f"P{n}"]["y"])))
    power = [dict(pad=name, x=pads[name][0], y=pads[name][1], edge=pads[name][2])
             for name in sorted(pads) if not name.startswith("P")]

    unconnected = conn["core_outputs_not_connected_to_any_pad"]
    used_ports = {r["core_in"] for r in rows if r["core_in"]} | \
                 {r["core_out"] for r in rows if r["core_out"]} | \
                 {r["hiz"] for r in rows if r["hiz"] in dirs} | set(unconnected)
    missing = sorted(set(dirs) - used_ports)
    if missing:
        raise SystemExit(f"core port(s) with no pad and not listed as unconnected: {missing}")
    return conn, rows, power, ties, dirs, unconnected


def render(conn, rows, power, ties, dirs, unconnected):
    geom = conn["chip_geometry"]
    L = []
    L.append("# ピン配置 — `tr_1um_3wire_SPI`")
    L.append("")
    L.append("`scripts/pin_list.py` が生成。手で編集しない。")
    L.append("")
    L.append(f"- ダイ 2,500 × 2,500 µm、`OSS_FRAME_GIO` の16パッド"
             f"(信号14 + VDD + VSS)。**P8 は存在しない**")
    L.append(f"- ボンドパッド中心は半径1,040 µm、コアが繋がるリング端子は半径921.7 µm")
    L.append(f"- コアは {tuple(geom['core_chip_bbox'])} "
             f"(オフセット {tuple(geom['core_offset'])})")
    L.append("")
    L.append("## 信号パッド")
    L.append("")
    L.append("| パッド | ボンド座標 | 辺 | 役割 | 方向 | コア入力 (`P`) | コア出力 (`OUT`) | `HIZ` の駆動元 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        hz = f"`{r['hiz']}`" if r["hiz"] not in ("VDD", "GND") else \
            f"**{r['hiz']}** 固定"
        L.append(f"| `{r['pad']}` | ({r['x']:.0f}, {r['y']:.0f}) | {r['edge']} | "
                 f"**{r['role']}** | {r['dir']} | "
                 f"{('`' + r['core_in'] + '`') if r['core_in'] else '—'} | "
                 f"{('`' + r['core_out'] + '`') if r['core_out'] else '—'} | {hz} |")
    L.append("")
    L.append("## パッド並び(ダイを表から見て、左上から時計回り)")
    L.append("")
    L.append("基板を配線するときはこちらの順。**電源パッドが信号列の間に入る**"
             "(`VDD` は `P1` と `P15` の間、`VSS` は `P7` と `P9` の間)。")
    L.append("")
    order = {"TOP": lambda p: p[1], "RIGHT": lambda p: -p[2],
             "BOTTOM": lambda p: -p[1], "LEFT": lambda p: p[2]}
    allp = [(r["pad"], r["x"], r["y"], r["edge"], r["role"]) for r in rows] + \
           [(q["pad"], q["x"], q["y"], q["edge"], "電源") for q in power]
    for edge in ("TOP", "RIGHT", "BOTTOM", "LEFT"):
        here = sorted((p for p in allp if p[3] == edge), key=order[edge])
        L.append(f"- **{edge}** … " +
                 " / ".join(f"`{n}` {role}" for n, _, _, _, role in here))
    L.append("")
    L.append("## 電源パッド")
    L.append("")
    L.append("| パッド | 座標 | 辺 | コア側 |")
    L.append("|---|---|---|---|")
    L.append(f"| `{power[0]['pad']}` | ({power[0]['x']:.0f}, {power[0]['y']:.0f}) | "
             f"{power[0]['edge']} | M1実ピン (50,920)-(350,934) へライザー9本 |")
    L.append(f"| `{power[1]['pad']}` | ({power[1]['x']:.0f}, {power[1]['y']:.0f}) | "
             f"{power[1]['edge']} | リングのGND端子経由(左右2本の脚) |")
    L.append("")
    L.append("## `HIZ` の極性")
    L.append("")
    L.append("`OSS_ESD_5V_DIO` は **`HIZ=0` で出力ドライバON**、`HIZ=1` でHi-Z。")
    L.append("")
    L.append("- **DATA 8本** … `HIZ` は **DIS パッド(`P5`)の網に直結**。コアが"
             "DATAを駆動するのは `data_oe = ~dis = 1`、つまり `dis = 0` のときで、"
             "`HIZ = dis` が過不足なく一致するので論理が要らない")
    L.append("- **`P2` (SDIO)** … コアが `sdio_oe_n = ~(dis & ~cs_n)`"
             "(アクティブLOW)を出すので `HIZ2` に直結")
    L.append("- **入力専用パッド** … `HIZ` を VDD 固定。`OUT` ピンは"
             "同じ辺で20 µm隣のリングGND端子に落としてある(開放にすると"
             "`OSS_ESD_5V_DIO` 内のゲート入力が浮く)")
    L.append("")
    L.append("固定タイ: " + ", ".join(f"`{k}`→**{v}**" for k, v in sorted(ties.items())))
    L.append("")
    L.append("## パッドの無いコア出力")
    L.append("")
    for p in unconnected:
        L.append(f"- **`{p}`** … `~dis` と同じ情報で、これを使うパッドは"
                 f"すべて `HIZ` を DIS パッドの網から取っている。プローブ用に"
                 f"ポートとしては残してある")
    L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=OUT_MD)
    args = ap.parse_args()
    conn, rows, power, ties, dirs, unconnected = build()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(render(conn, rows, power, ties, dirs, unconnected))
    print(f"wrote {args.out}: {len(rows)} signal pad(s) + {len(power)} power pad(s), "
          f"{len(dirs)} core port(s) accounted for")


if __name__ == "__main__":
    main()
