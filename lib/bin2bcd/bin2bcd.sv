// SPDX-License-Identifier: Apache-2.0
// Unsigned, combinational double-dabble. Digit 0 is bcd[3:0] (units).
// ceil(WIDTH/3) decimal digits safely cover ALL WIDTH-bit values because
// 2**3 < 10. Extra leading digits are zero, never silently truncated.
// DIGITS is derived, not an overrideable capacity parameter.
`timescale 1ns/1ps
`default_nettype none

module bin2bcd #(
    parameter integer WIDTH = 8
) (
    input  logic [WIDTH-1:0] binary,
    output logic [4*((WIDTH+2)/3)-1:0] bcd
);
  localparam integer DIGITS = (WIDTH+2)/3;

  initial begin
    if (WIDTH < 1 || WIDTH > 64)
      $fatal(1, "bin2bcd supports WIDTH from 1 through 64");
  end

  always_comb begin
    bcd = '0;
    for (integer bit_index = WIDTH-1; bit_index >= 0; bit_index = bit_index-1) begin
      for (integer digit = 0; digit < DIGITS; digit = digit+1) begin
        if (bcd[4*digit +: 4] >= 4'd5)
          bcd[4*digit +: 4] = bcd[4*digit +: 4] + 4'd3;
      end
      bcd = bcd << 1;
      bcd[0] = binary[bit_index];
    end
  end
endmodule

`default_nettype wire
