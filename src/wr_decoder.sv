// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Instituto Tecnológico de Costa Rica
//
// Sole Avalon-MM master for the generated Intel JTAG UART slave.
//
// Wire protocol (matches hardware.py exactly, big-endian on the wire):
//   write : 0x57 'W' | addr[31:0] | data[31:0]
//   read  : 0x52 'R' | addr[31:0]              -> response: data[31:0]
//
// The register port uses full 32-bit word addresses and single-cycle strobes.
// Invalid commands/register accesses and stalled frames latch fault until
// reset. There is no ACK, CRC or safe in-band resynchronization.

`timescale 1ns/1ps
`default_nettype none

module wr_decoder #(
    parameter integer TIMEOUT_CYCLES = 50000000
) (
    input  wire        clk,
    input  wire        rst_n,

    // Avalon-MM master port, driving the JTAG UART's exported slave port
    // (quartus/ip/jtag_uart_system.v: avalon_jtag_slave_*).
    output logic        m_address,      // 0 = DATA, 1 = CONTROL
    output logic        m_chipselect,
    output logic        m_read_n,
    output logic        m_write_n,
    output logic [31:0] m_writedata,
    input  wire  [31:0] m_readdata,
    input  wire         m_waitrequest,
    output wire [31:0]   reg_address,
    output wire [31:0]   reg_writedata,
    output wire         reg_write,
    output wire         reg_read,
    input  wire [31:0]   reg_readdata,
    input  wire         reg_error,
    output logic        fault
);

  localparam logic [7:0] CmdWrite = 8'h57;  // 'W'
  localparam logic [7:0] CmdRead  = 8'h52;  // 'R'

  logic [31:0] addr_reg;
  logic [31:0] data_reg;
  logic [31:0] resp_reg;

  // --- Generic single-outstanding Avalon-MM master primitive -------------------
  // avm_start pulses one cycle to launch a transaction; avm_busy is high until
  // the slave deasserts waitrequest. avm_rdata is valid the cycle avm_done
  // pulses (reads only).
  typedef enum logic {AVM_IDLE, AVM_WAIT} avm_state_e;
  avm_state_e avm_state;

  logic        avm_start;
  logic        avm_is_write;
  logic        avm_addr_bit;
  logic [31:0] avm_wdata;
  logic        avm_busy;
  logic        avm_done;
  logic [31:0] avm_rdata;

  assign avm_busy = (avm_state != AVM_IDLE);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      avm_state    <= AVM_IDLE;
      m_chipselect <= 1'b0;
      m_read_n     <= 1'b1;
      m_write_n    <= 1'b1;
      m_address    <= 1'b0;
      m_writedata  <= '0;
      avm_done     <= 1'b0;
      avm_rdata    <= '0;
    end else if (fault) begin
      avm_state    <= AVM_IDLE;
      m_chipselect <= 1'b0;
      m_read_n     <= 1'b1;
      m_write_n    <= 1'b1;
      avm_done     <= 1'b0;
    end else begin
      avm_done <= 1'b0;
      unique case (avm_state)
        AVM_IDLE: begin
          if (avm_start) begin
            m_chipselect <= 1'b1;
            m_address    <= avm_addr_bit;
            m_read_n     <= avm_is_write;
            m_write_n    <= ~avm_is_write;
            m_writedata  <= avm_wdata;
            avm_state    <= AVM_WAIT;
          end
        end
        AVM_WAIT: begin
          if (!m_waitrequest) begin
            m_chipselect <= 1'b0;
            m_read_n     <= 1'b1;
            m_write_n    <= 1'b1;
            avm_rdata    <= m_readdata;
            avm_done     <= 1'b1;
            avm_state    <= AVM_IDLE;
          end
        end
        default: avm_state <= AVM_IDLE;
      endcase
    end
  end

  // --- Byte framer + command dispatch ------------------------------------------
  typedef enum logic [2:0] {
    S_POLL_DATA,   // issue an Avalon read of DATA (word 0)
    S_GOT_BYTE,    // waiting for that read's avm_done
    S_DO_WRITE,
    S_DO_READ,
    S_POLL_CTRL,   // issue an Avalon read of CONTROL (word 1)
    S_CHECK_WSPACE,
    S_SEND_BYTE,   // issue an Avalon write of one response byte to DATA
    S_FAULT
  } state_e;

  typedef enum logic [1:0] {PH_CMD, PH_ADDR, PH_DATA} phase_e;

  state_e      state;
  phase_e      phase;
  logic [1:0]  byte_index;    // 0..3 within the current field
  logic        is_write_cmd;
  localparam integer TimeoutWidth = $clog2(TIMEOUT_CYCLES + 1);
  logic [TimeoutWidth-1:0] stalled_cycles;
  wire [7:0] rx_byte = avm_rdata[7:0];
  wire unused_uart_status = &{1'b0, avm_rdata[14:8]};
  wire progress = (state == S_GOT_BYTE && avm_done && avm_rdata[15])
                || state == S_DO_WRITE || state == S_DO_READ
                || (state == S_SEND_BYTE && !avm_busy && !avm_start);
  wire waiting = phase != PH_CMD || avm_busy
               || (state != S_POLL_DATA && state != S_GOT_BYTE);

  assign reg_address = addr_reg;
  assign reg_writedata = data_reg;
  assign reg_write = state == S_DO_WRITE && !fault;
  assign reg_read = state == S_DO_READ && !fault;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) stalled_cycles <= '0;
    else if (progress || !waiting) stalled_cycles <= '0;
    else if (!fault) stalled_cycles <= stalled_cycles + 1'b1;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state        <= S_POLL_DATA;
      phase        <= PH_CMD;
      byte_index   <= '0;
      is_write_cmd <= 1'b0;
      addr_reg     <= '0;
      data_reg     <= '0;
      resp_reg     <= '0;
      avm_start    <= 1'b0;
      avm_is_write <= 1'b0;
      avm_addr_bit <= 1'b0;
      avm_wdata    <= '0;
      fault        <= 1'b0;
    end else if (fault) begin
      avm_start <= 1'b0;
    end else if (waiting && !progress
                 && stalled_cycles == TimeoutWidth'(TIMEOUT_CYCLES - 1)) begin
      fault <= 1'b1;
      state <= S_FAULT;
      avm_start <= 1'b0;
    end else begin
      avm_start <= 1'b0;  // default: one-cycle pulse

      unique case (state)
        S_POLL_DATA: begin
          if (!avm_busy && !avm_start) begin
            avm_start    <= 1'b1;
            avm_is_write <= 1'b0;
            avm_addr_bit <= 1'b0;
            state        <= S_GOT_BYTE;
          end
        end

        S_GOT_BYTE: begin
          if (avm_done) begin
            if (avm_rdata[15]) begin
              unique case (phase)
                PH_CMD: begin
                  unique case (rx_byte)
                    CmdWrite: begin
                      is_write_cmd <= 1'b1;
                      phase        <= PH_ADDR;
                      byte_index   <= '0;
                    end
                    CmdRead: begin
                      is_write_cmd <= 1'b0;
                      phase        <= PH_ADDR;
                      byte_index   <= '0;
                    end
                    default: fault <= 1'b1;
                  endcase
                  state <= S_POLL_DATA;
                end

                PH_ADDR: begin
                  addr_reg <= {addr_reg[23:0], rx_byte};
                  if (byte_index == 2'd3) begin
                    byte_index <= '0;
                    if (is_write_cmd) begin
                      phase <= PH_DATA;
                      state <= S_POLL_DATA;
                    end else begin
                      state <= S_DO_READ;
                    end
                  end else begin
                    byte_index <= byte_index + 2'd1;
                    state      <= S_POLL_DATA;
                  end
                end

                PH_DATA: begin
                  data_reg <= {data_reg[23:0], rx_byte};
                  if (byte_index == 2'd3) begin
                    byte_index <= '0;
                    phase      <= PH_CMD;
                    state      <= S_DO_WRITE;
                  end else begin
                    byte_index <= byte_index + 2'd1;
                    state      <= S_POLL_DATA;
                  end
                end

                default: state <= S_POLL_DATA;
              endcase
            end else begin
              state <= S_POLL_DATA;  // no byte waiting yet: keep polling
            end
          end
        end

        S_DO_WRITE: begin
          fault <= reg_error;
          state <= reg_error ? S_FAULT : S_POLL_DATA;
        end

        S_DO_READ: begin
          resp_reg   <= reg_readdata;
          byte_index <= '0;
          phase      <= PH_CMD;
          fault      <= reg_error;
          state      <= reg_error ? S_FAULT : S_POLL_CTRL;
        end

        S_POLL_CTRL: begin
          if (!avm_busy && !avm_start) begin
            avm_start    <= 1'b1;
            avm_is_write <= 1'b0;
            avm_addr_bit <= 1'b1;
            state        <= S_CHECK_WSPACE;
          end
        end

        S_CHECK_WSPACE: begin
          if (avm_done) begin
            state <= (avm_rdata[31:16] != 16'd0) ? S_SEND_BYTE : S_POLL_CTRL;
          end
        end

        S_SEND_BYTE: begin
          if (!avm_busy && !avm_start) begin
            avm_start    <= 1'b1;
            avm_is_write <= 1'b1;
            avm_addr_bit <= 1'b0;
            avm_wdata    <= {24'd0, resp_reg[31:24]};
            resp_reg     <= {resp_reg[23:0], 8'd0};
            if (byte_index == 2'd3) begin
              byte_index <= '0;
              state      <= S_POLL_DATA;
            end else begin
              byte_index <= byte_index + 2'd1;
              state      <= S_POLL_CTRL;
            end
          end
        end

        default: begin
          fault <= 1'b1;
          state <= S_FAULT;
        end
      endcase
    end
  end

endmodule

`default_nettype wire
