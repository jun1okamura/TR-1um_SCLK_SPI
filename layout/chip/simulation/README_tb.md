# チップ level の ngspice TB について

`tb_chip_spi.spice` は**コミットしていない**。ngspice の `.include` は
環境変数を展開しないので、生成すると PDK の**絶対パス**がそのまま入る
（U24 / U35）。回す機械で作り直すこと:

    export TR1UM_PDK=<PDK と道具を置いた場所>/TR-1um
    export APRTOOLS=<PDK と道具を置いた場所>/TR-1um_APRtools
    export PYTHONPATH=$APRTOOLS/apr

    python3 $APRTOOLS/apr/gen_chip_sim_ready.py     # -> tr_1um_jun1okamura_3wire_spi_sim.spice
    python3 scripts/gen_chip_tb.py                  # -> tb_chip_spi.spice + _expected.json
    ( cd layout/chip/simulation && ngspice -b tb_chip_spi.spice > spice_chip.log 2>&1 )
    python3 scripts/check_chip_sim.py layout/chip/simulation/spice_chip.log   # ← 設計ルートで

`tb_chip_spi_expected.json`（判定表）と `spice_chip.log`（実行した現物）は
置いてある。2026-09-15 の結果は **12 項目すべて PASS**。

## 収束用の緩和は**既定では入れない**（U43、2026-09-18）

以前は `.options itl4=200 abstol=1e-11 vntol=1e-5 gmin=1e-11` を**常に**
入れていた（「59.4 版は既定だと t≈0.41 µs で進まない」）。★ **いまの
ネットリストは既定のまま 24 秒で完走し、12/12 PASS する**（実測）。
緩めた側の方が**精度は低い**ので、通るなら緩めない方を既定にする。

緩和の影響も測った — `tco_*` が **25〜49 ps（約 +0.1 %）**動き、電圧は
**µV 台**でしか変わらない。「判定は 2.5 V しきい値なので影響なし」は
正しかった（ただし今まで数字で言えていなかった）。

**進まなくなったときだけ** `--relax-tol` で戻す:

    python3 scripts/gen_chip_tb.py --relax-tol
