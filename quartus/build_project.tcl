# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
# Create a Quartus project for the template without editing RTL.
#
# Typical use:
#   quartus_sh -t quartus/build_project.tcl
#   quartus_sh -t quartus/build_project.tcl --target-board de10_standard
#   quartus_sh -t quartus/build_project.tcl --project-name counter \
#       --revision counter --top-level counter_board
#
# Environment fallbacks:
#   TARGET_BOARD, PROJECT_NAME, REVISION, TOP_LEVEL_ENTITY

package require ::quartus::project

proc fail {msg} {
    post_message -type error $msg
    qexit -error
}

proc take_option {name default} {
    global argv
    set idx [lsearch -exact $argv $name]
    if {$idx < 0} {
        return $default
    }
    if {$idx == [llength $argv] - 1} {
        fail "missing value for $name"
    }
    return [lindex $argv [expr {$idx + 1}]]
}

proc env_or_default {name default} {
    if {[info exists ::env($name)] && $::env($name) ne ""} {
        return $::env($name)
    }
    return $default
}

proc collect_sv_files {root} {
    set files {}
    if {![file isdirectory $root]} {
        return $files
    }
    foreach entry [glob -nocomplain -directory $root *] {
        if {[file isdirectory $entry]} {
            set files [concat $files [collect_sv_files $entry]]
        } elseif {[file extension $entry] eq ".sv"} {
            lappend files $entry
        }
    }
    return [lsort -unique $files]
}

set script_dir    [file dirname [file normalize [info script]]]
set template_root [file normalize [file join $script_dir ".."]]
set quartus_dir   [file normalize [file join $template_root "quartus"]]

set target_board [take_option --target-board [env_or_default TARGET_BOARD "de1_soc"]]
set project_name [take_option --project-name [env_or_default PROJECT_NAME "template_quartus"]]
set revision     [take_option --revision [env_or_default REVISION $project_name]]

switch -- $target_board {
    de1_soc {
        set family       "Cyclone V"
        set device       "5CSEMA5F31C6"
        set board_label  "Terasic DE1-SoC"
        set board_top    "de1_soc_top"
        set sdc_file     [file join $quartus_dir "targets" "de1_soc.sdc"]
    }
    de10_standard {
        set family       "Cyclone V"
        set device       "5CSXFC6D6F31C6"
        set board_label  "Terasic DE10-Standard"
        set board_top    "de10_standard_top"
        set sdc_file     [file join $quartus_dir "targets" "de10_standard.sdc"]
    }
    default {
        fail "unsupported TARGET_BOARD '$target_board' (expected de1_soc or de10_standard)"
    }
}

# Default to the board wrapper so the generated project drives the physical
# ports, matching the committed .qsf. Physical PIN assignments still live only
# in the committed .qsf (USE_COMMITTED_QSF=1); a generated project reaches the
# board top but leaves pin placement to Quartus or to the committed settings.
set top_level [take_option --top-level [env_or_default TOP_LEVEL_ENTITY $board_top]]

# The board wrapper for the selected target plus the shared 7-seg decoder, so
# the generated project instantiates the physical top. Only THIS board's wrapper
# is added, never the other board's, to avoid pulling an unverified top into the
# project. Missing files are skipped (collect_sv_files handles a bare list too).
set board_dir [file join $quartus_dir "boards"]
set board_srcs {}
foreach leaf [list "$board_top.sv" "seg7_hex.sv"] {
    set candidate [file join $board_dir $leaf]
    if {[file exists $candidate]} {
        lappend board_srcs $candidate
    }
}

set sv_files [concat \
    [collect_sv_files [file join $template_root "src"]] \
    [collect_sv_files [file join $template_root "lib"]] \
    $board_srcs]
set sv_files [lsort -unique $sv_files]

if {[llength $sv_files] == 0} {
    fail "no SystemVerilog sources found under src/ or lib/"
}
if {![file exists $sdc_file]} {
    fail "missing board SDC: $sdc_file"
}

project_new -overwrite $project_name -revision $revision

set_global_assignment -name FAMILY $family
set_global_assignment -name DEVICE $device
set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files
set_global_assignment -name TOP_LEVEL_ENTITY $top_level
set_global_assignment -name SDC_FILE [file normalize $sdc_file]
set_global_assignment -name POST_FLOW_SCRIPT_FILE "quartus_sh:quartus/post_flow.tcl"
set_global_assignment -name SEARCH_PATH [file join $template_root "src"]
set_global_assignment -name SEARCH_PATH [file join $template_root "lib"]

foreach file $sv_files {
    set_global_assignment -name SYSTEMVERILOG_FILE [file normalize $file]
}

export_assignments
project_close

post_message "build_project: wrote $project_name/$revision for $board_label"
post_message "build_project: TARGET_BOARD=$target_board DEVICE=$device SDC=[file tail $sdc_file]"
post_message "build_project: verify the board-specific pin assignments against the official Terasic manual before compiling a production bitstream"
