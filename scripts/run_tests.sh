#!/bin/sh
#=============================================================================
# run_tests.sh -- run the whole hdl/ verification suite.
#
#   Every testbench is run against every available view of the design, with
#   the SAME testbench source -- the two-stage equivalence flow of
#   TR-1um_Async_I2C, extended to the post-transform netlists:
#     RTL  hdl/$TOP.v                     behavioural
#     NET  layout/${TOP}_net.v            raw yosys/abc output
#     PNR  layout/${TOP}_net_pnr.v        after MUXDFFRB merge + BUFTH +
#                                         per-row clock buffers
#   Any stage whose netlist is absent is skipped, so this works before
#   scripts/build.sh has been run as well as after.
#
#   usage:  scripts/run_tests.sh [testbench-name ...]
#           TOP=<core> CHIP=<top> scripts/run_tests.sh
#           scripts/run_tests.sh tb_05_mode0        # just one
#           DUMP=1 scripts/run_tests.sh tb_05_mode0 # + VCD waveform
#=============================================================================
set -e
cd "$(dirname "$0")/.."
mkdir -p layout/sim

TOP=${TOP:-spi_slave_sclk}
CHIP=${CHIP:-tr_1um_3wire_SPI}
CELLS="hdl/cells_sim.v"
CHIPSRC="hdl/$CHIP.v"

STAGES="RTL:hdl/$TOP.v NET:layout/${TOP}_net.v PNR:layout/${TOP}_net_pnr.v"

if [ $# -gt 0 ]; then
    LIST="$*"
else
    LIST=$(cd hdl && ls tb_*.v | sed 's/\.v$//')
fi

[ -n "$DUMP" ] && PLUS="+dump" || PLUS=""

TOTAL=0
BAD=0

run_one() {           # $1 = tb name, $2 = stage label, $3 = dut source
    tb=$1; stage=$2; dut=$3
    bin="layout/sim/${tb}_${stage}"
    extra=""
    case "$tb" in tb_12_chip) extra="$CHIPSRC" ;; esac
    iverilog -g2012 -I hdl -o "$bin" "hdl/${tb}.v" $extra "$dut" "$CELLS"
    out=$(./"$bin" $PLUS 2>&1)
    line=$(echo "$out" | grep -E 'checks (PASSED|FAILED)' || true)
    TOTAL=$((TOTAL + 1))
    case "$line" in
        *PASSED*) printf "  %-16s %-4s  %s\n" "$tb" "$stage" "$line" ;;
        *)        BAD=$((BAD + 1))
                  printf "  %-16s %-4s  ** FAILED **\n" "$tb" "$stage"
                  echo "$out" | grep -E 'FAIL|mismatch' | head -10 ;;
    esac
}

for st in $STAGES; do
    stage=${st%%:*}
    dut=${st#*:}
    if [ ! -f "$dut" ]; then
        echo "-- stage $stage skipped ($dut not built)"
        continue
    fi
    echo "=========================================================="
    echo " stage $stage : $dut"
    echo "=========================================================="
    for tb in $LIST; do run_one "$tb" "$stage" "$dut"; done
done

echo "=========================================================="
if [ "$BAD" -eq 0 ]; then
    echo " ALL $TOTAL TESTBENCH RUNS PASSED"
else
    echo " $BAD of $TOTAL TESTBENCH RUNS FAILED"
    exit 1
fi
