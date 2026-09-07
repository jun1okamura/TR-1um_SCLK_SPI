//============================================================================
// tr_1um_3wire_SPI.v -- chip top level (core + pad wiring)
//
//   OSS_FRAME has 16 bond pads; VDD/VSS are implicit here, leaving 14
//   signal pads.  Assignment (P8 does not exist in this frame):
//
//     P1  SCLK      input    SPI clock            (BUFTH)
//     P2  SDIO      bidir    SPI data, half duplex(BUFTH on the in path)
//     P3  CS        input    chip select, act.low (BUFTH)
//     P4  TEST      output   byte_end -- high during the 8th bit
//     P5  DIS       input    0 = WRITE frame / DATA driven
//                            1 = READ  frame / DATA sampled, SDIO driven
//     P6  DATA[0]   bidir  |
//     P7  DATA[1]   bidir  |
//     P9  DATA[2]   bidir  |  direction controlled by DIS
//     P10 DATA[3]   bidir  |  ( DIS=0 -> chip drives, DIS=1 -> Hi-Z )
//     P11 DATA[4]   bidir  |
//     P12 DATA[5]   bidir  |
//     P13 DATA[6]   bidir  |
//     P14 DATA[7]   bidir  |
//     P15 RSTN      input    async reset, active low
//
//   BUFTH is inserted on the SPI input pins per the AP&R plan; SCLK is
//   branched per placement row at AP&R time (not modelled here).
//============================================================================
`default_nettype none

module tr_1um_3wire_SPI (
    inout wire P1,  inout wire P2,  inout wire P3,  inout wire P4,
    inout wire P5,  inout wire P6,  inout wire P7,  inout wire P9,
    inout wire P10, inout wire P11, inout wire P12, inout wire P13,
    inout wire P14, inout wire P15
);

    // ---- threshold buffers on the SPI inputs ------------------------
    wire sclk_i, sdio_i, cs_n_i;
    BUFTH u_bth_sclk (.A(P1), .Y(sclk_i));
    BUFTH u_bth_sdio (.A(P2), .Y(sdio_i));
    BUFTH u_bth_cs   (.A(P3), .Y(cs_n_i));

    wire dis_i  = P5;
    wire rstn_i = P15;

    // ---- DATA pads --------------------------------------------------
    wire [7:0] tx_data = {P14, P13, P12, P11, P10, P9, P7, P6};
    wire [7:0] rx_data;
    wire       data_oe;

    // ---- core -------------------------------------------------------
    wire sdio_out, sdio_out_b, sdio_oe, byte_end, byte_end_b;

    spi_slave_sclk u_core (
        .rstn     (rstn_i),
        .sclk     (sclk_i),
        .cs_n     (cs_n_i),
        .dis      (dis_i),
        .sdio_in  (sdio_i),
        .sdio_out (sdio_out),
        .sdio_oe  (sdio_oe),
        .tx_data  (tx_data),
        .rx_data  (rx_data),
        .data_oe  (data_oe),
        .byte_end (byte_end)
    );

    BUF_X1 u_buf_sdio_o (.A(sdio_out), .Y(sdio_out_b));
    BUF_X1 u_buf_test   (.A(byte_end), .Y(byte_end_b));

    // ---- pad drivers ------------------------------------------------
    assign P2 = sdio_oe ? sdio_out_b : 1'bz;
    assign {P14, P13, P12, P11, P10, P9, P7, P6} = data_oe ? rx_data : 8'hzz;
    assign P4 = byte_end_b;

endmodule

`default_nettype wire
