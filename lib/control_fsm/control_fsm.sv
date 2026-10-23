// SPDX-License-Identifier: Apache-2.0
// Single-clock transaction controller. Inputs must already be synchronous.
// start is accepted only in IDLE; finish only in RUN. DONE lasts one cycle.
// A held start begins another transaction after the DONE/IDLE turnaround.
`timescale 1ns/1ps
`default_nettype none

module control_fsm (
    input  logic clk,
    input  logic rst_n,
    input  logic start,
    input  logic finish,
    output logic busy,
    output logic done
);
  typedef enum logic [1:0] {IDLE, RUN, COMPLETE} state_t;
  state_t state, next_state;

  always_comb begin
    next_state = state;
    case (state)
      IDLE:     if (start) next_state = RUN;
      RUN:      if (finish) next_state = COMPLETE;
      COMPLETE: next_state = IDLE;
      default:  next_state = IDLE;
    endcase
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) state <= IDLE;
    else state <= next_state;
  end

  assign busy = (state == RUN);
  assign done = (state == COMPLETE);
endmodule

`default_nettype wire
