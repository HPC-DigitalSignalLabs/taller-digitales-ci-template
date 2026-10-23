// SPDX-License-Identifier: Apache-2.0
// Two-stage synchronizer for ONE asynchronous level, not a bus or pulse bridge.
// RTL simulation cannot model metastability or establish an MTBF.
// Assert reset asynchronously; the integrator must release it synchronously
// to clk. Keep stage1 private and physically close to sync_out in Quartus.
`timescale 1ns/1ps
`default_nettype none

module sync_2ff (
    input  logic clk,
    input  logic rst_n,
    input  logic async_in,
    output logic sync_out
);
  (* async_reg = "true" *) logic stage1;
  // Attributes are synthesis hints, not proof of physical CDC correctness.
  (* async_reg = "true" *) logic stage2;

  assign sync_out = stage2;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      stage1 <= 1'b0;
      stage2 <= 1'b0;
    end else begin
      stage1 <= async_in;
      stage2 <= stage1;
    end
  end
endmodule

`default_nettype wire
