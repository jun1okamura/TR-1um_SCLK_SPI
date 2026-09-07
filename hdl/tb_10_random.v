//============================================================================
// tb_10_random.v -- randomised regression against a golden model
//
//   200 randomised transactions (direction, data, message length and idle
//   gaps) compared against a behavioural golden model of the protocol.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_10_random;

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

    reg [7:0]  got, exp, pad;
    integer    k, n, j, nbytes;
    integer    errors;

    initial begin
        if ($test$plusargs("dump")) begin
            $dumpfile("tb_10_random.vcd");
            $dumpvars(0, tb_10_random);
        end
        $display("=== tb_10_random : randomised regression against a golden model ===");
        hard_reset;

        exp = 8'h00;                        // golden model of rx_data
        errors = 0;

        for (n = 0; n < 200; n = n + 1) begin
            nbytes = 1 + ({$random} % 4);

            if (({$random} % 2) == 0) begin
                // ---- WRITE message: golden rx_data becomes the last byte
                for (j = 0; j < nbytes; j = j + 1)
                    tb_txbuf[j] = $random;
                exp = tb_txbuf[nbytes-1];
                spi_msg_write(nbytes);
                if (rx_data !== exp) begin
                    errors = errors + 1;
                    $display("      WRITE mismatch @%0d: got 0x%02h exp 0x%02h",
                             n, rx_data, exp);
                end
                if (data !== exp) begin
                    errors = errors + 1;
                    $display("      DATA pad mismatch @%0d: got 0x%02h exp 0x%02h",
                             n, data, exp);
                end
            end else begin
                // ---- READ message: every byte must equal tx_data, and
                //      rx_data must be untouched
                pad = $random;
                spi_msg_read(nbytes, pad);
                for (j = 0; j < nbytes; j = j + 1)
                    if (tb_rxbuf[j] !== pad) begin
                        errors = errors + 1;
                        $display("      READ mismatch @%0d byte %0d: got 0x%02h exp 0x%02h",
                                 n, j, tb_rxbuf[j], pad);
                    end
                if (rx_data !== exp) begin
                    errors = errors + 1;
                    $display("      READ disturbed rx_data @%0d: got 0x%02h exp 0x%02h",
                             n, rx_data, exp);
                end
            end

            // random idle gap, sometimes with a free-running clock
            if (({$random} % 4) == 0) #({$random} % 3000);
            if (({$random} % 8) == 0)
                for (j = 0; j < 3; j = j + 1) sclk_pulse;
        end

        check8("200 random transactions, mismatches", errors[7:0], 8'h00);
        $display("      (%0d transactions replayed against the golden model)", n);

        report_and_finish("tb_10_random");
    end

endmodule

`default_nettype wire
