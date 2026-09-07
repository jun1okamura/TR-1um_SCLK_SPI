//============================================================================
// spi_tb_common.vh -- shared SPI Mode-0 master BFM and scoreboard.
//
//   `include this inside a testbench module that has already declared:
//
//     reg  sclk, cs_n, dis, rstn;
//     reg  m_sdio, m_sdio_oe;
//     reg  [7:0] m_data;
//     reg  m_data_oe;
//     wire sdio;              // bidirectional SDIO net
//     wire [7:0] data;        // bidirectional DATA[7:0] net
//
//   ...and that drives the bidirectional nets from the master side:
//
//     assign sdio = m_sdio_oe ? m_sdio : 1'bz;
//     assign data = m_data_oe ? m_data : 8'hzz;
//
//   Timing is held in thi/tlo so a test can sweep the SCLK rate and duty
//   cycle without touching the BFM.
//============================================================================

    integer thi     = 100;   // SCLK high time [ns]
    integer tlo     = 100;   // SCLK low  time [ns]
    integer tsu     = 40;    // CS setup / hold margin [ns]
    integer checks  = 0;
    integer fails   = 0;

    reg [7:0] tb_txbuf [0:31];
    reg [7:0] tb_rxbuf [0:31];

    //------------------------------------------------------------------
    // scoreboard
    //------------------------------------------------------------------
    task check8(input [511:0] name, input [7:0] got, input [7:0] exp);
        begin
            checks = checks + 1;
            if (got === exp)
                $display("  %3d PASS  %0s (0x%02h)", checks, name, got);
            else begin
                fails = fails + 1;
                $display("  %3d FAIL  %0s : got 0x%02h expected 0x%02h",
                         checks, name, got, exp);
            end
        end
    endtask

    task check1(input [511:0] name, input got, input exp);
        begin
            checks = checks + 1;
            if (got === exp)
                $display("  %3d PASS  %0s (%b)", checks, name, got);
            else begin
                fails = fails + 1;
                $display("  %3d FAIL  %0s : got %b expected %b",
                         checks, name, got, exp);
            end
        end
    endtask

    task report_and_finish(input [511:0] title);
        begin
            $display("-------------------------------------------------");
            if (fails == 0) $display("%0s : All %0d checks PASSED", title, checks);
            else            $display("%0s : %0d of %0d checks FAILED",
                                     title, fails, checks);
            $display("-------------------------------------------------");
            $finish;
        end
    endtask

    //------------------------------------------------------------------
    // bus idle / reset
    //------------------------------------------------------------------
    task bus_idle;
        begin
            sclk = 1'b0;  cs_n = 1'b1;  dis = 1'b0;
            m_sdio = 1'b0;  m_sdio_oe = 1'b0;
            m_data = 8'h00; m_data_oe = 1'b0;
        end
    endtask

    task hard_reset;
        begin
            bus_idle;
            rstn = 1'b0;  #500;
            rstn = 1'b1;  #500;
        end
    endtask

    task sclk_pulse;
        begin
            #tlo;  sclk = 1'b1;
            #thi;  sclk = 1'b0;
        end
    endtask

    //------------------------------------------------------------------
    // WRITE message : DIS=0, master drives SDIO, chip drives DATA.
    //   n bytes are sent back to back with CS held low for the whole
    //   message -- exactly what spidev does for a multi-byte ioctl.
    //------------------------------------------------------------------
    task spi_msg_write(input integer n);
        integer i, b;
        reg [7:0] cur;
        begin
            dis = 1'b0;  m_data_oe = 1'b0;
            m_sdio_oe = 1'b1;  m_sdio = tb_txbuf[0][7];
            #tsu;  cs_n = 1'b0;  #tsu;
            for (b = 0; b < n; b = b + 1) begin
                cur = tb_txbuf[b];
                for (i = 7; i >= 0; i = i - 1) begin
                    m_sdio = cur[i];
                    sclk_pulse;
                end
            end
            #tsu;  cs_n = 1'b1;  m_sdio_oe = 1'b0;  #tsu;
        end
    endtask

    //------------------------------------------------------------------
    // READ message : DIS=1, master drives DATA, chip drives SDIO.
    //   SDIO is sampled just before each rising edge (master setup).
    //------------------------------------------------------------------
    task spi_msg_read(input integer n, input [7:0] pad_val);
        integer i, b;
        begin
            dis = 1'b1;  m_data_oe = 1'b1;  m_data = pad_val;
            m_sdio_oe = 1'b0;
            #tsu;  cs_n = 1'b0;  #tsu;
            for (b = 0; b < n; b = b + 1)
                for (i = 7; i >= 0; i = i - 1) begin
                    #tlo;  tb_rxbuf[b][i] = sdio;  sclk = 1'b1;
                    #thi;  sclk = 1'b0;
                end
            #tsu;  cs_n = 1'b1;  #tsu;
            m_data_oe = 1'b0;  dis = 1'b0;  #tsu;
        end
    endtask

    // single-byte convenience wrappers
    task spi_write(input [7:0] b);
        begin  tb_txbuf[0] = b;  spi_msg_write(1);  end
    endtask

    task spi_read(input [7:0] pad_val, output [7:0] got);
        begin  spi_msg_read(1, pad_val);  got = tb_rxbuf[0];  end
    endtask
