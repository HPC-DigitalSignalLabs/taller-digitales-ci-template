// SPDX-License-Identifier: Apache-2.0
// Registered one-cycle event on a sampled rising (RISING=1) or falling edge.
// signal_in must already be synchronous to clk. This block is NOT a CDC.
// Reset clears history to zero: a high input on release is a rising event.
// The integrator must synchronize reset deassertion to clk.
`timescale 1ns/1ps
`default_nettype none

module edge_detector #(
    parameter bit RISING = 1'b1
) (
    input  logic clk,
    input  logic rst_n,
    input  logic signal_in,
    output logic pulse
);
  logic previous;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      previous <= 1'b0;
      pulse    <= 1'b0;
    end else begin
      previous <= signal_in;
      pulse <= RISING ? signal_in : !signal_in;
    end
  end
endmodule

`default_nettype wire
