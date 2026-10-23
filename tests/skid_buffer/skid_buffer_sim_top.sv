// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Instituto Tecnológico de Costa Rica
//
// Simulation-only top. Keeping the reusable skid_buffer as a child avoids a
// VPI root-discovery failure observed with Verilator 5.028 when the
// parameterized library module itself is selected as the simulation root.

`timescale 1ns/1ps
`default_nettype none

`ifndef SKID_WIDTH
`define SKID_WIDTH 8
`endif

module skid_buffer_sim_top (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  s_valid,
    output logic                  s_ready,
    input  logic [`SKID_WIDTH-1:0] s_data,
    output logic                  m_valid,
    input  logic                  m_ready,
    output logic [`SKID_WIDTH-1:0] m_data
);

  skid_buffer #(
      .WIDTH(`SKID_WIDTH)
  ) dut (
      .clk,
      .rst_n,
      .s_valid,
      .s_ready,
      .s_data,
      .m_valid,
      .m_ready,
      .m_data
  );

endmodule

`undef SKID_WIDTH
`default_nettype wire
