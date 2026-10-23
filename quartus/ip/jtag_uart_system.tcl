# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
#
# Headless Platform Designer (Qsys) system generator for the "JTAG UART Intel
# FPGA IP" (altera_avalon_jtag_uart), following ADR-0004 Level A (primary
# transport). Run declaratively via qsys-script, never through the GUI, so the
# system is reproducible from scratch and its parameters are recorded here
# instead of hidden inside a hand-edited .qsys binary/XML blob.
#
# No CPU, no bus fabric: this system contains exactly one component. Its
# clock, reset, Avalon-MM slave port and interrupt are exported directly to
# the system boundary, so the custom W/R decoder in RTL (outside Qsys) can
# drive the Avalon-MM slave port itself, acting as the system's sole master.
#
# Usage (from the template root, or any cwd -- paths below are absolute):
#   & 'C:\intelFPGA_lite\18.1\quartus\sopc_builder\bin\qsys-script.exe' \
#       --script=quartus\ip\jtag_uart_system.tcl
#
# Regenerate the Verilog output after editing this file with:
#   & 'C:\intelFPGA_lite\18.1\quartus\sopc_builder\bin\qsys-generate.exe' \
#       quartus\ip\jtag_uart_system.qsys --synthesis=VERILOG \
#       --output-directory=quartus\ip\jtag_uart_system

package require -exact qsys 12.0

create_system jtag_uart_system

set_project_property DEVICE_FAMILY {Cyclone V}
set_project_property DEVICE {5CSEMA5F31C6}

# --- JTAG UART instance -------------------------------------------------------
# Defaults kept explicit even where they match the component default, so a
# reviewer can see the exact generation parameters without opening the GUI.
add_instance jtag_uart_0 altera_avalon_jtag_uart 18.1
set_instance_parameter_value jtag_uart_0 {allowMultipleConnections} {false}
set_instance_parameter_value jtag_uart_0 {readBufferDepth} {64}
set_instance_parameter_value jtag_uart_0 {readIRQThreshold} {8}
set_instance_parameter_value jtag_uart_0 {useRegistersForReadBuffer} {false}
set_instance_parameter_value jtag_uart_0 {useRegistersForWriteBuffer} {false}
set_instance_parameter_value jtag_uart_0 {writeBufferDepth} {64}
set_instance_parameter_value jtag_uart_0 {writeIRQThreshold} {8}
set_instance_parameter_value jtag_uart_0 {simInteractiveOptions} {NO_INTERACTIVE_WINDOWS}

# --- Export every interface to the system boundary ----------------------------
add_interface clk clock sink
set_interface_property clk EXPORT_OF jtag_uart_0.clk

add_interface reset reset sink
set_interface_property reset EXPORT_OF jtag_uart_0.reset

add_interface avalon_jtag_slave avalon slave
set_interface_property avalon_jtag_slave EXPORT_OF jtag_uart_0.avalon_jtag_slave

add_interface irq interrupt sender
set_interface_property irq EXPORT_OF jtag_uart_0.irq

save_system {jtag_uart_system.qsys}
