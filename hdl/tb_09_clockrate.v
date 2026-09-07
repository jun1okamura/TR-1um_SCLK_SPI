//============================================================================
// tb_09_clockrate.v -- SCLK rate and duty-cycle sweep
//
//   The core is fully synchronous to SCLK, so behaviour must be independent
//   of the clock period and of the high/low duty ratio.  This covers the
//   Raspberry Pi's power-of-two clock divider landing on odd rates.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_09_clockrate;

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
            $dumpfile("tb_09_clockrate.vcd");
            $dumpvars(0, tb_09_clockrate);
        end
        $display("=== tb_09_clockrate : SCLK rate and duty-cycle sweep ===");
        hard_reset;

        // period sweep, 50% duty
        for (k = 0; k < 6; k = k + 1) begin
            case (k)
                0: begin thi = 5000; tlo = 5000; end   //  100 kHz
                1: begin thi = 1000; tlo = 1000; end   //  500 kHz
                2: begin thi =  250; tlo =  250; end   //    2 MHz
                3: begin thi =  100; tlo =  100; end   //    5 MHz
                4: begin thi =   50; tlo =   50; end   //   10 MHz
                5: begin thi =   25; tlo =   25; end   //   20 MHz
            endcase
            spi_write(8'h5A + k[7:0]);
            check8("WRITE at swept rate", rx_data, 8'h5A + k[7:0]);
            spi_read(8'hC3 - k[7:0], got);
            check8("READ  at swept rate", got, 8'hC3 - k[7:0]);
        end

        // asymmetric duty cycles
        thi = 20;   tlo = 400;
        spi_write(8'h3C);
        check8("WRITE, 5% high duty",  rx_data, 8'h3C);
        spi_read(8'hC3, got);
        check8("READ,  5% high duty",  got, 8'hC3);

        thi = 400;  tlo = 20;
        spi_write(8'hD2);
        check8("WRITE, 95% high duty", rx_data, 8'hD2);
        spi_read(8'h2D, got);
        check8("READ,  95% high duty", got, 8'h2D);

        thi = 100;  tlo = 100;

        report_and_finish("tb_09_clockrate");
    end

endmodule

`default_nettype wire
