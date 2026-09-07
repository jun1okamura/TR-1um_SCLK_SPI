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
| `gen_liberty.py` | `lef/cell_info.json` から合成用 Liberty を生成。**面積は実測値**を使うためABCが本物のシリコン面積で最適化し、`gate_count.py` の数字と一致する。タイミングはプレースホルダ(STA不可)。`-i` `-o` `--skip`。 |
| `sync_cell_info.py` | STDCELLライブラリを再測定して `lef/cell_info.json` を更新。**面積は `lef/TR-1um_STDCELL.lef` の MACRO SIZE を第一ソース**とし、GDSのbounding boxはフォールバック(全セルがprBoundaryからx方向12.6µm・y方向4.0µmはみ出すため、GDS実測は真のフットプリントではない)。トランジスタ数は各セルの `.extracted` から。追加/変更/削除セルを差分表示し、論理関数やTr数が欠けているセルを警告する。**STDCELLに手を入れたら必ずこれを回す**。 |

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
| `explore_rows.py` | 行数の検討。行数ごとにセル幅でバランスさせた分割を多スタートFMで求め、行をまたぐネット数から必要チャネルトラック数・コア寸法・面積を出して比較する。`--row-width` で行幅を固定すると(行が「入りさえすればよい」制約になり)分割器が自由に詰められる。 |
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
| `plot_layout.py` | 配線結果のPNG可視化(フロー外、目視確認用)。 |

移植した原本(直接編集しない — `port_i2c_scripts.py` で再生成される):
`route_channels_nrow_fm.py`(中核ルータ、5パス)、`ripup_reroute_shorts.py`、
`route_top_pins_nrow_fm.py`、`add_power_pins_nrow_fm.py`、
`squeeze_channels_nrow_fm.py`、`drc_check_nrow_fm.py`、
`verify_connectivity_nrow_fm{,_m1m2}.py`、`lef_parser.py`、
`netlist_parser.py` ほか。

### ステージ構成

| STEP | 内容 | 成果物 |
|---|---|---|
| step5 | 配置GDS + チャネル注釈 | `layout/step5/route_step_1_placement.gds` |
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

チャネル予算は**広めに取って配線し、step10で圧縮する**(`spi_config.py` の
`ROUTE_CH_HEIGHTS`)。ルータのジョグ機構は行またぎ1本ごとに新しいトラックを
確保するため、`place.py` のネット交差数ベースの見積もりでは足りない。

---

## 5. 規模レポート

| スクリプト | 役割 |
|---|---|
| `gate_count.py` | ネットリストのセル数・トランジスタ数・面積・**等価ゲート数**(NAND2 = 4Tr = 1981µm² = 1ゲート)を集計。`--density` で配置後コア面積も推定(既定 0.278 = I2C実チップの実測論理セル密度)。複数ネットリストを並べて工程ごとの増減を見られる。`--add CELL=N` で未挿入セルを仮に足せる。 |

---

## 6. データファイル

| ファイル | 内容 |
|---|---|
| `../lef/cell_info.json` | セル一覧。面積は `lef/TR-1um_STDCELL.gds` の bounding box 実測、トランジスタ数は `TR-1um_Async_I2C/LEF/<cell>.extracted` の MOS 素子数、論理関数はLiberty生成用。 |
| `../lef/TR-1um_STDCELL.gds` / `.lef` | 標準セルの物理データ(TR-1um_Async_I2C からコピー)。 |
| `../lef/TR1um_5_stdcell.lib` | I2C版のプレースホルダLiberty(参照用、`area: 1`)。 |
| `../lef/TR1um_5_stdcell_area.lib` | `gen_liberty.py` が生成する実面積版。合成はこちらを使う。 |

---

## 7. 今後追加予定

配線・DRC/LVS・IRSIM・MPWエクスポートの各スクリプトは、
`TR-1um_Async_I2C/script/` の対応スクリプト(`route_*.py`、`drc_check_nrow_fm.py`、`gen_irsim_*.py`、
`export_to_mpw_submission_v10.py` 等)を同じ方針で引数化して移植する。
