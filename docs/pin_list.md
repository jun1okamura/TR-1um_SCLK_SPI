# ピン配置 — `tr_1um_3wire_SPI`

`scripts/pin_list.py` が生成。手で編集しない。

- ダイ 2,500 × 2,500 µm、`OSS_FRAME_GIO` の16パッド(信号14 + VDD + VSS)。**P8 は存在しない**
- ボンドパッド中心は半径1,040 µm、コアが繋がるリング端子は半径921.7 µm
- コアは (-816.3, 515.9, 816.3, 830.0) (オフセット (-810.0, 515.9))

## 信号パッド

| パッド | ボンド座標 | 辺 | 役割 | 方向 | コア入力 (`P`) | コア出力 (`OUT`) | `HIZ` の駆動元 |
|---|---|---|---|---|---|---|---|
| `P1` | (-200, 1040) | TOP | **SCLK** | in | `sclk` | — | **VDD** 固定 |
| `P2` | (-600, 1040) | TOP | **SDIO** | bidir | `sdio_in` | `sdio_out` | `sdio_oe_n` |
| `P3` | (-1040, 600) | LEFT | **CS** | in | `cs_n` | — | **VDD** 固定 |
| `P4` | (-1040, 200) | LEFT | **TEST** | out | — | `byte_end` | **GND** 固定 |
| `P5` | (-1040, -200) | LEFT | **DIS** | in | `dis` | — | **VDD** 固定 |
| `P6` | (-1040, -600) | LEFT | **DATA[0]** | bidir | `tx_data[0]` | `rx_data[0]` | `dis` |
| `P7` | (-600, -1040) | BOTTOM | **DATA[1]** | bidir | `tx_data[1]` | `rx_data[1]` | `dis` |
| `P9` | (200, -1040) | BOTTOM | **DATA[2]** | bidir | `tx_data[2]` | `rx_data[2]` | `dis` |
| `P10` | (600, -1040) | BOTTOM | **DATA[3]** | bidir | `tx_data[3]` | `rx_data[3]` | `dis` |
| `P11` | (1040, -600) | RIGHT | **DATA[4]** | bidir | `tx_data[4]` | `rx_data[4]` | `dis` |
| `P12` | (1040, -200) | RIGHT | **DATA[5]** | bidir | `tx_data[5]` | `rx_data[5]` | `dis` |
| `P13` | (1040, 200) | RIGHT | **DATA[6]** | bidir | `tx_data[6]` | `rx_data[6]` | `dis` |
| `P14` | (1040, 600) | RIGHT | **DATA[7]** | bidir | `tx_data[7]` | `rx_data[7]` | `dis` |
| `P15` | (600, 1040) | TOP | **RSTN** | in | `rstn` | — | **VDD** 固定 |

## パッド並び(ダイを表から見て、左上から時計回り)

基板を配線するときはこちらの順。**電源パッドが信号列の間に入る**(`VDD` は `P1` と `P15` の間、`VSS` は `P7` と `P9` の間)。

- **TOP** … `P2` SDIO / `P1` SCLK / `VDD` 電源 / `P15` RSTN
- **RIGHT** … `P14` DATA[7] / `P13` DATA[6] / `P12` DATA[5] / `P11` DATA[4]
- **BOTTOM** … `P10` DATA[3] / `P9` DATA[2] / `VSS` 電源 / `P7` DATA[1]
- **LEFT** … `P6` DATA[0] / `P5` DIS / `P4` TEST / `P3` CS

## 電源パッド

| パッド | 座標 | 辺 | コア側 |
|---|---|---|---|
| `VDD` | (200, 1040) | TOP | M1実ピン (50,920)-(350,934) へライザー9本 |
| `VSS` | (-200, -1040) | BOTTOM | コア両側のM2脚(x=±838)→下辺のM1バスバー(y=-795)→M2ストリップ5本でフレームのVSSピンへ |

## `HIZ` の極性

`OSS_ESD_5V_DIO` は **`HIZ=0` で出力ドライバON**、`HIZ=1` でHi-Z。

- **DATA 8本** … `HIZ` は **DIS パッド(`P5`)の網に直結**。コアがDATAを駆動するのは `data_oe = ~dis = 1`、つまり `dis = 0` のときで、`HIZ = dis` が過不足なく一致するので論理が要らない
- **`P2` (SDIO)** … コアが `sdio_oe_n = ~(dis & ~cs_n)`(アクティブLOW)を出すので `HIZ2` に直結
- **入力専用パッド** … `HIZ` を VDD 固定。`OUT` ピンは同じ辺で20 µm隣のリングGND端子に落としてある(開放にすると`OSS_ESD_5V_DIO` 内のゲート入力が浮く)

固定タイ: `HIZ1`→**VDD**, `HIZ15`→**VDD**, `HIZ3`→**VDD**, `HIZ4`→**GND**, `HIZ5`→**VDD**

## パッドの無いコア出力

- **`data_oe`** … `~dis` と同じ情報で、これを使うパッドはすべて `HIZ` を DIS パッドの網から取っている。プローブ用にポートとしては残してある

