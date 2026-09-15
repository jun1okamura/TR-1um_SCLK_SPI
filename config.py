"""config.py -- TR-1um_SCLK_SPI を **59.4 版 STDCELL で作り直す**ための設定。

`TR-1um_SCLK_SPI` は行高 **64.8 µm の旧 STDCELL** で作られている。
正本は 59.4 なので、**行高が違う = 配置も配線も別物**になり、提出済みの
GDS とは突き合わせられない（`docs/02_stdcell_diff.md` / U32）。
合成からやり直し、**判定は DRC / LVS / ngspice / STA** で取る。

    cd ~/Dropbox/98_LSI_Design/TR-1um_SCLK_SPI
    export TR1UM_PDK=~/Dropbox/91_OpenPDK/TR-1um
    export APRTOOLS=~/Dropbox/91_OpenPDK/TR-1um_APRtools
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
