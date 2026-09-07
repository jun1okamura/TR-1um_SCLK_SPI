#!/bin/sh
#=============================================================================
# build.sh -- RTL -> place-and-route-ready gate-level netlist.
#
#   1  yosys synthesis          hdl/spi_slave_sclk.v  -> *_net.v
#   2  MUXDFFRB cell merge      *_net.v               -> *_net_merged.v
#   3  BUFTH on the SPI inputs  *_net_merged.v        -> *_net_bufth.v
#   4  per-row clock buffers    *_net_bufth.v         -> *_net_pnr.v
#   5  size report for every stage
#
#   Each step is a standalone, parameterised script -- see scripts/SCRIPTS.md.
#
#   env overrides:  TOP=  SRC=  ROWS=  LIB=  BUFTH_NETS=  CLK_NETS=
#                   ROW_BUF_CELL=
#                   ROW_ASSIGNMENT=<json from placement>
#=============================================================================
set -e
cd "$(dirname "$0")/.."

TOP=${TOP:-spi_slave_sclk}
ROWS=${ROWS:-2}
LIB=${LIB:-lef/TR1um_5_stdcell_area.lib}
BUFTH_NETS=${BUFTH_NETS:-sclk,cs_n,sdio_in}
CLK_NETS=${CLK_NETS:-sclk_buf,shift_clk}
ROW_BUF_CELL=${ROW_BUF_CELL:-BUF_X2}   # 2x output drive, same 16.2x64.8um footprint
L=layout

mkdir -p $L

echo "== 1. synthesis =========================================="
[ -f "$LIB" ] || python3 scripts/gen_liberty.py -o "$LIB"
SRC=${SRC:-hdl/$TOP.v}
sed -e "s|@TOP@|$TOP|g" -e "s|@LIB@|$LIB|g" -e "s|@SRC@|$SRC|g" \
    scripts/synth.ys.in > $L/synth.ys
yosys -q $L/synth.ys
yosys $L/synth.ys 2>&1 | sed -n '/Printing statistics/,/Chip area/p'""

echo "== 2. MUXDFFRB merge ====================================="
python3 scripts/merge_muxdffrb.py $L/${TOP}_net.v $L/${TOP}_net_merged.v

echo "== 3. BUFTH insertion ===================================="
python3 scripts/insert_bufth.py $L/${TOP}_net_merged.v $L/${TOP}_net_bufth.v \
        --nets "$BUFTH_NETS"

echo "== 4. per-row clock buffers (rows=$ROWS) ================="
python3 scripts/insert_row_buffers.py $L/${TOP}_net_bufth.v $L/${TOP}_net_pnr.v \
        --nets "$CLK_NETS" --rows "$ROWS" --cell "$ROW_BUF_CELL" \
        ${ROW_ASSIGNMENT:+--row-assignment "$ROW_ASSIGNMENT"}

echo "== 5. size report ========================================"
python3 scripts/gate_count.py \
        $L/${TOP}_net.v $L/${TOP}_net_merged.v $L/${TOP}_net_pnr.v
