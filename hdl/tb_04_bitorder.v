//============================================================================
// tb_04_bitorder.v -- MSB-first bit ordering
//
//   Walking-one and walking-zero patterns in both directions.  A swapped or
//   rotated bit order fails here even when byte-level patterns pass.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_04_bitorder;

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
            $dumpfile("tb_04_bitorder.vcd");
            $dumpvars(0, tb_04_bitorder);
        end
        $display("=== tb_04_bitorder : MSB-first bit ordering ===");
        hard_reset;

        for (k = 0; k < 8; k = k + 1) begin
            spi_write(8'h01 << k);
            check8("WRITE walking one",  data, 8'h01 << k);
        end
        for (k = 0; k < 8; k = k + 1) begin
            spi_write(~(8'h01 << k));
            check8("WRITE walking zero", data, ~(8'h01 << k));
        end
        for (k = 0; k < 8; k = k + 1) begin
            spi_read(8'h01 << k, got);
            check8("READ walking one",   got, 8'h01 << k);
        end
        for (k = 0; k < 8; k = k + 1) begin
            spi_read(~(8'h01 << k), got);
            check8("READ walking zero",  got, ~(8'h01 << k));
        end

        report_and_finish("tb_04_bitorder");
    end

endmodule

`default_nettype wire
