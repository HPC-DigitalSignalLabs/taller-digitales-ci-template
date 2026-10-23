// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Instituto Tecnológico de Costa Rica
//
// One-entry skid buffer for a ready/valid channel.
//
// Data is accepted when s_valid && s_ready and released when
// m_valid && m_ready. While the downstream side applies backpressure,
// m_valid and m_data remain stable. The registered full flag removes the
// combinational path from m_ready to s_ready.

`timescale 1ns/1ps
`default_nettype none

module skid_buffer #(
    parameter int unsigned WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst_n,

    input  logic             s_valid,
    output logic             s_ready,
    input  logic [WIDTH-1:0] s_data,

    output logic             m_valid,
    input  logic             m_ready,
    output logic [WIDTH-1:0] m_data
);

  logic             full;
  logic [WIDTH-1:0] data_q;

  assign s_ready = !full;
  assign m_valid = full || s_valid;
  assign m_data  = full ? data_q : s_data;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      full   <= 1'b0;
      data_q <= '0;
    end else if (full) begin
      if (m_ready) begin
        full <= 1'b0;
      end
    end else if (s_valid && !m_ready) begin
      full   <= 1'b1;
      data_q <= s_data;
    end
  end

endmodule

`default_nettype wire
