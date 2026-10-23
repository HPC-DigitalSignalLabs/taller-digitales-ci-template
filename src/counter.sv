// SPDX-License-Identifier: Apache-2.0
//
// Smoke design. Its only job is to prove the toolchain is alive: Verilator
// builds it, CocoTB drives it, PyTest reports it, and the gatekeeper turns
// green. It is deliberately trivial and is NOT the pattern for lib/ modules.
//
// Registered so the testbench must handle a clock and a reset, which is what a
// combinational smoke design fails to exercise.

`timescale 1ns/1ps
`default_nettype none

module counter #(
    parameter int unsigned WIDTH = 8
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              enable,
    output logic [WIDTH-1:0] count
);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      count <= '0;
    end else if (enable) begin
      count <= count + 1'b1;
    end
  end

endmodule

`default_nettype wire
