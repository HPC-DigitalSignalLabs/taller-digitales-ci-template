// SPDX-License-Identifier: Apache-2.0
// Decimal ONLY: 10..15 blank the display, unlike boards/seg7_hex.sv.
// seg[6:0] = {g,f,e,d,c,b,a}; active_low=1 suits the DE1-SoC HEX pins.
// Combinational polarity input can be tied to a constant by the integrator.
`timescale 1ns/1ps
`default_nettype none

module bcd_to_7seg (
    input  logic [3:0] bcd,
    input  logic active_low,
    output logic [6:0] seg
);
  logic [6:0] lit;

  always_comb begin
    case (bcd)
      4'd0: lit = 7'b0111111;
      4'd1: lit = 7'b0000110;
      4'd2: lit = 7'b1011011;
      4'd3: lit = 7'b1001111;
      4'd4: lit = 7'b1100110;
      4'd5: lit = 7'b1101101;
      4'd6: lit = 7'b1111101;
      4'd7: lit = 7'b0000111;
      4'd8: lit = 7'b1111111;
      4'd9: lit = 7'b1101111;
      default: lit = 7'b0000000;
    endcase
  end
  assign seg = active_low ? ~lit : lit;
endmodule

`default_nettype wire
