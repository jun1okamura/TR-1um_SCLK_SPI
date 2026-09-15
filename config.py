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

## フロアプラン（`explore_rows.py` で決めた）

40 セル / セル幅の総和 2,235.6 um。`apr/explore_rows.py --rows 1 2 3 4`:

    rows  最大行幅  収まる  行内で閉じる網  行を跨ぐ網  最大 ch トラック   コア WxH
       1   2235.6      NO              56           0           17    2236 x  151
       2   1166.4      OK              50           6           11    1166 x  243
       3    831.6      OK              49           7           11     832 x  356
       4    615.6      OK              45          11           13     616 x  486

**2 行**。1 行では行幅の上限 1598.4 に入らず、3 行以上は行を跨ぐ網が増えて
コアが縦に伸びるだけ。旧 64.8 版も 2 行だった。

旧版からの持ち越しで**直したもの**:

  * コア幅 1620.0（300 トラック）は TAP の上限 1614.6（299）を超えていた。
    最終間隔が 540.0 で実測ピッチ 534.6 を超えたまま提出されている（U15）。
    -> **296 トラック = 1598.4**（I2C / TD4 と同じ）。
  * 行ごとのクロックバッファ（`insert_row_buffers.py`）は入れない。
    配置と結び付いた 2 パスの仕組みで、正本の I2C 世代のフローには無い。
"""
import os

from config_base import *          # noqa: F401,F403

ROOT = os.path.dirname(os.path.abspath(__file__))

# ---- 設計の同定 ----------------------------------------------------------
TOP_CELL_NAME = "spi_slave_sclk"              # コア（RTL のトップ）
# ★ 命名規則は `tr_1um_<GitHub 名>_<設計の識別子>`（`info.yaml` の注記）。
#   提出時の `tr_1um_3wire_SPI` は **GitHub 名が入っていなかった**。
CHIP_TOP_CELL = "tr_1um_jun1okamura_3wire_spi"
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
SYN_REF_NETLIST = os.path.join(ROOT, "reference", "v64_8", "layout",
                               "spi_slave_sclk_net_pnr.v")

STA_PERIOD_NS = 100.0                         # 10 MHz。推奨最大（実測 16 MHz）
STA_CLK_PORT = "sclk"                         # SPI クロック。唯一のクロック源
STA_FALSE_PATH_FROM = ["rstn"]                # 非同期リセット（recovery 未特性化）
STA_NON_SIGNAL_PORTS = []                     # 構造セルの電源ポートは無い

# ---- フロアプラン --------------------------------------------------------
N_ROWS = 2
CORE_WIDTH_TRACKS = 296                       # x 5.4 = 1598.4（旧 1620.0 は TAP 超過）
# **配線前の予算**。step10 の圧縮が使わなかったトラックを削るので、
# 多めでよい（マクロが無いので TD4 のような貫通の問題も無い）。
# 端の 140.4 / 162.0 は I2C と同じ。どちらも 5.4 の倍数（26 / 30 トラック）。
# 真ん中の 700.0 は explore_rows の見積り 11 トラック = 59 um に対して十分。
# 旧 64.8 版の提出は真ん中が 900.0 だった。
CH_HEIGHTS = [140.4, 700.0, 162.0]
# ★ 下辺は**塞ぐ**。チップではコアを上へ寄せて、下をまるごとロゴに
#   明け渡す（提出済みの 64.8 版と同じ絵）。下辺にポートを出すと
#   ロゴの帯を跨がないと外へ出られない（I2C と同じ理由）。
NO_BOTTOM_PORTS = True

# ---- ボンドパッドの割り当て（`docs/11_frame_io.md` §2）------------------
# 旧 `scripts/gen_top_routing_plan.py` の PAD_MAP をそのまま持ってくる。
# `"HIZ"` はそのパッドの HIZ ピンを何が駆動するか。`VDD`/`GND` は固定。
# パッド 8 と 16 は使わない（電源で埋まる位置）。
PAD_MAP = {
    1:  {"role": "SCLK",    "P": "sclk",                                   "HIZ": "VDD"},
    2:  {"role": "SDIO",    "P": "sdio_in",    "OUT": "sdio_out", "HIZ": "sdio_oe_n"},
    3:  {"role": "CS",      "P": "cs_n",                                   "HIZ": "VDD"},
    4:  {"role": "TEST",                       "OUT": "byte_end", "HIZ": "GND"},
    5:  {"role": "DIS",     "P": "dis",                                    "HIZ": "VDD"},
    6:  {"role": "DATA[0]", "P": "tx_data[0]", "OUT": "rx_data[0]", "HIZ": "dis"},
    7:  {"role": "DATA[1]", "P": "tx_data[1]", "OUT": "rx_data[1]", "HIZ": "dis"},
    9:  {"role": "DATA[2]", "P": "tx_data[2]", "OUT": "rx_data[2]", "HIZ": "dis"},
    10: {"role": "DATA[3]", "P": "tx_data[3]", "OUT": "rx_data[3]", "HIZ": "dis"},
    11: {"role": "DATA[4]", "P": "tx_data[4]", "OUT": "rx_data[4]", "HIZ": "dis"},
    12: {"role": "DATA[5]", "P": "tx_data[5]", "OUT": "rx_data[5]", "HIZ": "dis"},
    13: {"role": "DATA[6]", "P": "tx_data[6]", "OUT": "rx_data[6]", "HIZ": "dis"},
    14: {"role": "DATA[7]", "P": "tx_data[7]", "OUT": "rx_data[7]", "HIZ": "dis"},
    15: {"role": "RSTN",    "P": "rstn",                                   "HIZ": "VDD"},
}
# コアには出ているがパッドに繋がないもの。`data_oe` は `~dis` そのもので、
# 使う側のパッドは DIS の網から HIZ を取る。
UNBONDED = {"data_oe"}

# ---- チップの床（`docs/23_flow_chip.md`）---------------------------------
# RING_OSC は積まない（I2C だけの構造）。コアを上へ寄せて下をロゴに明け渡すので、
# **VDD も GND も上辺のタップから**取る（I2C と同じ。RING_OSC の帯まわりだけ無い）。
# 下辺のタップは開放。TAP 柱が全行を縦に貫いて上辺と繋がっている。
CHIP_POWER = "top_only"
# 段の順番は TD4 と同じ：組立 -> 配線 -> トップピン -> **最後にロゴ**。
CHIP_ROUTE_IN_GDS = os.path.join(ROOT, "layout", "chip", "step1_assembled.gds")
CHIP_LOGO_IN_GDS = os.path.join(ROOT, "layout", "chip", "step3_top_pins.gds")
CHIP_LOGO_OUT_GDS = os.path.join(ROOT, "layout", "chip", "step4_final.gds")
CHIP_FINAL_GDS = CHIP_LOGO_OUT_GDS
# コアは squeeze 後 1611.0 x 308.7 で、ダイ中心に置くと y は -154.35…154.35。
# 開口の壁は四辺とも 920.0 なので上下のチャネルが 765.65 um ずつ空く。
# コアは中央寄せではなく**ロゴの帯の上**に置く（`config_base.core_bottom_y()`）。
STACK_BELOW_CORE = True
CORE_LOGO_GAP = 20.0
# 上のチャネルに GND 内側 / VDD 外側の M1 バス 2 本（I2C と同じ形）。
# **ただし値は I2C とは違う。** 全信号が上辺から出るのでレーンが 13 本要り、
# 帯は R 810.0..874.8 を占める（上限は GND リング 884 から 875.3）。
# レーン 0 は 810.0 より外へは出せない一方、コアの端 ±805.5 からは
# 810-1.7 = 808.3 で 2.8 µm 空くのでこれが下限。バスはその下に詰める:
#     コア上端 776.2
#     GND バス 786.0 -> M1 781.0…791.0（コア上端と 4.8）
#     VDD バス 800.0 -> M1 795.0…805.0（GND バスと 4.0）
#     レーン 0  810.0 -> M1 縁 809.1（VDD バスと 4.1。要 1.4）
# I2C の既定（790 / 804 / 815.4）のままだと VDD バスの上端 809.0 と
# レーン 0 の縁 809.1 が 0.1 µm しか空かず M1.S1 で落ちる（実際に出した）。
# ★ レーンの始まりだけ 815.4 -> **810.0** に下げる。全信号が上辺から出るので
#   帯が 13 本要り、815.4 始まりだと最上が 880.2 になって GND リング
#   （884、上限 875.3）に当たる。810 始まりなら最上 874.8 で収まる。
#   815.4 だったのは I2C の RING_OSC の帯（810）を避けるためで、
#   SCLK_SPI には帯が無い（TD4 も 810）。コアの端 ±805.5 とは
#   810-1.7 = 808.3 で 0.8 µm 空く。
#   **リングは動かさない**（GND 884 / VDD 902）。外へ寄せると VDD リングの
#   via と M1 へ跳ねる via（y=914.5）が重なって V1.W1（カット 1.4 上限）に
#   引っかかる（実際に 10 件出した）。
CHIP_LANE_R0 = 810.0
CHIP_GND_BUS_Y = 786.0
CHIP_VDD_BUS_Y = 800.0

# ---- ロゴ ----------------------------------------------------------------
# 提出済みの 64.8 版と同じ絵にする: コアを上へ寄せ、空いた下に**全幅のロゴを
# 2 枚**積む。帯の上端がコアの下端を決める（`STACK_BELOW_CORE`）。
#   全幅・等倍 = 1,585 x 315 µm。2 枚 + 間隔 100 で 730 µm。
#   下辺のレーンは R 815.4 から外なので、y は -800 まで使える。
#   帯の上端 415.6 -> コア下端 435.6、コア高 340.6 で**コア上端 776.2**。
#   GND バスの M1 下端 781.0 と 4.8 µm 空く。
LOGO_BOX = (-795.0, -800.0, 795.0, 415.6)
LOGO_SCALE = 1                                # 等倍
LOGO_COLS = None                              # 全幅（紋章 + "OpenSUSI"）
LOGO_ROWS = 2                                 # 縦に 2 枚
LOGO_GAP = 100.0

# ---- フレームの LVS ソース ----------------------------------------------
# ★ この設計の `lef/simulation` は **xschem の作業場への symlink**
#   （リポジトリの外を指す）。既定の置き場は使えないので `lef/` 直下に置く。
#     python3 $APRTOOLS/apr/mkframespice.py <frame.gds> OSS_FRAME_GIO \
#             --no-combine -o lef/OSS_FRAME_GIO_nocombine.spice
FRAME_LVS_SPICE = os.path.join(ROOT, "lef", "OSS_FRAME_GIO_nocombine.spice")

# ---- 成果物の名前 --------------------------------------------------------
LAYOUT = os.path.join(ROOT, "layout")

# ---- 配置の再現 ----------------------------------------------------------
# 新規設計なので「提出時の値」は無い。既定のまま回して、決まったら書く。
PLACE_SEED = 1
PAD_WEIGHT = 16.0                             # パッド近接は入れる（I2C 世代の改良）

finalize(globals())
