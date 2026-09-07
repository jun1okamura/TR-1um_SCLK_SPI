//============================================================================
// spi_slave_sclk.v -- SCLK-domain synchronous SPI slave core (TR-1um)
//
//   3-wire (half-duplex) SPI : SCLK / SDIO / CS
//   Mode 0 (CPOL=0, CPHA=0), MSB first, single 8-bit frame, no burst.
//   SCLK is the only clock source; RSTN is the async reset.
//
//   Frame direction is set externally by DIS (same pin that controls the
//   DATA[7:0] pad direction), so no command byte and no R/W bit is needed:
//
//     DIS = 0 : WRITE frame -- SDIO is an input.  The received byte is
//               latched into rx_data[] and driven out on the DATA pads.
//     DIS = 1 : READ  frame -- DATA pads are inputs (tx_data[]).  The chip
//               drives SDIO while CS is low and shifts tx_data[] out.
//
//   Because the two directions never overlap, ONE shift register serves
//   both.  Mode 0 requires SDIO to change on the falling edge and be
//   sampled on the rising edge, so the shared register is clocked on
//   sclk ^ dis :  posedge sclk when writing, negedge sclk when reading.
//   DIS is static for the duration of a frame, so this select never
//   changes while CS is low.
//============================================================================
`default_nettype none

module spi_slave_sclk (
    input  wire       rstn,      // async reset, active low
    input  wire       sclk,      // SPI clock -- sole clock domain
    input  wire       cs_n,      // chip select, active low (frame boundary)
    input  wire       dis,       // 0 = WRITE frame, 1 = READ frame
    input  wire       sdio_in,   // SDIO pad, input path
    output wire       sdio_out,  // SDIO pad, output value
    output wire       sdio_oe,   // SDIO pad, 1 = drive
    input  wire [7:0] tx_data,   // DATA pads, input path  (READ source)
    output wire [7:0] rx_data,   // DATA pads, output value (WRITE result)
    output wire       data_oe,   // DATA pads, 1 = drive
    output wire       byte_end   // test output: high during the 8th bit
);

    //------------------------------------------------------------------
    // bit counter -- async-cleared while CS is de-asserted, so a frame
    // always starts at bit 7 no matter what SCLK did while idle.
    //------------------------------------------------------------------
    wire      cnt_rstn = rstn & ~cs_n;
    reg [2:0] bit_cnt;

    always @(posedge sclk or negedge cnt_rstn)
        if (!cnt_rstn) bit_cnt <= 3'd0;
        else           bit_cnt <= bit_cnt + 3'd1;

    wire last_bit = (bit_cnt == 3'd7);

    assign byte_end = last_bit & ~cs_n;

    //------------------------------------------------------------------
    // shared 8-bit shift register
    //   WRITE : posedge sclk, shifts SDIO in
    //   READ  : negedge sclk, parallel-loads tx_data then shifts out
    //------------------------------------------------------------------
    wire shift_clk = sclk ^ dis;
    wire load      = dis & (bit_cnt == 3'd1);   // READ: load at 1st falling edge
    wire shift_in  = sdio_in & ~dis;            // READ shifts in 0s

    reg [7:0] sr;

    always @(posedge shift_clk or negedge rstn)
        if (!rstn)     sr <= 8'h00;
        else if (load) sr <= {tx_data[6:0], 1'b0};
        else           sr <= {sr[6:0], shift_in};

    //------------------------------------------------------------------
    // rx_data hold : capture the completed byte on the 8th rising edge
    //                (WRITE frames only -- during a READ, SDIO is not
    //                 driven by the master and sr holds read data)
    //------------------------------------------------------------------
    wire      rx_cap = ~cs_n & ~dis & last_bit;
    reg [7:0] rx_data_r;

    always @(posedge sclk or negedge rstn)
        if (!rstn)       rx_data_r <= 8'h00;
        else if (rx_cap) rx_data_r <= {sr[6:0], sdio_in};

    assign rx_data = rx_data_r;

    //------------------------------------------------------------------
    // MSB select
    //   Mode 0 needs the MSB valid before the first rising edge, i.e.
    //   before any clock edge exists -- so bit 7 comes straight from
    //   tx_data[7] and bits 6..0 come from the shift register.
    //
    //   Selecting on (bit_cnt == 0) directly would move SDIO on the 1st
    //   and 8th RISING edges (bit_cnt changes there), which violates
    //   Mode 0 and eats into the master's hold margin.  Registering the
    //   select on shift_clk instead confines every SDIO transition to a
    //   falling edge, and re-arms it at each byte boundary so multi-byte
    //   messages keep working.  Async-cleared with the bit counter, so
    //   the first bit of a message is always tx_data[7].
    //------------------------------------------------------------------
    reg msb_done;

    always @(posedge shift_clk or negedge cnt_rstn)
        if (!cnt_rstn) msb_done <= 1'b0;
        else           msb_done <= (bit_cnt != 3'd0);

    //------------------------------------------------------------------
    // SDIO / DATA pad control
    //------------------------------------------------------------------
    assign sdio_out = msb_done ? sr[7] : tx_data[7];
    assign sdio_oe  = dis & ~cs_n;
    assign data_oe  = ~dis;

endmodule

`default_nettype wire
