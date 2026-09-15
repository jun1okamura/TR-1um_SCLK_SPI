# チップ level の ngspice TB について

`tb_chip_spi.spice` は**コミットしていない**。ngspice の `.include` は
環境変数を展開しないので、生成すると PDK の**絶対パス**がそのまま入る
（U24 / U35）。回す機械で作り直すこと:

    export TR1UM_PDK=~/HogeHoge/OpenPDK/TR-1um
    export APRTOOLS=~/HogeHoge/OpenPDK/TR-1um_APRtools
    export PYTHONPATH=$APRTOOLS/apr

    python3 $APRTOOLS/apr/gen_chip_sim_ready.py     # -> tr_1um_3wire_SPI_sim.spice
    python3 scripts/gen_chip_tb.py                  # -> tb_chip_spi.spice + _expected.json
    cd layout/chip/simulation && ngspice -b tb_chip_spi.spice > spice_chip.log 2>&1
    cd - && python3 scripts/check_chip_sim.py layout/chip/simulation/spice_chip.log

`tb_chip_spi_expected.json`（判定表）と `spice_chip.log`（実行した現物）は
置いてある。2026-09-15 の結果は **12 項目すべて PASS**。
