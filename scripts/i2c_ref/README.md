# `scripts/i2c_ref/` — 移植の島（**凍結物**）

`TR-1um_Async_I2C`（実チップ）の配線スクリプトをそのまま持ってきた一式です。
**この設計の流れは呼びません**（`scripts/PORTING.md` に何をどこから持ってきたかの記録）。

★ **中の絶対パスは当時の作業場のもので、どこでも動きません。**

```
sys.path.insert(0, "/sessions/…/klayout/tech/python")
BASE = "/sessions/…/TR-1um_Async_I2C"
```

**直さないでください。** 直すと「当時こう書いてあった」という記録でなくなります
（U93 / U35 の決着と同じ扱い）。`../.frozen-paths` に理由付きで登録してあるので、
`apr/check_no_home_paths.py` はここを数えません。

**もしここの処理を使いたくなったら** — 復活させるのではなく、
**正本（`$APRTOOLS/apr/`）に同じ役の道具があるかを先に見てください**。
無ければ APRtools に足すのが筋です（U94: 写しを増やさない）。
