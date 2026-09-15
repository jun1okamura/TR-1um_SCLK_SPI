# lef/

**行高 64.8 µm の STDCELL はここには無い。** `reference/v64_8/lef/` に移した。

    TR-1um_STDCELL.gds / .lef / .spice
    TR1um_5_stdcell.lib / TR1um_5_stdcell_area.lib
    cell_info.json

正本は **v59_4（行高 59.4）** で、`TR-1um_APRtools/stdcell/v59_4/` にある
（`TR-1um_APRtools/docs/02_stdcell_diff.md`）。いまのフローはそちらしか読まない。
64.8 版を `lef/` に置いたままにしていると `selfcheck.py` が毎回

    [  NG  ] TR-1um_STDCELL.gds    **不一致** old=… new=…

を出し続けて、**本物の不一致が埋もれる**。`scripts/` の 64.8 世代のスクリプトは
`reference/v64_8/lef/` を見るように直してある。

## ここに残っているもの

| ファイル | 何 |
|---|---|
| `OSS_FRAME_GIO_nocombine.spice` | **いまのフローが使う**。チップ LVS のフレーム側ソース（`config.FRAME_LVS_SPICE`）。`apr/mkframespice.py --no-combine` の出力 |
| `OSS_FRAME_GIO.spice` / `.extracted` | 64.8 世代の抽出。参考 |
| `TR-1um_frame_25x25.gds` | フレーム。いまのフローは `APRtools/pdk/pending-upstream/TR-1um_frame_25x25_GIO.gds` を使う（`docs/07_frame_issue.md`） |
| `opensusi_logo.txt` | ロゴのビットマップ。いまのフローは `APRtools/art/opensusi_logo.txt` を使う |
| `BUF_X2.sch` / `.sym` / `.extracted` | xschem の古い写し。**PDK のものとは違う**（design_notes: トランジスタが 4 個で、実物は 6 個） |
| `simulation` | xschem の作業場への **symlink**。リポジトリの外を指す（U40） |
