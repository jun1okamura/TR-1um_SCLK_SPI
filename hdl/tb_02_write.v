//============================================================================
// tb_02_write.v -- WRITE frame data patterns
//
//   Ten WRITE frames covering corner and walking patterns; each is checked
//   both at rx_data and on the DATA[7:0] pads.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_02_write;

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
            $dumpfile("tb_02_write.vcd");
            $dumpvars(0, tb_02_write);
        end
        $display("=== tb_02_write : WRITE frame data patterns ===");
        hard_reset;

        tb_txbuf[16] = 8'h00;  tb_txbuf[17] = 8'hFF;
        tb_txbuf[18] = 8'hA5;  tb_txbuf[19] = 8'h5A;
        tb_txbuf[20] = 8'h01;  tb_txbuf[21] = 8'h80;
        tb_txbuf[22] = 8'h0F;  tb_txbuf[23] = 8'hF0;
        tb_txbuf[24] = 8'h3C;  tb_txbuf[25] = 8'hC3;

        for (k = 0; k < 10; k = k + 1) begin
            exp = tb_txbuf[16 + k];
            spi_write(exp);
            check8("WRITE -> rx_data",   rx_data, exp);
            check8("WRITE -> DATA pads", data,    exp);
        end

        report_and_finish("tb_02_write");
    end

endmodule

`default_nettype wire
