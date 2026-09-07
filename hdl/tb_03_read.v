//============================================================================
// tb_03_read.v -- READ frame data patterns
//
//   Ten READ frames covering corner and walking patterns, driven onto the
//   DATA[7:0] pads by the master and read back over SDIO.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_03_read;

    reg        sclk, cs_n, dis, rstn;
    reg        m_sdio, m_sdio_oe;
    reg [7:0]  m_data;
    reg        m_data_oe;

    wire       sdio;
    wire [7:0] data;
    wire       sdio_out, sdio_oe, data_oe, byte_end;
    wire [7:0] rx_data;

    assign sdio = m_sdio_oe ? m_sdio   : 1'bz;
    assign sdio = sdio_oe   ? sdio_out : 1'bz;
    assign data = m_data_oe ? m_data   : 8'hzz;
    assign data = data_oe   ? rx_data  : 8'hzz;

    spi_slave_sclk dut (
        .rstn(rstn), .sclk(sclk), .cs_n(cs_n), .dis(dis),
        .sdio_in(sdio), .sdio_out(sdio_out), .sdio_oe(sdio_oe),
        .tx_data(data), .rx_data(rx_data), .data_oe(data_oe),
        .byte_end(byte_end)
    );

`include "spi_tb_common.vh"

    reg [7:0] got, exp;
    integer   k, n;

    initial begin
        if ($test$plusargs("dump")) begin
            $dumpfile("tb_03_read.vcd");
            $dumpvars(0, tb_03_read);
        end
        $display("=== tb_03_read : READ frame data patterns ===");
        hard_reset;

        tb_txbuf[16] = 8'h00;  tb_txbuf[17] = 8'hFF;
        tb_txbuf[18] = 8'hA5;  tb_txbuf[19] = 8'h5A;
        tb_txbuf[20] = 8'h01;  tb_txbuf[21] = 8'h80;
        tb_txbuf[22] = 8'h0F;  tb_txbuf[23] = 8'hF0;
        tb_txbuf[24] = 8'h3C;  tb_txbuf[25] = 8'hC3;

        spi_write(8'h96);   // seed rx_data so we can prove READ leaves it alone

        for (k = 0; k < 10; k = k + 1) begin
            exp = tb_txbuf[16 + k];
            spi_read(exp, got);
            check8("READ -> SDIO", got, exp);
        end
        check8("READ never disturbs rx_data", rx_data, 8'h96);

        report_and_finish("tb_03_read");
    end

endmodule

`default_nettype wire
