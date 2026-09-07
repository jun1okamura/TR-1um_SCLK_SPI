//============================================================================
// tb_01_reset.v -- reset and initial state
//
//   Power-on reset, idle pad state, asynchronous reset during idle and in
//   the middle of a frame, and recovery afterwards.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_01_reset;

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
            $dumpfile("tb_01_reset.vcd");
            $dumpvars(0, tb_01_reset);
        end
        $display("=== tb_01_reset : reset and initial state ===");
        hard_reset;

        check8("POR: rx_data cleared",        rx_data, 8'h00);
        check8("POR: DATA pads drive 0x00",   data,    8'h00);
        check1("POR: SDIO released",          sdio_oe_n, 1'b1);
        check1("POR: DATA driven (DIS=0)",    data_oe, 1'b1);

        spi_write(8'hA5);
        check8("write before reset",          rx_data, 8'hA5);

        // asynchronous reset while idle
        rstn = 1'b0;  #300;
        check8("RSTN low clears rx_data",     rx_data, 8'h00);
        rstn = 1'b1;  #300;
        spi_write(8'h5A);
        check8("frame works after reset",     rx_data, 8'h5A);

        // asynchronous reset in the middle of a frame
        dis = 1'b0;  m_sdio_oe = 1'b1;  m_sdio = 1'b1;
        #tsu;  cs_n = 1'b0;  #tsu;
        for (k = 0; k < 4; k = k + 1) sclk_pulse;
        rstn = 1'b0;  #200;  rstn = 1'b1;  #200;
        cs_n = 1'b1;  m_sdio_oe = 1'b0;  #tsu;
        check8("reset mid-frame aborts it",   rx_data, 8'h00);

        spi_write(8'hC3);
        check8("frame after aborted frame",   rx_data, 8'hC3);

        // reset asserted with CS still low
        dis = 1'b0;  m_sdio_oe = 1'b1;  m_sdio = 1'b1;
        #tsu;  cs_n = 1'b0;  #tsu;
        rstn = 1'b0;  #200;
        check8("reset with CS low clears",    rx_data, 8'h00);
        rstn = 1'b1;  cs_n = 1'b1;  m_sdio_oe = 1'b0;  #tsu;
        spi_write(8'h3C);
        check8("recovery after CS-low reset", rx_data, 8'h3C);

        report_and_finish("tb_01_reset");
    end

endmodule

`default_nettype wire
