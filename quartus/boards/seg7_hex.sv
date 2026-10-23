// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Instituto Tecnológico de Costa Rica
//
// Shared combinational hexadecimal-to-seven-segment decoder for the board-level
// top wrappers (de1_soc_top.sv, de10_standard_top.sv). Kept in its own file so
// each Quartus revision compiles exactly one board top plus this decoder,
// without a duplicate-module collision.
//
// Output is ACTIVE-LOW (0 lights a segment), matching the Cyclone V SoC teaching
// boards' HEX displays. Bit order is {g,f,e,d,c,b,a} on seg[6:0].
//
// Course-maintained reference material (analogous to lib/).

`timescale 1ns/1ps
`default_nettype none

module seg7_hex (
    input  wire  [3:0] nibble,
    output logic [6:0] seg
);

  always_comb begin
    unique case (nibble)
      4'h0: seg = 7'b1000000;
      4'h1: seg = 7'b1111001;
      4'h2: seg = 7'b0100100;
      4'h3: seg = 7'b0110000;
      4'h4: seg = 7'b0011001;
      4'h5: seg = 7'b0010010;
      4'h6: seg = 7'b0000010;
      4'h7: seg = 7'b1111000;
      4'h8: seg = 7'b0000000;
      4'h9: seg = 7'b0010000;
      4'hA: seg = 7'b0001000;
      4'hB: seg = 7'b0000011;
      4'hC: seg = 7'b1000110;
      4'hD: seg = 7'b0100001;
      4'hE: seg = 7'b0000110;
      4'hF: seg = 7'b0001110;
      default: seg = 7'b1111111;
    endcase
  end

endmodule

`default_nettype wire
