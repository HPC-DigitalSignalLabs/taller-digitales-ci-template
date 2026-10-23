// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Instituto Tecnológico de Costa Rica
//
// Parameterised combinational ALU for didactic smoke and library exercises.
//
// The opcode selects one basic arithmetic, logic or comparison operation.
// Comparison results are returned as a WIDTH-bit word with only bit 0 used.
// carry_out is meaningful for ADD and SUB only; for SUB it is asserted when
// the subtraction completes without borrow (a >= b). overflow is the signed
// overflow flag for ADD and SUB only.

`timescale 1ns/1ps
`default_nettype none

module alu #(
    parameter int unsigned WIDTH = 8
) (
    input  logic [WIDTH-1:0] a,
    input  logic [WIDTH-1:0] b,
    input  logic [2:0]       op,
    output logic [WIDTH-1:0] result,
    output logic             zero_flag,
    output logic             carry_out,
    output logic             overflow
);

  localparam logic [2:0] OP_ADD = 3'b000;
  localparam logic [2:0] OP_SUB = 3'b001;
  localparam logic [2:0] OP_AND = 3'b010;
  localparam logic [2:0] OP_OR  = 3'b011;
  localparam logic [2:0] OP_XOR = 3'b100;
  localparam logic [2:0] OP_EQ  = 3'b101;
  localparam logic [2:0] OP_LT  = 3'b110;

  always_comb begin
    result    = '0;
    zero_flag = 1'b1;
    carry_out = 1'b0;
    overflow  = 1'b0;

    unique case (op)
      OP_ADD: begin
        {carry_out, result} = {1'b0, a} + {1'b0, b};
        overflow = !(a[WIDTH-1] ^ b[WIDTH-1]) && (result[WIDTH-1] ^ a[WIDTH-1]);
      end

      OP_SUB: begin
        result = a - b;
        carry_out = (a >= b);
        overflow = (a[WIDTH-1] ^ b[WIDTH-1]) && (result[WIDTH-1] ^ a[WIDTH-1]);
      end

      OP_AND: begin
        result = a & b;
      end

      OP_OR: begin
        result = a | b;
      end

      OP_XOR: begin
        result = a ^ b;
      end

      OP_EQ: begin
        result[0] = (a == b);
      end

      OP_LT: begin
        result[0] = (a < b);
      end

      default: begin
        result = '0;
      end
    endcase

    zero_flag = (result == '0);
  end

endmodule

`default_nettype wire
