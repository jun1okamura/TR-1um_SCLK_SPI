# PROVENANCE — `src/` の出どころ

`src/` の2ファイルは **`scripts/export_mpw.py` が機械生成**したもの。手で
編集しない。作り直すには:

```sh
scripts/export_mpw.py
```

| 提出物 | 元ファイル | 差分 |
|---|---|---|
| `src/tr_1um_3wire_SPI.gds` | `layout/chip/step4_final.gds` | `OSS_FRAME_GIO` → `OSS_FRAME` のセル改名のみ |
| `src/tr_1um_3wire_SPI.cir` | `layout/chip/tr_1um_3wire_SPI.spice` | 同じ改名のみ(＋生成元を記すヘッダ) |

改名の理由は `scripts/pre_check.py` — 提出物自身のゲートで、CIの最初に走る —
が **`OSS_FRAME` か `OSS_FRAME_TEG` という名前のセルの存在を要求する**ため。
本設計が実体化しているのはGIO版の `OSS_FRAME_GIO`。フレームGDSには素の
`OSS_FRAME` / `OSS_FRAME_TEG` も入っているが、どこからも実体化されないので
トップセルになってしまい、同じ pre_check の「トップセルはちょうど1つ」に
引っかかる(`place_logo.py` が未使用ライブラリセルごと刈っている)。実際に
使っているリングを改名すれば両方の条件を同時に満たす。**改名はこの提出用
コピーの中だけ**で、マスタのレイアウトと参照ネットリストは本来の名前のまま。
GDSとネットリストの両方に同じ改名をかけるので、LVSが突き合わせる2つは
互いに整合している。I2C版(`TR-1um_I2C_2026`)も同じ理由で同じことをしている。

---

## それぞれの元ファイルがどう作られたか

```
hdl/spi_slave_sclk.v                     RTL
  └ scripts/build.sh                     Yosys+ABC合成 → MUXDFFRB統合
                                         → BUFTH挿入 → 行バッファ挿入
      └ layout/spi_slave_sclk_net_pnr.v  41セル / 844 Tr / 211等価ゲート
          ├ scripts/place.py             nrow配置(2行、FM分割)
          ├ scripts/route.py             チャネル配線 → 圧縮 → トップピン
          │   └ layout/step10/...gds     コア `spi_slave_sclk_nrow_fm`
          │                              1,632.6 × 314.1 µm
          └ scripts/gen_lvs_spice.py     コアのLVS参照ネットリスト
                                         (セル実体は lef/TR-1um_STDCELL.spice)

lef/TR-1um_frame_25x25.gds               パッドフレーム(OSS_FRAME_GIO)
  └ scripts/assemble_top.py              コアをフレームへ配置 + PTECT
      └ scripts/route_chip.py            トップ配線 → PTECT削除
          └ scripts/add_top_pins.py      ボンドパッド16箇所にLVSピン
              └ scripts/place_logo.py    ロゴ2段 + 未使用セルの刈り取り
                  └ layout/chip/step4_final.gds        ← src/*.gds の元

lef/OSS_FRAME_GIO.spice                  パッドリングのトランジスタ実体
  └ scripts/gen_lvs_spice_top.py         + コアのLVSネットリスト
                                         + layout/chip/gio_connections.json
      └ layout/chip/tr_1um_3wire_SPI.spice                ← src/*.cir の元
```

参照ネットリストは**手書きではない**。機能検証を通したゲートレベルNETと
パッド結線マップから機械生成しているので、**LVSが参照する回路は検証したのと
同一のNET**であることが構造的に保証されている。

---

## 提出時点での検証状況

| | |
|---|---|
| RTL / 合成直後NET / P&R用NET の3ビュー | 12本のテストベンチ・187チェック × 3 = **561チェック / 36ラン 全PASS** |
| コア単体 DRC / LVS(実機KLayout) | **クリーン** |
| チップ全体 DRC / LVS(実機KLayout) | **クリーン**(`step4_final.gds`、2026-09-08) |
| 自前チップチェック(`scripts/check_chip.py`) | 新規DRCマーカー0 / フレームPTECT内0 / 信号24ネット断線0・短絡0 / VDD・GND独立 / ボンドパッド16本正しいネット / 参照ネットリストと27ネット一致 |
| ngspice トランジスタレベル(チップ全体) | **12チェック / 54 measure 全PASS**。参照ネットリスト・**レイアウト抽出ネットリスト**の両方 |
| 通信速度 | 推奨最大 SCLK **10 MHz**(5.0 V、パッド負荷20 pF、マスタのセットアップ10 ns) |
| `scripts/pre_check.py`(提出物自身のゲート) | `src/tr_1um_3wire_SPI.gds` で **PASS** |

詳細は [`design_notes.md`](./design_notes.md)、スクリプトの役割は
[`scripts/SCRIPTS.md`](./scripts/SCRIPTS.md)。

---

## テンプレート同梱ファイルの扱い

`src/tr_1um_username.{gds,cir,sch,extracted}` はMPWテンプレートの
プレースホルダ設計。`export_mpw.py` が書き出し時に削除する — 本物の隣に
残しておくと、間違ったほうを提出することになるため。
