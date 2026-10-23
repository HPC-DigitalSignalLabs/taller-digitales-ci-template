// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
module jtag_counter_sim_top (
    input wire clk,
    input wire rst_n,
    output wire m_address,
    output wire m_chipselect,
    output wire m_read_n,
    output wire m_write_n,
    output wire [31:0] m_writedata,
    input wire [31:0] m_readdata,
    input wire m_waitrequest,
    output wire [7:0] count,
    output wire fault
);
  jtag_counter #(.TIMEOUT_CYCLES(512)) u_dut (.*);
endmodule
