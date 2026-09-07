//============================================================================
// tb_07_idle.v -- idle robustness
//
//   rx_data must hold across long idle periods, a free-running SCLK while CS
//   is high must not corrupt anything, and an aborted (short) frame must not
//   commit data.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_07_idle;

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
            $dumpfile("tb_07_idle.vcd");
            $dumpvars(0, tb_07_idle);
        end
        $display("=== tb_07_idle : idle robustness ===");
        hard_reset;

        spi_write(8'h96);
        check8("baseline write",              rx_data, 8'h96);

        #20000;
        check8("holds over 20us idle",        rx_data, 8'h96);

        for (k = 0; k < 20; k = k + 1) sclk_pulse;      // free-running SCLK
        check8("free-running SCLK, CS high",  rx_data, 8'h96);
        spi_write(8'h69);
        check8("frame after free-run SCLK",   rx_data, 8'h69);

        // CS pulse with no clocks at all
        #tsu;  cs_n = 1'b0;  #(tlo*4);  cs_n = 1'b1;  #tsu;
        check8("CS pulse without clocks",     rx_data, 8'h69);

        // aborted frame: only 5 of 8 clocks
        dis = 1'b0;  m_sdio_oe = 1'b1;  m_sdio = 1'b1;
        #tsu;  cs_n = 1'b0;  #tsu;
        for (k = 0; k < 5; k = k + 1) sclk_pulse;
        #tsu;  cs_n = 1'b1;  m_sdio_oe = 1'b0;  #tsu;
        check8("5-clock frame does not commit", rx_data, 8'h69);

        // the next full frame must still be bit-aligned
        spi_write(8'h2D);
        check8("realigns after short frame",  rx_data, 8'h2D);

        // 9 clocks (one too many) -- the 9th starts a new frame
        dis = 1'b0;  m_sdio_oe = 1'b1;
        exp = 8'hE1;  m_sdio = exp[7];
        #tsu;  cs_n = 1'b0;  #tsu;
        for (k = 7; k >= 0; k = k - 1) begin
            m_sdio = exp[k];  sclk_pulse;
        end
        m_sdio = 1'b0;  sclk_pulse;                     // 9th clock
        #tsu;  cs_n = 1'b1;  m_sdio_oe = 1'b0;  #tsu;
        check8("8th edge committed the byte", rx_data, 8'hE1);

        spi_write(8'h74);
        check8("clean frame after 9 clocks",  rx_data, 8'h74);

        report_and_finish("tb_07_idle");
    end

endmodule

`default_nettype wire
