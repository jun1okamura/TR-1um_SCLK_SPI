//============================================================================
// tb_12_chip.v -- chip-level test through the real bond pads
//
//   Drives P1..P15 exactly as the package pins are wired, so the pad
//   assignment, the BUFTH input path and the output drivers are all in the
//   loop.  A swapped DATA pad shows up here and nowhere else.
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_12_chip;

    reg        sclk, cs_n, dis, rstn;
    reg        m_sdio, m_sdio_oe;
    reg [7:0]  m_data;
    reg        m_data_oe;

    // package pins
    wire P1  = sclk;
    wire P3  = cs_n;
    wire P5  = dis;
    wire P15 = rstn;
    wire P2, P4;
    wire P6, P7, P9, P10, P11, P12, P13, P14;

    assign P2 = m_sdio_oe ? m_sdio : 1'bz;
    assign {P14,P13,P12,P11,P10,P9,P7,P6} = m_data_oe ? m_data : 8'hzz;

    // aliases so the shared BFM can be used unchanged
    wire       sdio = P2;
    wire [7:0] data = {P14,P13,P12,P11,P10,P9,P7,P6};

    tr_1um_3wire_SPI dut (
        .P1(P1), .P2(P2), .P3(P3), .P4(P4), .P5(P5),
        .P6(P6), .P7(P7), .P9(P9), .P10(P10), .P11(P11),
        .P12(P12), .P13(P13), .P14(P14), .P15(P15)
    );

`include "spi_tb_common.vh"

    reg [7:0] got, exp;
    integer   k;
    reg       test_seen;

    initial test_seen = 1'b0;
    always @(posedge P4) test_seen = 1'b1;

    initial begin
        if ($test$plusargs("dump")) begin
            $dumpfile("tb_12_chip.vcd");
            $dumpvars(0, tb_12_chip);
        end
        $display("=== tb_12_chip : chip-level pad test ===");
        hard_reset;

        check8("POR: DATA pads = 0x00", data, 8'h00);

        // one-hot walk proves every DATA pad is on the bit it claims
        for (k = 0; k < 8; k = k + 1) begin
            spi_write(8'h01 << k);
            check8("WRITE one-hot -> DATA pad", data, 8'h01 << k);
        end

        check1("TEST pad (P4) pulsed", test_seen, 1'b1);

        // explicit per-pad identity for 0xA5 = 1010_0101
        spi_write(8'hA5);
        check1("P6  = DATA[0] = 1", P6,  1'b1);
        check1("P7  = DATA[1] = 0", P7,  1'b0);
        check1("P9  = DATA[2] = 1", P9,  1'b1);
        check1("P10 = DATA[3] = 0", P10, 1'b0);
        check1("P11 = DATA[4] = 0", P11, 1'b0);
        check1("P12 = DATA[5] = 1", P12, 1'b1);
        check1("P13 = DATA[6] = 0", P13, 1'b0);
        check1("P14 = DATA[7] = 1", P14, 1'b1);

        // read direction through the same pads
        for (k = 0; k < 8; k = k + 1) begin
            spi_read(8'h01 << k, got);
            check8("READ one-hot <- DATA pad", got, 8'h01 << k);
        end
        spi_read(8'h5A, got);
        check8("READ 0x5A over SDIO (P2)", got, 8'h5A);

        // DIS must release the DATA pads at the package boundary
        dis = 1'b1;  #tsu;
        check8("DIS=1 releases DATA pads", data, 8'hzz);
        check1("DIS=1, CS high: P2 Hi-Z",  P2,   1'bz);
        dis = 1'b0;  #tsu;

        // reset through P15
        rstn = 1'b0;  #300;  rstn = 1'b1;  #300;
        check8("RSTN (P15) clears DATA pads", data, 8'h00);
        spi_write(8'hC7);
        check8("frame after pad-level reset",  data, 8'hC7);

        report_and_finish("tb_12_chip");
    end

endmodule

`default_nettype wire
