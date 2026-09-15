// TR-1um 標準セルのゼロ遅延 Verilog モデル
//
// scripts/char/mkcellverilog.py が cellspec.py から自動生成。手で編集しないこと。
// cellspec.py の表は check_comb.py / check_seq.py が ngspice で
// 実レイアウトの抽出ネットリストと突き合わせて検証している。
//
// 用途: .lib でマッピングした合成後ネットリストを iverilog で回す。
// 遅延は .lib + STA の仕事。ここに入っているのは、クロス結合ループを
// iverilog で収束させるためだけの単位遅延（--delay）。

`timescale 1ns/1ps

module AND2_X1 (
    input  A, B,
    output Y,
    input  VDD, GND);
  assign Y = A & B;
endmodule

module AND3_X1 (
    input  A, B, C,
    output Y,
    input  VDD, GND);
  assign Y = A & B & C;
endmodule

module AND4_X1 (
    input  A, B, C, D,
    output Y,
    input  VDD, GND);
  assign Y = A & B & C & D;
endmodule

module BUFTH (
    input  A,
    output Y,
    input  VDD, GND);
  assign Y = A;
endmodule

module BUF_X1 (
    input  A,
    output Y,
    input  VDD, GND);
  assign Y = A;
endmodule

module BUF_X2 (
    input  A,
    output Y,
    input  VDD, GND);
  assign Y = A;
endmodule

module DEL1 (
    input  A,
    output Y,
    input  VDD, GND);
  assign Y = A;
endmodule

module INV_X1 (
    input  A,
    output Y,
    input  VDD, GND);
  assign Y = ~A;
endmodule

module INV_X2 (
    input  A,
    output Y,
    input  VDD, GND);
  assign Y = ~A;
endmodule

module MUX2 (
    input  A, B, S,
    output Y,
    input  VDD, GND);
  assign Y = (A & ~S) | (B & S);
endmodule

module NAND2 (
    input  A, B,
    output Y,
    input  VDD, GND);
  assign Y = ~(A & B);
endmodule

module NAND3 (
    input  A, B, C,
    output Y,
    input  VDD, GND);
  assign Y = ~(A & B & C);
endmodule

module NAND4 (
    input  A, B, C, D,
    output Y,
    input  VDD, GND);
  assign Y = ~(A & B & C & D);
endmodule

module NOR2 (
    input  A, B,
    output Y,
    input  VDD, GND);
  assign Y = ~(A | B);
endmodule

module NOR3 (
    input  A, B, C,
    output Y,
    input  VDD, GND);
  assign Y = ~(A | B | C);
endmodule

module NOR4 (
    input  A, B, C, D,
    output Y,
    input  VDD, GND);
  assign Y = ~(A | B | C | D);
endmodule

module OR2 (
    input  A, B,
    output Y,
    input  VDD, GND);
  assign Y = A | B;
endmodule

module OR3 (
    input  A, B, C,
    output Y,
    input  VDD, GND);
  assign Y = A | B | C;
endmodule

module OR4 (
    input  A, B, C, D,
    output Y,
    input  VDD, GND);
  assign Y = A | B | C | D;
endmodule

module XNOR2 (
    input  A, B,
    output Y,
    input  VDD, GND);
  assign Y = ~(A ^ B);
endmodule

module XOR2 (
    input  A, B,
    output Y,
    input  VDD, GND);
  assign Y = A ^ B;
endmodule

module DFF (
    input  CK, D,
    output Q, QB,
    input  VDD, GND);
  reg q;
  always @(posedge CK)
    q <= D;
  assign Q  = q;
  assign QB = ~q;
endmodule

module DFFRB (
    input  CK, D, RSTB,
    output Q, QB,
    input  VDD, GND);
  reg q;
  always @(posedge CK or negedge RSTB)
    if (~RSTB) q <= 1'b0;
    else q <= D;
  assign Q  = q;
  assign QB = ~q;
endmodule

module DFFS (
    input  CK, D, SET,
    output Q, QB,
    input  VDD, GND);
  reg q;
  always @(posedge CK or posedge SET)
    if (SET) q <= 1'b1;
    else q <= D;
  assign Q  = q;
  assign QB = ~q;
endmodule

module MUXDFFRB (
    input  CK, A, B, S, RSTB,
    output Q, QB,
    input  VDD, GND);
  reg q;
  always @(posedge CK or negedge RSTB)
    if (~RSTB) q <= 1'b0;
    else q <= (A & ~S) | (B & S);
  assign Q  = q;
  assign QB = ~q;
endmodule

module RSLATCH (input S, R, output Q, QB, input VDD, GND);
  // ゼロ遅延ではクロス結合が収束しないので reg で書く。
  // **S=R=1 の挙動が実物と違う**（実物は Q=QB=0）。
  reg q;
  always @* begin
    if (R) q = 1'b0;
    else if (S) q = 1'b1;
  end
  assign Q  = q;
  assign QB = ~q;
endmodule

