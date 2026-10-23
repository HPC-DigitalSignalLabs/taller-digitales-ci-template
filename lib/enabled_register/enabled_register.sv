// SPDX-License-Identifier: Apache-2.0
// Reset to zero, otherwise load on enable, otherwise hold.
// Assert rst_n asynchronously; release synchronously to clk.
`timescale 1ns/1ps
`default_nettype none

module enabled_register #(
    parameter integer WIDTH = 8
) (
    input  logic clk,
    input  logic rst_n,
    input  logic enable,
    input  logic [WIDTH-1:0] data_in,
    output logic [WIDTH-1:0] data_out
);
  initial begin
    if (WIDTH < 1) $fatal(1, "WIDTH must be >= 1");
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) data_out <= '0;
    else if (enable) data_out <= data_in;
  end
endmodule

`default_nettype wire
