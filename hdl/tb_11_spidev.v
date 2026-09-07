//============================================================================
// tb_11_spidev.v -- Raspberry Pi spidev / 3-wire driver sequence
//
//   Reproduces exactly what spi-bcm2835 does in SPI_3WIRE mode: DIS is set
//   from a GPIO before the ioctl, writebytes() sends a tx-only transfer
//   (REN=0, master drives), readbytes() issues an rx-only transfer (REN=1,
//   master's MOSI is high-Z).  Also demonstrates why xfer2() must not be
//   used.
//
//   Self-checking.  Runs against the RTL core or the synthesized gate-level
//   netlist without modification (both present module `spi_slave_sclk`).
//   Waveforms: add +dump on the vvp command line.
//============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_11_spidev;

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
            $dumpfile("tb_11_spidev.vcd");
            $dumpvars(0, tb_11_spidev);
        end
        $display("=== tb_11_spidev : Raspberry Pi spidev / 3-wire driver sequence ===");
        hard_reset;

        // --- writebytes([0xA5]) : DIS=0 set first, then a tx-only transfer
        dis = 1'b0;  #tsu;                        // GPIO write before ioctl
        spi_write(8'hA5);                         // REN=0, master drives SDIO
        check8("spidev writebytes([0xA5])", rx_data, 8'hA5);
        check8("DATA pads show 0xA5",       data,    8'hA5);

        // --- readbytes(1) : DIS=1 first, master's MOSI stays high-Z
        dis = 1'b1;  #tsu;
        spi_read(8'h3C, got);                     // REN=1, master released
        check8("spidev readbytes(1)",       got,     8'h3C);

        // --- a 4-byte writebytes() : CS stays low for the whole ioctl
        for (k = 0; k < 4; k = k + 1) tb_txbuf[k] = 8'h10 + k[7:0];
        spi_msg_write(4);
        check8("spidev writebytes(4 bytes)", rx_data, 8'h13);

        // --- a 4-byte readbytes()
        spi_msg_read(4, 8'hE7);
        check8("spidev readbytes(4) byte 0", tb_rxbuf[0], 8'hE7);
        check8("spidev readbytes(4) byte 3", tb_rxbuf[3], 8'hE7);

        // --- DIS toggled only while CS is high, many times in a row
        for (k = 0; k < 6; k = k + 1) begin
            spi_write(8'h40 + k[7:0]);
            check8("alternating W/R: write", rx_data, 8'h40 + k[7:0]);
            spi_read(8'h80 + k[7:0], got);
            check8("alternating W/R: read",  got,     8'h80 + k[7:0]);
        end

        // --- negative test: a full-duplex xfer2() drives SDIO during a READ
        //     frame and collides with the chip.  Contention must be visible,
        //     which is exactly why xfer/xfer2 are forbidden (design_notes 10.5).
        dis = 1'b1;  m_data_oe = 1'b1;  m_data = 8'h00;
        m_sdio_oe = 1'b1;  m_sdio = 1'b1;         // master wrongly drives '1'
        #tsu;  cs_n = 1'b0;  #tsu;
        check1("xfer2() causes SDIO contention", (sdio === 1'bx), 1'b1);
        cs_n = 1'b1;  m_sdio_oe = 1'b0;  m_data_oe = 1'b0;  dis = 1'b0;  #tsu;

        // --- and the part must recover cleanly afterwards
        spi_write(8'h7B);
        check8("recovers after contention", rx_data, 8'h7B);

        report_and_finish("tb_11_spidev");
    end

endmodule

`default_nettype wire
