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
//   This is the SIMULATION model of the assembled chip, and it now mirrors
//   what the layout really contains: OSS_FRAME_GIO plus the core, and
//   nothing else at top level.  The BUFTH threshold buffers on SCLK / SDIO /
//   CS live INSIDE the core (scripts/insert_bufth.py puts them there), so
//   instantiating them again here would have double-buffered those paths and
//   left the chip-level LVS reference with two cells the GDS does not have.
//   SCLK is branched per placement row at AP&R time (not modelled here).
//
//   Pad direction control maps onto the GIO pad cell's HIZ pin, which is
//   ACTIVE LOW (HIZ=0 drives, HIZ=1 is Hi-Z):
//     P2       HIZ2   <- core.sdio_oe_n  directly
//     DATA x8  HIZ<n> <- the DIS pad net (core drives DATA when dis = 0)
//     P4       HIZ4   tied low, always driven
//   See design_notes.md section 16.
//============================================================================
`default_nettype none

module tr_1um_3wire_SPI (
    inout wire P1,  inout wire P2,  inout wire P3,  inout wire P4,
    inout wire P5,  inout wire P6,  inout wire P7,  inout wire P9,
    inout wire P10, inout wire P11, inout wire P12, inout wire P13,
    inout wire P14, inout wire P15
);

    // ---- pad inputs (BUFTH lives inside the core) --------------------
    wire sclk_i = P1;
    wire sdio_i = P2;
    wire cs_n_i = P3;
    wire dis_i  = P5;
    wire rstn_i = P15;

    // ---- DATA pads --------------------------------------------------
    wire [7:0] tx_data = {P14, P13, P12, P11, P10, P9, P7, P6};
    wire [7:0] rx_data;
    // data_oe is left unconnected on purpose: the DATA pads take their HIZ
    // off the DIS pad net, which carries the same information (data_oe = ~dis)
    // with no wire from the core.  Kept as a port for probing.
    wire       data_oe;

    // ---- core -------------------------------------------------------
    wire sdio_out, sdio_oe_n, byte_end;

    spi_slave_sclk u_core (
        .rstn     (rstn_i),
        .sclk     (sclk_i),
        .cs_n     (cs_n_i),
        .dis      (dis_i),
        .sdio_in  (sdio_i),
        .sdio_out (sdio_out),
        .sdio_oe_n(sdio_oe_n),
        .tx_data  (tx_data),
        .rx_data  (rx_data),
        .data_oe  (data_oe),
        .byte_end (byte_end)
    );

    // ---- pad drivers (the GIO pad cells, behaviourally) -------------
    //   HIZ is active low, hence the sense of each condition here.
    assign P2 = sdio_oe_n ? 1'bz : sdio_out;
    assign {P14, P13, P12, P11, P10, P9, P7, P6} = dis_i ? 8'hzz : rx_data;
    assign P4 = byte_end;

endmodule

`default_nettype wire
