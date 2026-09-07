//============================================================================
// tb_06_tristate.v -- SDIO / DATA drive and Hi-Z control
//
//   Pad direction is a function of DIS and CS only.  Every combination is
//   checked, including that nothing is driven when it should not be.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_06_tristate;

    reg        sclk, cs_n, dis, rstn;
    reg        m_sdio, m_sdio_oe;
    reg [7:0]  m_data;
    reg        m_data_oe;

    wire       sdio;
    wire [7:0] data;
    wire       sdio_out, sdio_oe_n, data_oe, byte_end;
    wire [7:0] rx_data;

    assign sdio = m_sdio_oe ? m_sdio   : 1'bz;
    assign sdio = sdio_oe_n ? 1'bz : sdio_out;  // HIZ pin: 0 = drive
    assign data = m_data_oe ? m_data   : 8'hzz;
    assign data = data_oe   ? rx_data  : 8'hzz;

    spi_slave_sclk dut (
        .rstn(rstn), .sclk(sclk), .cs_n(cs_n), .dis(dis),
        .sdio_in(sdio), .sdio_out(sdio_out), .sdio_oe_n(sdio_oe_n),
        .tx_data(data), .rx_data(rx_data), .data_oe(data_oe),
        .byte_end(byte_end)
    );

`include "spi_tb_common.vh"

    reg [7:0] got, exp;
    integer   k, n;

    initial begin
        if ($test$plusargs("dump")) begin
            $dumpfile("tb_06_tristate.vcd");
            $dumpvars(0, tb_06_tristate);
        end
        $display("=== tb_06_tristate : SDIO / DATA drive and Hi-Z control ===");
        hard_reset;

        // DIS = 0 : chip drives DATA, never drives SDIO
        dis = 1'b0;  m_data_oe = 1'b0;  #tsu;
        check1("DIS=0, CS high: DATA driven",   data_oe, 1'b1);
        check1("DIS=0, CS high: SDIO released", sdio_oe_n, 1'b1);
        cs_n = 1'b0;  #tsu;
        check1("DIS=0, CS low : SDIO released", sdio_oe_n, 1'b1);
        check1("DIS=0, CS low : DATA driven",   data_oe, 1'b1);
        cs_n = 1'b1;  #tsu;

        // DIS = 1 : DATA released, SDIO driven only while CS is low
        dis = 1'b1;  #tsu;
        check1("DIS=1, CS high: DATA released", data_oe, 1'b0);
        check8("DIS=1, CS high: DATA is Hi-Z",  data,    8'hzz);
        check1("DIS=1, CS high: SDIO released", sdio_oe_n, 1'b1);
        check1("DIS=1, CS high: SDIO is Hi-Z",  sdio,    1'bz);
        cs_n = 1'b0;  #tsu;
        check1("DIS=1, CS low : SDIO driven",   sdio_oe_n, 1'b0);
        check1("DIS=1, CS low : DATA released", data_oe, 1'b0);
        cs_n = 1'b1;  #tsu;
        check1("after frame: SDIO released",    sdio_oe_n, 1'b1);
        dis = 1'b0;  #tsu;

        // no contention during a full WRITE or READ frame
        spi_write(8'hA5);
        check1("WRITE frame: no SDIO contention", (sdio === 1'bx), 1'b0);
        spi_read(8'h5A, got);
        check1("READ frame: no DATA contention",  (data === 8'hxx), 1'b0);

        report_and_finish("tb_06_tristate");
    end

endmodule

`default_nettype wire
