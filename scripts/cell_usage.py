#!/usr/bin/env python3
"""cell_usage.py -- which standard cells this design actually instantiates.

Reports the whole library against the FINAL PLACEMENT (so physical cells --
TAP/FILL -- are included, unlike scripts/gate_count.py which only sees the
netlist), split into:

  logic     cells that carry the design's function
  physical  TAP / FILL, placed by scripts/place.py
  unused    library macros this design never instantiates

Sizes come from the LEF MACRO SIZE, transistor counts from lef/cell_info.json.

  usage:  scripts/cell_usage.py [-o docs/cell_usage.md] [--markdown]
"""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spi_config as cfg      # noqa: E402
import lef_parser             # noqa: E402

PHYS_PREFIX = ("TAP_", "FILL_", "FILLPRI_")
NAND2_TR = 4


def collect(placement=cfg.PLACEMENT_JSON):
    pl = json.load(open(placement))
    logic, phys = collections.Counter(), collections.Counter()
    for row in pl["rows"]:
        for i in row:
            tgt = phys if i["name"].startswith(PHYS_PREFIX) else logic
            tgt[i["type"]] += 1
    return pl, logic, phys


def main(placement=cfg.PLACEMENT_JSON, out=None, markdown=False):
    pl, logic, phys = collect(placement)
    macros = lef_parser.parse_lef(cfg.LEF_PATH)
    info = json.load(open(os.path.join(cfg.ROOT, "lef", "cell_info.json")))

    def row_of(name, n):
        w, h = macros[name]["size"]
        tr = info.get(name, {}).get("transistors") or 0
        a = w * h
        return dict(cell=name, n=n, w=w, h=h, area=a, tr=tr,
                    area_tot=a * n, tr_tot=tr * n,
                    pins=",".join(p for p, d in macros[name]["pins"].items()
                                  if d["use"] not in ("POWER", "GROUND")))

    L = [row_of(c, n) for c, n in logic.most_common()]
    P = [row_of(c, n) for c, n in phys.most_common()]
    used = set(logic) | set(phys)
    U = sorted(set(macros) - used)

    lines = []
    w = lines.append
    ttl = "# STDCELL 利用リスト — tr_1um_3wire_SPI\n"
    w(ttl)
    w(f"配置: `{os.path.relpath(placement, cfg.ROOT)}`  "
      f"({len(pl['rows'])}行 × {pl['row_width']:.1f} µm)\n")
    w(f"ライブラリ: `lef/TR-1um_STDCELL.lef` — マクロ {len(macros)}種、"
      f"うち **使用 {len(used)}種 / 未使用 {len(U)}種**\n")

    def table(title, rows, note=""):
        w(f"\n## {title}\n")
        if note:
            w(note + "\n")
        w("| セル | 個数 | W × H (µm) | 面積/個 (µm²) | 面積計 (µm²) | Tr/個 | Tr計 | 信号ピン |")
        w("|---|---:|---|---:|---:|---:|---:|---|")
        for r in rows:
            w(f"| `{r['cell']}` | {r['n']} | {r['w']:.1f} × {r['h']:.1f} | "
              f"{r['area']:,.0f} | {r['area_tot']:,.0f} | {r['tr']} | "
              f"{r['tr_tot']} | {r['pins']} |")
        w(f"| **計** | **{sum(r['n'] for r in rows)}** | | | "
          f"**{sum(r['area_tot'] for r in rows):,.0f}** | | "
          f"**{sum(r['tr_tot'] for r in rows)}** | |")

    table("論理セル", L)
    table("物理セル(TAP / FILL)", P,
          "`scripts/place.py` が配置。`TAP2` は x = 0 / 534.6 / 1069.2 / 1609.2 の"
          "固定位置、その直後の `FILL2` は優先M2コリドー(`FILLPRI_*`)として"
          "ルータが行またぎの着地点に使う。")

    tot_a = sum(r["area_tot"] for r in L + P)
    tot_t = sum(r["tr_tot"] for r in L + P)
    la = sum(r["area_tot"] for r in L)
    lt = sum(r["tr_tot"] for r in L)
    w("\n## サマリ\n")
    w("| | 論理 | 物理 | 合計 |")
    w("|---|---:|---:|---:|")
    w(f"| インスタンス数 | {sum(logic.values())} | {sum(phys.values())} | "
      f"{sum(logic.values()) + sum(phys.values())} |")
    w(f"| セル面積 (µm²) | {la:,.0f} | {tot_a - la:,.0f} | {tot_a:,.0f} |")
    w(f"| トランジスタ数 | {lt} | {tot_t - lt} | {tot_t} |")
    w(f"\n**等価ゲート数(論理セルのみ): {lt / NAND2_TR:.0f}** "
      f"(`NAND2` = {NAND2_TR} Tr = 1 ゲート)\n")

    w("\n## 未使用のライブラリセル\n")
    w("| セル | W × H (µm) | Tr | 備考 |")
    w("|---|---|---:|---|")
    NOTE = {
        "BUF_X1": "行バッファは `BUF_X2` を採用(同フットプリントで2倍ドライブ)",
        "BUF_X4": "LEFにMACROはあるがGDS未実装",
        "BUF_X16": "LEFにMACROはあるがGDS未実装",
        "DFF": "非同期リセット無し。本設計は全FFが `RSTN` 必須",
        "DFFS": "セット付きFF。本設計はリセットのみ",
        "RSLATCH": "ラッチ。本設計は純同期のFFのみ",
        "TAP3": "`TAP2` で足りている",
        "DEL1": "遅延セル。純同期設計なので不要",
    }
    for c in U:
        ww, hh = macros[c]["size"]
        tr = info.get(c, {}).get("transistors")
        w(f"| `{c}` | {ww:.1f} × {hh:.1f} | {tr if tr is not None else '-'} | "
          f"{NOTE.get(c, '合成で選ばれなかった')} |")

    text = "\n".join(lines) + "\n"
    if markdown or out:
        path = out or os.path.join(cfg.ROOT, "docs", "cell_usage.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)
        print(f"wrote {os.path.relpath(path, cfg.ROOT)}")
    else:
        print(text)
    return text


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--placement", default=cfg.PLACEMENT_JSON)
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--markdown", action="store_true")
    a = ap.parse_args()
    main(a.placement, a.output, a.markdown)
