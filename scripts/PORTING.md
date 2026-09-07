# TR-1um_Async_I2C 配線スクリプトの移植記録

配置配線は I2C 版(`TR-1um_Async_I2C/script/`)のフローをそのまま使う。
あちらは実機KLayoutで**チップ全体DRC/LVSクリーン**、SPICEトランジスタ
レベル検証14/14 PASS まで到達した実績があるので、**アルゴリズムには一切
手を入れない**方針とした。

- `scripts/i2c_ref/` … I2C版からコピーした**無改変**の原本(23本)
- `scripts/*.py` … `scripts/port_i2c_scripts.py` が原本から生成した
  本プロジェクト版。**直接編集しないこと**(再生成で上書きされる)
- 変更内容はすべて `port_i2c_scripts.py` に置換ルールとして記述してある

```sh
scripts/port_i2c_scripts.py           # 再生成
scripts/port_i2c_scripts.py --check   # 差分確認のみ(書き込まない)
```

## 何を変えたか

### 1. ハードコードされた絶対パス

原本には Claude サンドボックスの絶対パス
(`/sessions/dreamy-ecstatic-heisenberg/mnt/...`)が多数残っている
(I2C版でも `lef_parser.py` / `netlist_parser.py` だけは可搬化済みだった)。
これを全て `scripts/spi_config.py` 経由に置換した。

| 原本 | 置換後 |
|---|---|
| `sys.path.insert(0, ".../script")` | `sys.path.insert(0, _HERE)` |
| `TECH_PY_DIR = ".../klayout/tech/python"` | `_cfg.pdk_tech_python()` |
| `PLACEMENT_JSON = "..."` | `_cfg.PLACEMENT_JSON` |
| `CELL_GDS = "..."` | `_cfg.CELL_GDS` |
| その他の中間ファイル | `_cfg.artifact("<元のファイル名>")` → `layout/` |

`pdk_tech_python()` は `via_1` PCell を持つPDKの KLayout python ディレクトリ
を探す。全ての via はこの PCell インスタンスなので必須。環境変数
`TR1UM_PDK` で明示できる。

### 2. トップセル名

`"i2c_slave_async_nrow_fm"` → `_cfg.TOP_CELL_NAME`(= `spi_slave_sclk_nrow_fm`)。

### 3. LEF ディレクトリ名

I2C版は `LEF/`(大文字)、本プロジェクトは `lef/`。`lef_parser.py` /
`netlist_parser.py` の既定パスを `_cfg` に向けた。

### 4. `route_top_pins_nrow_fm.py` の4行決め打ちをN行に一般化

ロジック変更その1。原本の `gather_pins()` は4行構成前提で、
row0=下端 / row3=上端 / row1=右端 / row2=左端 と決め打ちしていた。

```
row0_ports  = by_row[0]                 ->  by_row[0]
row3_ports  = by_row[3]                 ->  by_row[n_rows - 1]
row1_list   = by_row[1]                 ->  中間行の前半(右端へ)
row2_list   = by_row[2]                 ->  中間行の後半(左端へ)
row3_bound  = row_y0[3] + row_h         ->  row_y0[-1] + row_h
ch2 band    = row_y0[1]+row_h, row_y0[2] -> 中間行が無ければ上マージンで代用
```

**`n_rows == 4` では原本と完全に同一の挙動になる**(中間行 [1,2] が
前半[1]=右、後半[2]=左に分かれるため)。本プロジェクトは2行なので
row0=下端、row1=上端に振られ、左右端は使わない。

### 5. `squeeze_channels_nrow_fm.py` にPINレイヤ保護を追加

ロジック変更その2。原本の `build_y_map` は「最後に使われたトラックより上の
ヘッドルームを潰す」ため、**コア境界上に描かれたトップ辺のPINマーカーを
高さ0に潰してしまう**(下辺のマーカーはy=0からコア内側へ描かれるので無傷)。
ラベルもピン形状から外れ、`gen_lef.py` / LVS抽出の「PIN形状の内側にテキスト
ラベルがある」規約が壊れる。

`build_y_map(..., protect_y=())` を追加し、潰し処理を `_collapse_to()` に
くくり出して、保護Y区間に重なる部分だけ identity のまま残すようにした。
保護区間は `collect_protect_y()` がトップセル自身の (48,1)/(49,1)/(48,0)/(49,0)
から収集・マージする。`protect_y` が空なら**原本と完全に同一の挙動**。

置換ペアはコード片を含むため `port_rules.py` に分離してある
(`SQUEEZE_PIN_PROTECT`)。

## 本プロジェクト側で用意したもの(原本に対応物なし)

| ファイル | 役割 |
|---|---|
| `spi_config.py` | パスと幾何定数の単一ソース |
| `gen_placement_json.py` | `place.py` の配置結果 → I2C版ルータが読む配置JSONスキーマへの変換。I2C版 `gen_placement_nrow_fm.py` に相当する位置づけだが、配置そのものは `place.py` が持つ |
| `route.py` | I2C版 `run_v10_pipeline.py` に相当するステージドライバ |
| `port_rules.py` | 上記パッチのうちコード片差し替え分 |
| `plot_layout.py` | 配線結果のPNG可視化(目視確認用、フロー外) |

## 使わなかった原本

`fm_partition.py` / `gen_placement_2row.py` / `gen_placement_nrow_fm.py` /
`gen_placement_gds_nrow_fm.py` 以外の配置系、`dedup_gates.py` /
`insert_row_buffers.py` / `insert_bufth_scl_sda.py` /
`merge_muxdffrb_rslatch.py` / `apply_dff_group_constraints.py` は、
本プロジェクトが既に一般化した同等品を `scripts/` に持っているため
`i2c_ref/` に参照用として残すだけとした(`SCRIPTS.md` §2/§3 参照)。
