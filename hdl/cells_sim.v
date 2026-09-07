//============================================================================
// cells_sim.v -- behavioural models of the TR1um_5_stdcell cells used by
//                this project (structural top level + gate-level netlist).
//                Simulation only; the real cells come from the PDK.
//                Pin names/order follow TR-1um_I2C_2026/src/*.cir.
//============================================================================
`default_nettype none

module INV_X1  (input wire A,          output wire Y); assign Y = ~A;        endmodule
module BUF_X1  (input wire A,          output wire Y); assign Y =  A;        endmodule
module BUF_X2  (input wire A,          output wire Y); assign Y =  A;        endmodule
module BUF_X4  (input wire A,          output wire Y); assign Y =  A;        endmodule
module BUF_X16 (input wire A,          output wire Y); assign Y =  A;        endmodule
module BUFTH   (input wire A,          output wire Y); assign Y =  A;        endmodule
module NAND2   (input wire A, B,       output wire Y); assign Y = ~(A & B);  endmodule
module NOR2    (input wire A, B,       output wire Y); assign Y = ~(A | B);  endmodule
module AND2_X1 (input wire A, B,       output wire Y); assign Y =  (A & B);  endmodule
module OR2     (input wire A, B,       output wire Y); assign Y =  (A | B);  endmodule
module NAND3   (input wire A, B, C,    output wire Y); assign Y = ~(A&B&C);  endmodule
module NOR3    (input wire A, B, C,    output wire Y); assign Y = ~(A|B|C);  endmodule
module OR3     (input wire A, B, C,    output wire Y); assign Y =  (A|B|C);  endmodule
module NOR4    (input wire A, B, C, D, output wire Y); assign Y = ~(A|B|C|D);endmodule
module OR4     (input wire A, B, C, D, output wire Y); assign Y =  (A|B|C|D);endmodule
module AND4_X1 (input wire A, B, C, D, output wire Y); assign Y =  (A&B&C&D);endmodule
module XOR2    (input wire A, B,       output wire Y); assign Y =  (A ^ B);  endmodule
module XNOR2   (input wire A, B,       output wire Y); assign Y = ~(A ^ B);  endmodule
module MUX2    (input wire A, B, S,    output wire Y); assign Y = S ? B : A; endmodule

// D flip-flop, async active-low reset
module DFFRB (
    input  wire D, CK, RSTB,
    output reg  Q,
    output wire QB
);
    always @(posedge CK or negedge RSTB)
        if (!RSTB) Q <= 1'b0;
        else       Q <= D;
    assign QB = ~Q;
endmodule

// Mux-D flip-flop, async active-low reset (S=0 -> A, S=1 -> B).
// Not inferred by yosys; created by cell merging at AP&R time.
module MUXDFFRB (
    input  wire A, B, S, CK, RSTB,
    output reg  Q,
    output wire QB
);
    always @(posedge CK or negedge RSTB)
        if (!RSTB) Q <= 1'b0;
        else       Q <= S ? B : A;
    assign QB = ~Q;
endmodule

`default_nettype wire
