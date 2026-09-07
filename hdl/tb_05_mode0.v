//============================================================================
// tb_05_mode0.v -- SPI Mode-0 edge behaviour
//
//   CPOL=0/CPHA=0 requires the MSB to be valid before the first rising edge
//   and the line to change only on falling edges.  Also proves tx_data is
//   captured at the first falling edge and ignored afterwards.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_05_mode0;

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
            $dumpfile("tb_05_mode0.vcd");
            $dumpvars(0, tb_05_mode0);
        end
        $display("=== tb_05_mode0 : SPI Mode-0 edge behaviour ===");
        hard_reset;

        // MSB must be valid before any clock edge exists
        dis = 1'b1;  m_data_oe = 1'b1;  m_data = 8'hB4;  m_sdio_oe = 1'b0;
        #tsu;  cs_n = 1'b0;  #tsu;
        check1("MSB valid before 1st edge", sdio, 1'b1);   // 0xB4 bit7 = 1
        check1("SDIO driven at CS low",     sdio_oe_n, 1'b0);

        // the line must be stable across every rising edge
        for (k = 7; k >= 0; k = k - 1) begin
            #tlo;
            got[k] = sdio;                       // just before the rising edge
            sclk = 1'b1;
            #(thi/2);
            check1("SDIO stable across rising edge", sdio, got[k]);
            #(thi/2);  sclk = 1'b0;
        end
        #tsu;  cs_n = 1'b1;  #tsu;  m_data_oe = 1'b0;  dis = 1'b0;  #tsu;
        check8("Mode-0 read of 0xB4",       got, 8'hB4);

        // tx_data is sampled at the first falling edge only
        dis = 1'b1;  m_data_oe = 1'b1;  m_data = 8'h6D;  m_sdio_oe = 1'b0;
        #tsu;  cs_n = 1'b0;  #tsu;
        #tlo;  got[7] = sdio;  sclk = 1'b1;      // bit7 straight from tx_data
        #thi;  sclk = 1'b0;                      // <- load happens here
        #5;    m_data = 8'hFF;                    // change well AFTER the load
        for (k = 6; k >= 0; k = k - 1) begin
            #tlo;  got[k] = sdio;  sclk = 1'b1;
            #thi;  sclk = 1'b0;
        end
        #tsu;  cs_n = 1'b1;  #tsu;  m_data_oe = 1'b0;  dis = 1'b0;  #tsu;
        check8("tx_data latched at 1st falling edge", got, 8'h6D);

        // a WRITE frame samples MOSI on rising edges: hold data only around them
        dis = 1'b0;  m_data_oe = 1'b0;  m_sdio_oe = 1'b1;
        exp = 8'h9C;
        m_sdio = exp[7];
        #tsu;  cs_n = 1'b0;  #tsu;
        for (k = 7; k >= 0; k = k - 1) begin
            m_sdio = exp[k];
            #tlo;  sclk = 1'b1;
            #thi;  sclk = 1'b0;
            m_sdio = 1'bx;                       // garbage between edges
            #1;
        end
        #tsu;  cs_n = 1'b1;  m_sdio_oe = 1'b0;  #tsu;
        check8("MOSI sampled on rising edge only", rx_data, exp);

        report_and_finish("tb_05_mode0");
    end

endmodule

`default_nettype wire
