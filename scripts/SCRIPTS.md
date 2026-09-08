# scripts/ ガイド

`scripts/` 配下のスクリプトの説明資料。RTL から配置配線用ネットリストまでの
再現可能なパイプラインを構成する。設計方針は
[`TR-1um_Async_I2C/script/`](../../TR-1um_Async_I2C/script/) を踏襲しつつ、
特定の設計・セル名・ネット名に依存しないよう全て引数化してある
(`SCRIPTS.md` §2/§3 の `insert_row_buffers.py` / `insert_bufth_scl_sda.py` /
`gen_liberty.py` の一般化版)。

すべてのスクリプトは `--help` を持ち、`main()` を直接呼ぶ形でも使える。

---

## 0. エントリポイント

| スクリプト | 役割 |
|---|---|
| `build.sh` | RTL → 配置配線用ネットリストの全工程。合成 → MUXDFFRB統合 → BUFTH挿入 → 行バッファ挿入 → 規模レポート。環境変数 `TOP=` `SRC=` `ROWS=` `LIB=` `BUFTH_NETS=` `CLK_NETS=` `ROW_ASSIGNMENT=` で上書き可能。 |
| `run_tests.sh` | `hdl/tb_*.v` の全テストベンチを、RTL / 合成直後NET / 配置配線用NET の**3つのビュー**に対して同一ソースで実行。存在しないビューは自動スキップ。引数でテストベンチを絞れる (`scripts/run_tests.sh tb_05_mode0`)、`DUMP=1` でVCD出力。 |

```sh
scripts/build.sh                    # 合成〜P&R用ネットリスト生成
scripts/run_tests.sh                # 全12テストベンチ × 3ビュー
ROWS=6 scripts/build.sh             # 6行構成で行バッファを入れ直す
DUMP=1 scripts/run_tests.sh tb_05_mode0   # 波形付きで1本だけ
```

---

## 1. 論理合成

| スクリプト | 役割 |
|---|---|
| `synth.ys.in` | Yosys スクリプトのテンプレート。`build.sh` が `@TOP@` / `@LIB@` / `@SRC@` を置換して `layout/synth.ys` として実行するので、任意のトップ・任意のLibertyに使い回せる。 |
| `gen_liberty.py` | `lef/cell_info.json` から合成用 Liberty を生成。**面積は実測値**(`lef/TR-1um_STDCELL.gds` のセルbounding box)を使うため、ABCが本物のシリコン面積で最適化し、`gate_count.py` の数字と一致する。タイミングはプレースホルダ(STA不可)。`-i` `-o` `--skip` で入出力とセル除外を指定。 |

I2C版の `gen_liberty.py` はセル表をソース内に持ち `area: 1` の相対値だったのに対し、
こちらはデータ(`lef/cell_info.json`)とコードを分離し、実面積・実トランジスタ数を使う。

---

## 2. 合成後ネットリスト加工

各スクリプトは入力ネットリストのテキストを**最小限だけ書き換える**方式
(再生成しない)なので、コメントや書式、パーサが理解しない記述はそのまま残る。

| スクリプト | 役割 |
|---|---|
| `netlist_util.py` | Yosys `write_verilog -noattr` 出力の最小パーサ/ライタ。以下3本の共通依存。インスタンス抽出、ネットのドライバ/ロード解析、スパン置換、wire/インスタンス追加。 |
| `merge_muxdffrb.py` | `MUX2` + `DFFRB` のペアを `MUXDFFRB` 1個に統合(I2C版V10で手作業だったセルマージの自動化)。MUX2出力がそのDFFRBのDピン**だけ**を駆動する場合のみ統合するので常に安全。トランジスタ数不変・面積10.3%減/ペア。`--mux-cell` `--ff-cell` `--merged-cell` `--d-pin` で任意のセル対に適用可能。 |
| `insert_bufth.py` | 指定したトップレベル入力ネットに閾値バッファを挿入(`sclk` → `BUFTH` → `sclk_buf` → 全内部シンク)。ポート宣言や `wire` 宣言は書き換えない。`--nets` `--cell` `--suffix`。I2C版が scl/sda_in 決め打ちだったのを一般化。 |
| `insert_row_buffers.py` | 高ファンアウトのクロック系ネットに**配置行ごとに1個**のバッファを入れ、各行のシンクだけを駆動する行ローカルネットに分ける。行またぎネットが消えるのでチャネルルータの特別扱いが不要になる。行割り当ては配置結果のJSON(`--row-assignment`、`{inst: row}` / `{row: [inst]}` 両形式)か、配置前なら `--rows N` の均等分割。 |

**適用順序**: `merge_muxdffrb` → `insert_bufth` → `insert_row_buffers`
(BUFTH を先に入れることで、行バッファ段がバッファ済み信号から駆動される)。

---

## 3. 配置(Placement)

| スクリプト | 役割 |
|---|---|
| `place.py` | nrow配置本体。**各STEPごとにGDSとJSONを `layout/stepN/` に残す**。step1=行割り当て(FM分割)、step2=行内順序最適化(バリセンタ反復、HPWL評価)、step3=TAP挿入(固定ピッチのセグメント分割)、step4=FILL挿入で行幅を厳密に揃える(最終)。配置規約はI2C実チップのGDSから実測したもの — prBoundary(0..W × 0..64.8)でアバット、回転・反転なし、行はx=0から行幅ちょうどまで、TAP2は x=0 / 534.6 / 1069.2 / 行幅-10.8。FILLはI2C版と同じく行内に分散配置(`--fill-mode end` で右端寄せも可)。`--rows` `--row-width` `--restarts` `--order-passes` `--seed`。 |
| `verify_placement.py` | 配置の検証。インスタンス被覆(過不足・重複)、行内のアバット/重なり、5.4µmトラックグリッド整合、行幅一致、TAP位置、行のy重なりとコア枠内収納、GDSの参照数と実寸の突き合わせ。占有率・HPWL・チャネル高もレポート。 |
| `plot_placement.py` | 各STEPのPNG可視化(`layout/placement_steps.png`)。テープアウトフローの一部ではなく目視確認用。 |

`place.py` は `layout/row_assignment.json` も出力する。これを
`insert_row_buffers.py --row-assignment` に食わせて行バッファを実配置ベースで
入れ直し、再度 `place.py` を回す**2パス**が正規の手順(I2C版
`design_notes.md` §40 と同じ考え方)。

```sh
scripts/place.py                                    # 1パス目
scripts/insert_row_buffers.py layout/spi_slave_sclk_net_bufth.v \
    layout/spi_slave_sclk_net_pnr.v --nets sclk_buf,shift_clk \
    --cell BUF_X2 --row-assignment layout/row_assignment.json
scripts/run_tests.sh                                # 等価性再確認
scripts/place.py                                    # 2パス目
scripts/verify_placement.py
```

---

## 4. 配線(Routing)

I2C版(`TR-1um_Async_I2C/script/`)のフローをそのまま使う。移植の詳細と
変更点は [`PORTING.md`](PORTING.md)。**アルゴリズムは無改変**で、パス・
トップセル名・行数依存箇所だけを本プロジェクト向けに置換している。

| スクリプト | 役割 |
|---|---|
| `route.py` | ステージドライバ(I2C版 `run_v10_pipeline.py` 相当)。step5〜step10 を順に実行し、**各STEPのGDSを `layout/stepN/` に残す**。`--from` / `--to` で範囲指定、`--ch-heights` でチャネル予算を上書き。 |
| `spi_config.py` | パス・トップセル名・幾何定数の単一ソース。PDKの `via_1` PCell ディレクトリ探索(`TR1UM_PDK` で明示可)もここ。 |
| `gen_placement_json.py` | `place.py` の step4 出力 → I2C版ルータが読む配置JSONスキーマへ変換(LEFのピン矩形を絶対座標化し、ネットリストのネット名を解決。TAP直後のFILL2を `FILLPRI_*` として優先M2コリドーに指定)。 |
| `port_i2c_scripts.py` | `i2c_ref/` の原本から本プロジェクト版を再生成。置換ルールが移植の唯一の記録。 |
| `port_rules.py` | 上記のうち、コード片を丸ごと差し替えるパッチ(三重引用符を含むためモジュール分離)。 |
| `verify_port_connectivity.py` | **全トップレベルポートが実際にセルピンに届いているか**をジオメトリで検証。M1+M2をV1で繋いだ連結成分を作り、各ポートのPINマーカーとそのネットのセルピンが同じ成分にあることを確認する。`verify_connectivity_nrow_fm*.py` はチャネルルータが記録した44ネットしか見ず、スタブ30本(ポート→セルピン1本)が死角になっていた — §4.2の不具合が最終GDSまで残った理由。`route.py` が step10 の後に自動実行する。 |
| `plot_layout.py` | 配線結果のPNG可視化(フロー外、目視確認用)。 |
| `cell_usage.py` | 最終配置からSTDCELL利用リストを生成(`docs/cell_usage.md`)。論理/物理/未使用に分け、個数・寸法・面積・Tr数・信号ピンを表にする。 |
| `drc_check_cells.py` | **STDCELLの全セルに単体でDRC**をかける。チップレベルのDRCは配置されたセルしか見ないので、壊れたライブラリセルは何かが置くまで見えない — `BUF_X2` のM1間隔違反が最終GDSまで残った理由(design_notes §14.6)。**GDSを触ったら回す。** |

移植した原本(直接編集しない — `port_i2c_scripts.py` で再生成される):
`route_channels_nrow_fm.py`(中核ルータ、5パス)、`ripup_reroute_shorts.py`、
`route_top_pins_nrow_fm.py`、`add_power_pins_nrow_fm.py`、
`squeeze_channels_nrow_fm.py`、`drc_check_nrow_fm.py`、
`verify_connectivity_nrow_fm{,_m1m2}.py`、`lef_parser.py`、
`netlist_parser.py` ほか。

### ステージ構成

| STEP | 内容 | 成果物 |
|---|---|---|
| step5 | **配置JSONの変換**(`gen_placement_json.py`)+ 配置GDS + チャネル注釈 | `layout/placement_nrow_fm.json` / `layout/step5/route_step_1_placement.gds` |
| step6 | チャネル配線(内部5パスも各々GDS化) | `layout/step6/route_step_2_*.gds` |
| step7 | 短絡のリップアップ/再配線 | `layout/step7/route_step_3_ripup_reroute.gds` |
| step8 | トップレベルピンのコア端引き出し | `layout/step8/route_step_4_top_pins.gds` |
| step9 | VDD/GND チップレベルピン | `layout/step9/route_step_5_power_pins.gds` |
| step10 | チャネル圧縮(未使用トラック除去) | `layout/step10/route_step_6_squeezed.gds` |

```sh
scripts/gen_placement_json.py     # place.py の結果をルータ用スキーマへ
scripts/route.py                  # step5〜step10 + DRC/接続性チェック
scripts/route.py --from 6 --to 6  # 配線だけやり直す
scripts/plot_layout.py layout/step10/route_step_6_squeezed.gds
```

### 4.1 squeeze の PINレイヤ保護

`squeeze_channels_nrow_fm.py` は配線が使っていないYスライスを削除するが、
原本のままだとトップ辺のPINマーカーも消える。マーカーは**コア境界のちょうど
上**に描かれるため、「最後に使われたトラックより上のヘッドルームを潰す」規則の
巻き添えで**高さ0**になり(下辺のマーカーはy=0からコア内側へ描かれるので無傷)、
ラベルもピンから外れて `gen_lef.py` / LVS抽出の「PIN形状の内側にテキスト
ラベルがある」規約が壊れる。

`port_rules.py` の `SQUEEZE_PIN_PROTECT` で、`build_y_map` に
**保護Y区間**の概念を追加した。トップセル自身のPINレイヤ(48/1, 49/1)と
テキストレイヤ(48/0, 49/0)が占めるY区間を `collect_protect_y()` で集めて
マージし、潰し区間がそこに重なる部分だけ identity のまま残す。

```
protecting 2 Y interval(s) holding PIN markers/labels: 0.0-3.0, 1326.6-1329.6
core height: 1329.6 um -> 324.9 um (-1004.7 um, -75.6%)
```

セル内部のピンは行バンド内(元から identity 写像)にあるので走査対象外。
保護区間が無ければ従来どおりの挙動になる。

### 4.2 ポート名のネットリスト導出

`highlight_top_pins_nrow_fm.py`(`route_top_pins_nrow_fm.py` の `gather_pins`
が使う)は**I2C設計のポート名を直書き**していた:

```python
SCALAR_PORTS = ["rst_n", "scl", "sda_in", ...]
BUS_PORTS    = {"tx_data": 8, "rx_data": 8}
PORT_NET_ALIAS     = {"addr_match": "addr_ok", "rw": "rw_bit"}
BUS_PORT_NET_ALIAS = {"rx_data": "rx_data_r"}
```

`gather_pins` はこの2つに載っているポートしか探さないので、他設計では
スカラーポートが黙って全部スキップされる。本設計では `BUS_PORTS` だけが
たまたま一致していたため**バス16本だけが引き出され、スカラー9本が
未配線のまま**残っていた(`BUFTH` の入力 `sclk`/`cs_n`/`sdio_in` を含む)。

`port_rules.py` の `HIGHLIGHT_PORTS_FROM_NETLIST` で、この4つを
**ネットリストのポート宣言から導出**するよう置き換えた。
`assign port = net;` の別名も拾う — スカラーは `netlist_parser` の
union-find リゾルバが解決するが、**バスの別名はビットごとに展開が必要**
なので `BUS_PORT_NET_ALIAS` に入れる(本設計の `assign rx_data = rx_data_r;`
がまさにこれ)。ポート方向 `PORT_DIR` も同じ宣言から導出する。

再発防止として `route.py` が step8 の直後と step10 の後に
「全ポートがPINマーカーを持つ」「全PINマーカーがセルピンに届く」の
2つのチェックを自動実行する。

チャネル予算は**広めに取って配線し、step10で圧縮する**(`spi_config.py` の
`ROUTE_CH_HEIGHTS`)。ルータのジョグ機構は行またぎ1本ごとに新しいトラックを
確保するため、`place.py` のネット交差数ベースの見積もりでは足りない。

---

## 5. LVS用ネットリスト

| スクリプト | 役割 |
|---|---|
| `gen_cell_spice.py` | `lef/TR-1um_STDCELL.spice`(セルのトランジスタ実体)を生成。各 `.subckt` は `TR-1um_I2C_2026/src/tr_1um_i2c_slave_async.cir`(I2C版MPW提出netlist = 同じセルライブラリで実機LVS一致済み)から**そのまま**抜き出す。`.cir` に無いセル(`BUF_X2`)は xschem の export `simulation/<CELL>.spice` を使い、それも無ければ `LOCAL_BODIES` で定義。素子ゼロの `TAP2` と、トップに展開する `FILL3` は対象外。`--check` で生成物の鮮度確認、`--verify-only` で照合のみ。入力パスは `I2C_REF_CIR` で差し替え可。<br>**xschem照合**: `layout/step10/simulation`(= `~/.xschem/simulations`)が見えるときは、出力する各セル実体を xschem 自身の `<CELL>.spice` と素子単位で自動照合し、食い違えば停止する。MUXDFFRB の階層exportは I2C版と同じ手順でフラット化、`m=N` は並列N個に展開してから比較。`XSCHEM_SIM_DIR` で場所を指定可。 |
| `gen_lvs_spice.py` | `layout/spi_slave_sclk_net_pnr.v` から LVS参照ネットリスト `layout/<TOP>.spice` を生成。**設計固有の定数を持たない**: トップポート順と方向は `module` ヘッダと `input`/`output` 宣言から、セルのSPICEピン順は `TR-1um_STDCELL.spice` の `.subckt` 行から、ネット別名は `assign` の union-find(スカラー / ビット指定 / バス全体 / 部分選択)から導出する。解釈できない `assign` はエラーで止める。FILL2はサブサーキット呼び出し(コール順は実体の `.subckt` 行から取る)、FILL3は素子をインライン展開(I2C版でLVS一致した形)。出力は `layout/` と `layout/step10/simulation/` の2箇所(`--no-sim-out` で後者を抑止)。 |
| `check_cell_spice.py` | LVSにかける前の突き合わせ。**(1)** セル実体 vs `lef/TR-1um_STDCELL.gds`: poly∩activeでゲートを抜いてW/Lを実測し、**折り畳み不変量**(PMOS総幅・NMOS総幅・チャネル長)を比較する(BUFTHは1素子を2フィンガーに折って描いてあるので素子数は一致しない)。**(2)** 生成ネットリスト vs 配線後GDSのインスタンス数。`gdstk` が要る。 |

```sh
scripts/gen_cell_spice.py        # lef/TR-1um_STDCELL.spice
scripts/gen_lvs_spice.py         # layout/spi_slave_sclk_nrow_fm.spice
scripts/check_cell_spice.py      # GDSと突き合わせ
```

> `lef/BUF_X2.sch` は元々BUF_X1と同じ4素子だった(GDSの実体は6素子)。
> `design_notes.md` §15.2 を参照。

---

## 6. チップ統合(GIOフレーム)

| スクリプト | 役割 |
|---|---|
| `frame_pins.py` | `lef/TR-1um_frame_25x25.gds` の `OSS_FRAME_GIO` から `P<n>` / `HIZ<n>` / `OUT<n>` 42端子の実座標・辺・レイヤを読む。I2C版は LEF から書き写した表を手で保守していたが、その写しを廃止した。単体実行で一覧を印字。 |
| `assemble_top.py` | コアをフレームに落とし込んで `layout/chip/step1_assembled.gds`(セル `tr_1um_3wire_SPI`)を作る。**配置のみ、配線しない**(I2C版 `assemble_top_v10.py` と同じ区切り)。オフセットは `spi_config.chip_geometry()` が導出。PTECTボックスも置き、リング内壁の位置を毎回実測して設定値と照合する。 |
| `gen_top_routing_plan.py` | `layout/chip/gio_connections.json`(論理接続表)と `layout/chip/signal_routing_plan.json`(ルータ用の実座標)を生成。手書きは `PAD_MAP` のみ。全コアポートを突き合わせるので割り当ての抜けはエラーになる。 |
| `check_top_channels.py` | 配線前のチャネル容量確認。T-R-B-Lのループを1次元に展開し(I2C版の unrolled ring interval と同じ)、各ネットを最短の弧として重ねて必要トラック数を数える。ジョグ・ビアを数えない下限値。 |
| `plot_chip_floorplan.py` | `layout/chip/floorplan.png`。ダイ・内壁・42端子・コア・PTECT・コリドーだけを描く(GDSビューアはパッドリングの全ポリゴンを描いてしまい配置確認には向かない)。 |

```sh
scripts/assemble_top.py
scripts/gen_top_routing_plan.py
scripts/check_top_channels.py
scripts/plot_chip_floorplan.py
```

> コアは `sdio_oe_n`(アクティブLOW)を出すので `HIZ2` に直結できる。
> チップトップは「パッドリング + コア」だけで、追加セルは無い
> (`design_notes.md` §16.6)。

---

| `route_chip.py` | コアとGIOリングの配線。`layout/chip/step1_assembled.gds` + `signal_routing_plan.json` → `layout/chip/step2_routed.gds`。リングエンジン(`perimeter_s` / `ring_waypoints` / `project_to_R` / `seg_layer` / `unroll` とレーン詰め)は `i2c_ref/route_gio_core_v10.py` から**逐語コピー**。本プロジェクト固有なのは**コア下(U)コリドー** — PTECTに塞がれた下辺ピン11本を横に逃がす経路で、逃がす向き(L/R)は全探索で決める。電源はVDDがTコリドーのM1バス+細いライザー9本、GNDがUコリドーのM1バス+左右の脚。1ルート=1本のポリラインで描くので、レイヤが変わる角のビアが抜けない。 |
| `check_chip.py` | チップ配線の検証。**(1)** DRCを配線前後の両方で走らせ**増えた分だけ**座標付きで報告(パッドリングは元からマーカーを数個持っている)。**(2)** PTECT(63/1)内の金属。**(3)** KLayoutの `LayoutToNetlist` で抽出し `probe_net` で各ネットの端点を引いて、断線と短絡、電源2系統の独立、**ボンドパッドのLVSピンが名前どおりのネットに乗っているか**、および**チップレベル参照ネットリストが繋いでいる点がレイアウトでも同じネットか**(断線・ショートの両方向)を確認。`step3_top_pins.gds` があればそちらを見る。 |
| `gen_lvs_spice_top.py` | チップレベルのLVS参照ネットリスト `layout/chip/<CHIP_TOP>.spice` を生成。コア(`layout/<TOP>.spice`)+ パッドリング(`lef/OSS_FRAME_GIO.spice`)+ 結線マップ。両インスタンスのポート順は各 `.subckt` 行から読み、ネットは結線マップから導出する。**トップポートは16本**(レイアウト側のピン数と一致していないとKLayoutは照合を試みない)。説明のつかないポートは固有の `NC_*` で個別に浮かせる。`layout/step10/simulation/` にも同じものを書く。 |
| `add_top_pins.py` | ボンドパッド16箇所にLVS用のピンを置く(`step2_routed.gds` → `step3_top_pins.gds`)。M2PIN(49,1)に3×3 µmのボックス + TXM2(49,0)に**ボックス中心**へピン名TEXT(20 µm) — コアセルおよびI2C版チップと同じ規約。座標と名前はフレームGDSの `OSS_PAD` インスタンスとその上のラベルから読む(I2C版は位置で名前を割り当てていた)。書き込む前に各中心が実M2の上にあることを確認する。 |
| `place_logo.py` | PTECTを外して空いたコア下へ**OpenSUSIロゴを2段**置く(`step3_top_pins.gds` → `step4_final.gds`)。5.0 µmグリッドのONセルごとに**3.0 × 3.0 µmの孤立M2ドット**をセル中央に置くので、直交隣接は2.0 µm(M2最小スペースちょうど)、斜め隣接は2.83 µm。塗り潰しブロックだと斜め接触を手で当てる必要があるが(I2C版 §105)、ドットならその状況が起きない。ビットマップは `lef/opensusi_logo.txt`(317 × 63、5,785ドット。**コメント文字は `%`** — `#` はONセルなので `#` をコメントにすると左端がONの行が消える)。置く前にロゴ単体でDRCし、置いた後にチップ全体で**増えたマーカーが0**であることを確認してから書き出す。 |
| `pin_list.py` | `docs/pin_list.md`(ピン配置表)を生成。ボンドパッドの座標は `lef/TR-1um_frame_25x25.gds` の `OSS_PAD` インスタンスとその上のラベルから、役割とネットは `gio_connections.json` から、方向はネットリストのポート宣言から取り、**互いに突き合わせる** — 接続表に無いパッドやパッドの無いコアポートは空欄ではなくエラーになる。 |
| `plot_layout.py` | 配線結果のPNG。`--cell` でチップセルを指定(チップGDSはトップレベルセルが複数ある)、`--figsize 13x13` で正方形。 |

```sh
scripts/route_chip.py
scripts/add_top_pins.py
scripts/place_logo.py                # PTECT削除後の空きへロゴ2段 → step4_final.gds
scripts/gen_lvs_spice_top.py
scripts/check_chip.py                # 既定で最新のステージ(step4_final)を見る
scripts/pin_list.py
scripts/plot_layout.py layout/chip/step4_final.gds --cell tr_1um_3wire_SPI --figsize 13x13 \
    -o layout/chip/step4_final.png
```

---

## 7. ngspice によるチップレベル検証

コアだけでなく**パッドリングを含むチップ全体**を、PDKの実デバイスモデルで
トランジスタレベルに流す。理想化した箇所がひとつも無いので、パッドの極性、
ドライバの喧嘩、レールまで届かないレベルといった、論理シミュレーションでは
原理的に見えない不具合をここで初めて捕まえられる。

| スクリプト | 役割 |
|---|---|
| `gen_chip_sim_ready.py` | LVS用ネットリスト `layout/chip/<CHIP_TOP>.spice` を ngspice が読める形に直して `ngspice/<CHIP_TOP>_sim_ready.spice` を書く。直すのは機械的な5点だけで、ポート・ネット・階層・素子寸法は一切触らない。**(1)** PDKの `PMOS`/`NMOS`/`MPE`/`MNE` は `.model` ではなく `.subckt` なので、インスタンスの行頭は `M` ではなく `X`。**(2)** `rx_data[0]` → `rx_data_0`。**(3)** `*.PININFO` の `+` 継続行はコメントの継続にならないので `*+`。**(4)** ESDダイオードの `A=`/`P=` → `AREA=`/`PJ=`。**(5)** `NMOSE` → `MNE`(PDKに `NMOSE` は無い)。置換数を全部数えて印字し、書き出す前にKLayoutのSPICEリーダで読み直す。 |
| `gen_chip_tb.py` | テストベンチ `ngspice/tb_chip_spi.spice` と期待値 `ngspice/tb_chip_spi_expected.json` を生成。パッドの割り当ては `gio_connections.json` から読むので配置表とずれない。**双方向パッドは必ず片側しか駆動しない** — DATAはDISに追随する `TXGATE`、SDIOはWRITEフレーム中だけ閉じる `MGATE` で、電圧制御スイッチ越しに繋ぐ(I2C版と同じ手口)。`.tran` の第4引数 **Tmax = 1 ns**。 |
| `gen_sim_from_extracted.py` | **抽出ネットリスト**(KLayoutのLVS抽出 `layout/chip/<CHIP_TOP>.extracted`)を ngspice で流せる形にして `ngspice/<CHIP_TOP>_extracted_sim.spice` を書く。`gen_chip_sim_ready.py` が回路図側(「こうあるべき」)を変換するのに対し、こちらは**レイアウトが実際にそうなっている側**。各素子が抽出器の実測 `AS`/`AD`/`PS`/`PD` を持つので、接合容量がPDKの既定値(`w*sdwidth`)ではなく描いた通りになる。直すのはKLayout固有の名前だけ(`\$107`→`net_107`、`PAD\|VDD`→`PAD_VDD`、`X$1`→`X_1`、`tx_data[0]`→`tx_data_0`、ダイオードの `A=`,`P=`、`NMOSE`)。**改名の衝突を全部照合してから書く**(別々のネットが同じ名前になったら、後からは本物のショートと区別がつかない)。トップのポートはボンドパッド順に並べ替えるので、TBの `--netlist` を差し替えるだけで入れ替わる。参照ネットリストとセル構成・総P/Nチャネル幅も突き合わせる。 |
| `sweep_sclk.py` | **通信速度の測定**。同じ12チェックを `--sclk` で周波数軸に沿って上げていき、壊れる周波数と各点の clock-to-out を報告する。大事なのは1つの数字ではなく**どのチェックが先に落ちるか**で、`read_byte` なら出力経路、`rx_wr*` なら入力経路かシフトレジスタ、`test_*` ならビットカウンタ、と落ち方が原因を名指しする。各周波数は独立したディレクトリで走るので、落ちた点のネットリストとログがそのまま残る。**シミュレータが落ちた点は FAIL ではなく ERROR** として区別する(部品に無い上限を報告しないため)。`--load-pf` で全信号パッドの外部容量、`--master-ohm` でTB側マスタの出力インピーダンスを振れる。 |
| `check_chip_sim.py` | ngspiceのログから `.measure` の結果(`name = value` 行)を拾い、期待値JSONと突き合わせて PASS/FAIL を印字。評価できなかった measure は `failed` と出るので、その項目だけFAILにして残りは続ける。 |

```sh
scripts/gen_chip_sim_ready.py
scripts/gen_chip_tb.py
cd ngspice && ngspice -b tb_chip_spi.spice > spice_chip.log 2>&1 && cd ..
scripts/check_chip_sim.py ngspice/spice_chip.log

# 抽出ネットリストで同じTBを流す
scripts/gen_sim_from_extracted.py
scripts/gen_chip_tb.py --netlist tr_1um_3wire_SPI_extracted_sim.spice \
    -o ngspice/tb_chip_spi_extracted.spice -j /dev/null
cd ngspice && ngspice -b tb_chip_spi_extracted.spice > spice_chip_extracted.log 2>&1 && cd ..
scripts/check_chip_sim.py ngspice/spice_chip_extracted.log
```

### 7.1 何を流しているか

SCLK 1 MHz / Mode 0 / MSBファースト で3フレーム連続:

| | DIS | 内容 |
|---|---|---|
| WRITE | 0 | マスタがSDIOに `0xA5` を送る。8発目の立ち上がりで `rx_data` に取り込まれ、DATAパッドに出てくる |
| READ | 1 | DATAパッドが入力になり `tx_data = 0x3D`。チップがSDIOを駆動して送り返す |
| WRITE | 0 | `0x5A`。2フレーム目もビット7から始まること、READが `rx_data` を壊していないことを見る |

`0x3D` はLSBが1なので、チップが最後に駆動する値がHIGHになる。CSが上がった
直後にSDIOが 0 V へ落ちる(20 kΩのプルダウンが効く)ことが、**「本当に手を
離した」ことと「まだ駆動している」ことを区別できる**。LSBが0の値だとこの
チェックはどちらでも通ってしまう。

12チェック / 54 measure。DATAパッド8本の上を `0x00` → `0xA5` → `0x3D` →
`0xA5` → `0x5A` と4通りの相異なるパターンが通るので、たまたま一致することは
ない。

### 7.2 `.control` に `run` を書かない

バッチモード(`ngspice -b`)は `.tran` カードを自分で走らせる。そこに
`.control ... run ... .endc` を書くと**解析が2回走る**(I2C版のTBはそう
なっていた。2回の測定値は最後の桁まで一致したので、間違いではなく無駄)。
`.control` は `save` を置くためだけに使い、`run` も `print` も書かない。
`save` で残すベクタをパッド16本に絞ってあるので、Tmax 1 nsでもメモリは
10 MB程度で済む。

### 7.3 Tmax = 1 ns の理由

`.tran` の第4引数。I2C版は、IRSIM・Verilog・ゲートレベルの全てで通る
READフレームがSPICEだけ落ちる、という現象を数セッション追いかけて、
**Tmax=50 ns では10 ns未満のセットアップ余裕を解像できず、ngspiceの適応
ステップ制御が誤った側に丸めていた**ことを突き止めた。他を何も変えずTmaxを
1 nsにするだけで 10/14 → 14/14 になっている。区間を限って指定する手段が
無いので全区間に効き、その分遅い(本設計は 37.5 µs で24秒)。

### 7.4 結果 — **12/12 PASS**

```
[t=   4500 ns] OK  : DATA pads read 0x00 out of reset                        (got 0x00)
[t=   4500 ns] OK  : byte_end low while CS is high                           (0.000 V on P4)
[t=   8000 ns] OK  : byte_end low mid-frame (bit 3 of the WRITE)             (0.000 V on P4)
[t=  12000 ns] OK  : byte_end high during the 8th bit of the WRITE           (5.000 V on P4)
[t=  13200 ns] OK  : DATA pads carry the received 0xA5                       (got 0xA5)
[t=  15700 ns] OK  : SDIO high-Z with DIS=1 and CS high                      (0.000 V on P2)
[t=  15700 ns] OK  : DATA pads are inputs carrying tx_data = 0x3D            (got 0x3D)
[t=  23500 ns] OK  : the chip shifted 0x3D out on SDIO                       (got 0x3D)
[t=  23000 ns] OK  : byte_end high during the 8th bit of the READ            (5.000 V on P4)
[t=  25200 ns] OK  : SDIO released after CS rises (last bit driven was 1)    (0.000 V on P2)
[t=  27200 ns] OK  : the READ frame left rx_data at 0xA5                     (got 0xA5)
[t=  35700 ns] OK  : a second WRITE frame lands 0x5A                         (got 0x5A)
```

### 7.5 抽出ネットリストでも同じTBを流す

`gen_chip_sim_ready.py` が変換するのはLVSの**参照側**(Verilogとセル回路図から
組み立てた「こうあるべき」)。`gen_sim_from_extracted.py` は**レイアウト側**を
変換する。LVSは両者がグラフとして一致することを言うが、**レイアウト自身の
数値を持っているのは後者だけ**:

```
XM$1 vdd A Y vdd PMOS L=1u W=10.2u AS=28.56p AD=15.3p PS=26u PD=13.2u
```

PDKの `PMOS`/`NMOS` サブサーキットは `AS`/`AD` の既定値が `w*sdwidth`、
`PS`/`PD` が `2*(sdwidth+w)` で、参照ネットリストはこれを使う。抽出側は
実際の形状を測った値なので、**行内で隣り合うセルが共有する拡散は1回しか
数えられない**。接合容量、つまり遅延が、描いた通りになる。

参照側との突き合わせは**素子数ではなく総チャネル幅**で行う。抽出器は
並列素子をまとめてしまう(FILL3のデカップリング48個が W=1017.6 µm の
PMOS 1個になる)し、回路図側は同じものを `m=2` と書くことがある。
総幅はどちらの畳み込みでも保存されるが、個数はどちらでも壊れる
(`check_cell_spice.py` がセルとGDSを比べるときと同じ理屈)。

結果: **19セルすべてが同じ子インスタンスと同じ総P/N幅**
(PMOS 3205.6 µm / NMOS 2413.9 µm)。同じTBで **12/12 PASS**、54 measure の
参照側との最大差は **0.7 mV**。

> `layout/step10/spi_slave_sclk_nrow_fm.extracted`(コア単体)は
> option C の再合成より**前**の抽出で、`NOR2` が2個残っている。上の
> 突き合わせがそれを検出する。チップレベルの抽出はコアを含むので、
> こちらを使う限り問題にならない。

### 7.6 通信速度 — 10 MHz

`gen_chip_tb.py` に判定しない計測(TRIG/TARG)を4本足してある。`.measure` の
`TD` を対象のエッジ直前に置くので `RISE=`/`FALL=` の数え上げは常に1回目で、
24発のクロックを頭から数えるより頑健:

| 計測 | 経路 |
|---|---|
| `tco_sdio_*` | SCLK**立ち下がり** → SDIO 有効(READ) |
| `tco_data` | SCLK 8発目の**立ち上がり** → DATAパッド有効(WRITE) |

実測(抽出ネットリスト / typical / 5.0 V / 27 °C):

| | 負荷なし | 20 pF |
|---|---|---|
| `tco_sdio` | **30.7 ns** | **34.4 ns** |
| `tco_data` | 21.5 ns | 25.2 ns |
| 12チェックが通る上限 | **16 MHz** | 15 MHz(マスタ100 Ω) |
| コア+入力経路だけの限界 | **32 MHz** | — |

Mode 0 ではチップは立ち下がりでSDIOを変え、マスタは次の立ち上がり=**半周期後**に
サンプルするので `f_max ≈ 1/(2 × tco_sdio)`。負荷なしで 16.3 MHz、実測の
「16 MHz通過 / 17 MHz失敗」と一致する。17 MHzで最初に落ちるのは `read_byte`
だけで `rx_wr*` は32 MHzまで通る — **遅いのは出力経路だけ**。

> **推奨最大 SCLK = 10 MHz**(5.0 V、パッド負荷20 pFまで、マスタのセットアップ
> 10 ns)。詳細と、テストベンチ側のマスタが先に律速した件は
> `design_notes.md` §18.8。

---

## 8. 規模レポート

| スクリプト | 役割 |
|---|---|
| `gate_count.py` | ネットリストのセル数・トランジスタ数・面積・**等価ゲート数**(NAND2 = 4Tr = 1981µm² = 1ゲート)を集計。`--density` で配置後コア面積も推定(既定 0.278 = I2C実チップの実測論理セル密度)。複数ネットリストを並べて工程ごとの増減を見られる。`--add CELL=N` で未挿入セルを仮に足せる。 |

---

## 9. データファイル

| ファイル | 内容 |
|---|---|
| `../lef/cell_info.json` | セル一覧。面積は `lef/TR-1um_STDCELL.gds` の bounding box 実測、トランジスタ数は `TR-1um_Async_I2C/LEF/<cell>.extracted` の MOS 素子数、論理関数はLiberty生成用。 |
| `../lef/TR-1um_STDCELL.gds` / `.lef` | 標準セルの物理データ(TR-1um_Async_I2C からコピー)。 |
| `../lef/TR1um_5_stdcell.lib` | I2C版のプレースホルダLiberty(参照用、`area: 1`)。 |
| `../lef/TR1um_5_stdcell_area.lib` | `gen_liberty.py` が生成する実面積版。合成はこちらを使う。 |
| `../lef/TR-1um_STDCELL.spice` | セルのトランジスタ実体(schematic側)。`gen_cell_spice.py` が生成、LVSネットリストの部品。 |
| `../layout/<TOP>.spice` | LVS参照ネットリスト。`gen_lvs_spice.py` が生成。同じ内容が `../layout/step10/simulation/` にも置かれる(実機LVS用)。 |
| `../lef/OSS_FRAME_GIO.spice` | パッドリングのトランジスタレベル実体(xschem export のコピー)。チップレベルLVSネットリストの部品。 |
| `../lef/TR-1um_frame_25x25.gds` | パッドフレーム(`OSS_FRAME_GIO` / `OSS_FRAME_TEG` / `OSS_FRAME`)。`TR-1um_Async_I2C/FRAME/` からコピー。 |
| `../ngspice/<CHIP_TOP>_sim_ready.spice` | LVSネットリストをngspice用に直したもの。`gen_chip_sim_ready.py` が生成。 |
| `../ngspice/tb_chip_spi.spice` / `_expected.json` | チップレベルTBと期待値。`gen_chip_tb.py` が生成。 |
| `../ngspice/spice_chip.log` | 上を流したngspiceのログ(12/12 PASSの現物)。 |
| `../ngspice/<CHIP_TOP>_extracted_sim.spice` | **抽出**ネットリストをngspice用に直したもの。`gen_sim_from_extracted.py` が生成。 |
| `../ngspice/tb_chip_spi_extracted.spice` / `spice_chip_extracted.log` | 抽出ネットリストに対する同じTBとそのログ(12/12 PASS)。 |
| `../layout/chip/` | チップ統合の成果物。`step1_assembled.gds`(配置のみ) / `step2_routed.gds`(配線後、PTECT削除済み) / `step3_top_pins.gds`(ボンドパッドにLVSピン) / **`step4_final.gds`(ロゴまで入った最終物)** / `gio_connections.json` / `signal_routing_plan.json` / `floorplan.png` / `step4_final.png`。 |
| `../lef/opensusi_logo.txt` | OpenSUSIロゴを5.0 µmグリッドに落としたビットマップ(317 × 63、5,785ドット)。`place_logo.py` が読む。 |

---

## 10. 今後追加予定

IRSIM・MPWエクスポートの各スクリプトは、
`TR-1um_Async_I2C/script/` の対応スクリプト(`route_*.py`、`drc_check_nrow_fm.py`、`gen_irsim_*.py`、
`export_to_mpw_submission_v10.py` 等)を同じ方針で引数化して移植する。
