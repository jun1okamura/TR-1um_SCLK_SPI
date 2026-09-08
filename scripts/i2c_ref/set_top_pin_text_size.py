"""
set_top_pin_text_size.py -- user request (this session): set the TEXT
size of the 16 chip-level TOP PIN labels (P1-P7,VSS,P9-P15,VDD; TXM2
layer 49/0, added directly to the top cell in design_notes.md 83.x) to
20um, for layout readability. Positions/strings/layer are untouched --
only the db.Text.size attribute is set.
"""
import klayout.db as db

IN_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/TR-1um_Async_I2C/src/tr_1um_i2c_slave_async.gds"
OUT_GDS = "/sessions/dreamy-ecstatic-heisenberg/mnt/outputs/tr_1um_i2c_slave_async_pin_text.gds"
TOP_CELL = "tr_1um_i2c_slave_async"
TXM2_LAYER = (49, 0)
TEXT_SIZE_UM = 20.0
TOP_PIN_LABELS = {"P1", "P2", "P3", "P4", "P5", "P6", "P7", "VSS",
                   "P9", "P10", "P11", "P12", "P13", "P14", "P15", "VDD"}


def main():
    ly = db.Layout()
    ly.read(IN_GDS)
    dbu = ly.dbu
    top = ly.cell(TOP_CELL)
    li = ly.layer(*TXM2_LAYER)
    size_dbu = int(round(TEXT_SIZE_UM / dbu))

    n = 0
    for s in top.shapes(li).each():
        if s.is_text() and s.text.string in TOP_PIN_LABELS:
            t = s.text.dup()
            t.size = size_dbu
            s.text = t
            n += 1

    print(f"updated {n} text labels to size {TEXT_SIZE_UM}um")
    assert n == len(TOP_PIN_LABELS), f"expected {len(TOP_PIN_LABELS)}, updated {n}"

    ly.write(OUT_GDS)
    print(f"wrote {OUT_GDS}")


if __name__ == "__main__":
    main()
