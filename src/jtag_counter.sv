// SPDX-License-Identifier: Apache-2.0
// Deterministic register wrapper around the maintained eight-bit counter.
`timescale 1ns/1ps
`default_nettype none

module jtag_counter #(
    parameter integer TIMEOUT_CYCLES = 50000000
) (
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
  wire [31:0] reg_address;
  wire [31:0] reg_writedata;
  wire reg_write, reg_read;
  logic [31:0] reg_readdata;
  logic reg_error;
  logic [31:0] steps;
  logic counter_rst_n;
  wire reset_command = reg_write && reg_address == 32'd1 && reg_writedata == 32'd1;
  wire step_command = reg_write && reg_address == 32'd2 && reg_writedata <= 32'd1;

  wr_decoder #(.TIMEOUT_CYCLES(TIMEOUT_CYCLES)) u_decoder (
      .clk(clk), .rst_n(rst_n),
      .m_address(m_address), .m_chipselect(m_chipselect),
      .m_read_n(m_read_n), .m_write_n(m_write_n),
      .m_writedata(m_writedata), .m_readdata(m_readdata),
      .m_waitrequest(m_waitrequest),
      .reg_address(reg_address), .reg_writedata(reg_writedata),
      .reg_write(reg_write), .reg_read(reg_read),
      .reg_readdata(reg_readdata), .reg_error(reg_error), .fault(fault)
  );

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      counter_rst_n <= 1'b0;
      steps <= '0;
    end else begin
      counter_rst_n <= !reset_command;
      if (reset_command) steps <= '0;
      else if (step_command) steps <= steps + 1'b1;
    end
  end

  counter #(.WIDTH(8)) u_counter (
      .clk(clk), .rst_n(counter_rst_n),
      .enable(step_command && reg_writedata[0]), .count(count)
  );

  always_comb begin
    reg_readdata = '0;
    reg_error = 1'b0;
    if (reg_read) begin
      case (reg_address)
        32'd0: reg_readdata = 32'h434E5431;  // "CNT1", protocol identity
        32'd3: reg_readdata = {24'd0, count};
        32'd4: reg_readdata = steps;
        default: reg_error = 1'b1;
      endcase
    end
    if (reg_write) begin
      case (reg_address)
        32'd1: reg_error = reg_writedata != 32'd1;
        32'd2: reg_error = reg_writedata > 32'd1;
        default: reg_error = 1'b1;
      endcase
    end
  end
endmodule

`default_nettype wire
