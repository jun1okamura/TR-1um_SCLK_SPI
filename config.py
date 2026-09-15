"""config.py -- TR-1um_SCLK_SPI を **59.4 版 STDCELL で作り直す**ための設定。

`TR-1um_SCLK_SPI` は行高 **64.8 µm の旧 STDCELL** で作られている。
正本は 59.4 なので、**行高が違う = 配置も配線も別物**になり、提出済みの
GDS とは突き合わせられない（`docs/02_stdcell_diff.md` / U32）。
合成からやり直し、**判定は DRC / LVS / ngspice / STA** で取る。

    cd ~/HogeHoge/LSI_Design/TR-1um_SCLK_SPI
    export TR1UM_PDK=~/HogeHoge/OpenPDK/TR-1um
    export APRTOOLS=~/HogeHoge/OpenPDK/TR-1um_APRtools
    export PYTHONPATH=$APRTOOLS/apr
    sh $APRTOOLS/syn/syn.sh          # 合成 + STA
    python3 $APRTOOLS/apr/place.py   # 以降は I2C / TD4 と同じ

## ★ フロアプランはまだ暫定

`N_ROWS` / `CH_HEIGHTS` は**合成と STA を回すための置き値**。行高が
64.8 -> 59.4 に変わるので、実際の値は配置を回して決める
（`apr/explore_rows.py` が行数の当たりを付ける）。

旧版からの持ち越しで**直すもの**:

  * コア幅 1620.0（300 トラック）は TAP の上限 1614.6（299）を超えている。
    最終間隔が 540.0 で実測ピッチ 534.6 を超えたまま提出されている（U15）。
    -> **296 トラック = 1598.4** にする。`config_base.tap_columns()` が
       痩せすぎ / 広すぎの両方を振り直すので、これで収まる。
"""
import os

from config_base import *          # noqa: F401,F403

ROOT = os.path.dirname(os.path.abspath(__file__))

# ---- 設計の同定 ----------------------------------------------------------
TOP_CELL_NAME = "spi_slave_sclk"              # コア（RTL のトップ）
CHIP_TOP_CELL = "tr_1um_3wire_SPI"            # ★ 提出時は GitHub 名を含める規約
NET_PATH = os.path.join(ROOT, "out", "spi_slave_sclk_pnr.v")

# ---- 合成 ----------------------------------------------------------------
# **特性化済みの Liberty**（面積だけの `*_area.lib` ではない）。STA を当てる
# ので実測のタイミングが要る。
SYN_LIB = None                                # 既定は stdcell_file("tr1um_typ_5v0_25c.lib")
SYN_RTL = [os.path.join(ROOT, "hdl", "spi_slave_sclk.v")]
SYN_TOP = "spi_slave_sclk"

# RTL は**素の RTL**（I2C のようにセルを直接インスタンス化していない）ので
# yosys にセルモデルを読ませる必要は無い（`SYN_CELLS_IN_SYNTH = False`）。
# iverilog の TB には要るので `char/mkcellverilog.py` で**生成する**。
#   手書きの `hdl/cells_sim.v` は 64.8 世代のもので `INV_X2` が無く、
#   ABC が INV_X2 を選んだ瞬間にゲートレベル TB がコンパイルできなくなった。
#   生成すれば `cellspec.py`（ngspice で実物と突き合わせ済み）と必ず揃う。
SYN_CELLS_V = os.path.join(ROOT, "out", "tr1um_cells.v")
SYN_CELLS_GEN = True
#   `--power` は要る。`merge_muxdffrb_rslatch.py` が書く MUXDFFRB は
#   V10 の書式で `.VDD(VDD), .GND(GND)` まで繋いである。
#   `--delay` は要らない（クロス結合が無く RSLATCH も出ない）。
SYN_CELLS_ARGS = ["--power"]
SYN_CELLS_IN_SYNTH = False
SYN_BLACKBOX = []                             # RSLATCH は使っていない

# TB は 11 本。**RTL にもネットリストにも同じものが当たる**
# （どちらも `spi_slave_sclk` を出す）。`tb_12_chip.v` はチップ階層なので外す。
_TBS = [os.path.join(ROOT, "hdl", "tb_%02d_%s.v" % (i, n)) for i, n in (
    (1, "reset"), (2, "write"), (3, "read"), (4, "bitorder"), (5, "mode0"),
    (6, "tristate"), (7, "idle"), (8, "multibyte"), (9, "clockrate"),
    (10, "random"), (11, "spidev"))]
SYN_TB_RTL = _TBS
SYN_TB_NET = _TBS
SYN_TB_INCDIR = [os.path.join(ROOT, "hdl")]   # spi_tb_common.vh

# 外部入力は BUFTH で受ける（PAD 4.8 pF を外部ドライバが直接振るため）。
# 旧 64.8 版の提出と同じ 3 本。`dis` は**フレーム中に動かない静的な選択**
# なので入れない。
BUFTH_NETS = ["sclk", "cs_n", "sdio_in"]
# 行ごとのクロックバッファ（旧 `insert_row_buffers.py`）は**入れない**。
# 配置と結び付いた 2 パスの仕組みで、正本の I2C 世代のフローには無い。
# 配線が詰まったときだけ検討する。
CLK_NETS = ["sclk_buf", "shift_clk"]

# 旧 64.8 版の提出ネットリスト。**行高が違うので配置は比べられない**が、
# セルの内訳は比べる意味がある（`docs/02_stdcell_diff.md`）。
SYN_REF_NETLIST = os.path.join(ROOT, "layout", "spi_slave_sclk_net_pnr.v")

STA_PERIOD_NS = 100.0                         # 10 MHz。推奨最大（実測 16 MHz）
STA_CLK_PORT = "sclk"                         # SPI クロック。唯一のクロック源
STA_FALSE_PATH_FROM = ["rstn"]                # 非同期リセット（recovery 未特性化）
STA_NON_SIGNAL_PORTS = []                     # 構造セルの電源ポートは無い

# ---- フロアプラン（★ 暫定。配置を回して決める）--------------------------
N_ROWS = 2
CORE_WIDTH_TRACKS = 296                       # x 5.4 = 1598.4（旧 1620.0 は TAP 超過）
CH_HEIGHTS = [140.4, 700.0, 162.0]            # 端はサイトグリッドに乗せる
NO_BOTTOM_PORTS = False

# ---- 成果物の名前 --------------------------------------------------------
LAYOUT = os.path.join(ROOT, "layout")

# ---- 配置の再現 ----------------------------------------------------------
# 新規設計なので「提出時の値」は無い。既定のまま回して、決まったら書く。
PLACE_SEED = 1
PAD_WEIGHT = 16.0                             # パッド近接は入れる（I2C 世代の改良）

finalize(globals())
