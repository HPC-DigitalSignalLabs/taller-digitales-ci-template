// SPDX-License-Identifier: Apache-2.0
// Opt-in JTAG counter; KEY0 resets the UART FIFOs and decoder together.
`timescale 1ns/1ps
`default_nettype none

module de1_soc_jtag_top (
    input wire CLOCK_50,
    input wire KEY0_N,
    output wire [9:0] LEDR
);
  (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED" *)
  logic [1:0] reset_release = 2'b00;
  wire rst_n = reset_release[1];
  wire address, chipselect, read_n, write_n, waitrequest;
  wire [31:0] writedata, readdata;
  wire [7:0] count;
  wire fault;
  wire unused_irq;

  always_ff @(posedge CLOCK_50 or negedge KEY0_N) begin
    if (!KEY0_N) reset_release <= 2'b00;
    else reset_release <= {reset_release[0], 1'b1};
  end

  jtag_counter u_design (
      .clk(CLOCK_50), .rst_n(rst_n),
      .m_address(address), .m_chipselect(chipselect),
      .m_read_n(read_n), .m_write_n(write_n),
      .m_writedata(writedata), .m_readdata(readdata),
      .m_waitrequest(waitrequest), .count(count), .fault(fault)
  );

  jtag_uart_system u_uart (
      .clk_clk(CLOCK_50), .reset_reset_n(rst_n),
      .avalon_jtag_slave_address(address),
      .avalon_jtag_slave_chipselect(chipselect),
      .avalon_jtag_slave_read_n(read_n),
      .avalon_jtag_slave_write_n(write_n),
      .avalon_jtag_slave_writedata(writedata),
      .avalon_jtag_slave_readdata(readdata),
      .avalon_jtag_slave_waitrequest(waitrequest),
      .irq_irq(unused_irq)
  );

  assign LEDR = {fault, rst_n, count};
endmodule

`default_nettype wire
