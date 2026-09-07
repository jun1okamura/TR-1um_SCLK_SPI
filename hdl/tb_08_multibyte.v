//============================================================================
// tb_08_multibyte.v -- multi-byte message with CS held low
//
//   spidev keeps CS asserted for a whole ioctl, so an N-byte transfer is 8N
//   clocks in one frame.  The design treats that as N back-to-back frames.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_08_multibyte;

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
            $dumpfile("tb_08_multibyte.vcd");
            $dumpvars(0, tb_08_multibyte);
        end
        $display("=== tb_08_multibyte : multi-byte message with CS held low ===");
        hard_reset;

        // 3-byte WRITE : the last byte is what stays on the pads
        tb_txbuf[0] = 8'h11;  tb_txbuf[1] = 8'h22;  tb_txbuf[2] = 8'h33;
        spi_msg_write(3);
        check8("3-byte WRITE: last byte wins", rx_data, 8'h33);
        check8("3-byte WRITE: DATA pads",      data,    8'h33);

        // 8-byte WRITE
        for (k = 0; k < 8; k = k + 1) tb_txbuf[k] = 8'hA0 + k[7:0];
        spi_msg_write(8);
        check8("8-byte WRITE: last byte wins", rx_data, 8'hA7);

        // 4-byte READ : every byte re-samples tx_data
        spi_msg_read(4, 8'h7E);
        check8("4-byte READ byte 0", tb_rxbuf[0], 8'h7E);
        check8("4-byte READ byte 1", tb_rxbuf[1], 8'h7E);
        check8("4-byte READ byte 2", tb_rxbuf[2], 8'h7E);
        check8("4-byte READ byte 3", tb_rxbuf[3], 8'h7E);

        // tx_data changed between bytes must show up in the next byte
        dis = 1'b1;  m_data_oe = 1'b1;  m_data = 8'h0F;  m_sdio_oe = 1'b0;
        #tsu;  cs_n = 1'b0;  #tsu;
        for (k = 7; k >= 0; k = k - 1) begin
            #tlo;  got[k] = sdio;  sclk = 1'b1;
            #thi;  sclk = 1'b0;
        end
        check8("burst READ byte 0 = 0x0F", got, 8'h0F);
        m_data = 8'hF0;                                  // change between bytes
        for (k = 7; k >= 0; k = k - 1) begin
            #tlo;  got[k] = sdio;  sclk = 1'b1;
            #thi;  sclk = 1'b0;
        end
        #tsu;  cs_n = 1'b1;  #tsu;  m_data_oe = 1'b0;  dis = 1'b0;  #tsu;
        check8("burst READ byte 1 = 0xF0", got, 8'hF0);

        // rx_data survives a multi-byte READ
        spi_write(8'h5C);
        spi_msg_read(3, 8'h39);
        check8("multi-byte READ leaves rx_data", rx_data, 8'h5C);

        report_and_finish("tb_08_multibyte");
    end

endmodule

`default_nettype wire
