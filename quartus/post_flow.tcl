# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
# Post-flow script for Quartus Prime. Registered with:
#
#   set_global_assignment -name POST_FLOW_SCRIPT_FILE "quartus_sh:quartus/post_flow.tcl"
#
# It runs automatically when Compile Design finishes, copies the flow report to
# output_files/ and writes the provenance sidecar the CI gate requires.
#
# Design rule: this script computes no hashes itself. Every content hash is a
# `git hash-object` blob id produced by git, exactly as CI will produce it. That
# keeps the two sides identical by construction and keeps crypto out of TCL.
#
# Inspired by rgarciatec/ce3201's post_flow.tcl, extended with the provenance
# record. See docs/fase0/observacion-repos-curso.md.

package require ::quartus::flow

# --- Configuration ------------------------------------------------------------

set project_dir    [pwd]
set source_globs   {src lib quartus}

# --- Helpers ------------------------------------------------------------------

# Run git and return trimmed stdout. Sets ok=0 on genuine failure.
#
# Two Tcl hazards are handled here, and both are the difference between a gate
# that works and one that fails open:
#
#  1. `exec` raises an error whenever the child writes to *stderr*, even when it
#     exits 0 -- verified: `exec sh -c {echo note >&2; echo out}` raises despite
#     status 0. Git writes advisories there (e.g. line-ending conversion or
#     "detected dubious ownership" notices; plausible triggers, not ones observed
#     here). Redirecting stderr into stdout (2>@1) stops any such advisory from
#     being mistaken for a failure.
#  2. Returning "" on error is indistinguishable from a legitimately empty
#     result. `git status --porcelain` returning "" means *clean*, so swallowing
#     an error there would assert a clean tree on any git hiccup. The caller is
#     therefore handed an explicit ok flag and must decide.
proc git_try {repo okVar args} {
    upvar 1 $okVar ok
    set ok 1
    if {[catch {eval exec git -C [list $repo] $args 2>@1} result options]} {
        set code [dict get $options -errorcode]
        # CHILDSTATUS means git ran and exited non-zero: a real failure.
        # Anything else here is Tcl noise around a successful run.
        if {[lindex $code 0] eq "CHILDSTATUS"} {
            set ok 0
            return $result
        }
    }
    return [string trim $result]
}

# Convenience wrapper for commands whose failure must abort the script.
proc git_must {repo args} {
    set out [git_try $repo ok {*}$args]
    if {!$ok} {
        error "git $args failed: $out"
    }
    return $out
}

proc json_escape {s} {
    string map {\\ \\\\ \" \\\" \n \\n \r \\r \t \\t} $s
}

# --- Resolve the repository root ----------------------------------------------
# Ask git rather than guessing [pwd]/.., so the script keeps working if the
# Quartus project is moved. The gate resolves paths from the root too, and the
# two must agree or every source will look absent.

if {[catch {git_must $project_dir rev-parse --show-toplevel} repo_root]} {
    post_message -type error "post_flow: not a git repository; cannot emit provenance"
    return
}
set repo_root  [file normalize [string trim $repo_root]]
set output_dir [file join $repo_root "output_files"]

# --- Locate the flow report ---------------------------------------------------
# Order matters. The freshly written report is what Quartus just produced in the
# project directory; output_files/ holds the *archived copy from the previous
# compile*, which is tracked in git. Searching the archive first would let the
# script vouch for a report it did not produce -- run the script without
# compiling and the stale report gets bound to the current sources.

set revision [get_current_revision]
set candidates [list \
    "$revision.flow.rpt" \
    [file join "output_files" "$revision.flow.rpt"] \
    [file join $output_dir "$revision.flow.rpt"]]

set report_src ""
foreach candidate $candidates {
    if {[file exists $candidate]} {
        set report_src $candidate
        break
    }
}

if {$report_src eq ""} {
    post_message -type error "post_flow: no flow report found for revision $revision"
    return
}

# Refuse to vouch for a report that predates this run. Without this, invoking
# quartus_sh -t on this script alone -- no compile -- produces a sidecar that
# binds last week's report to today's sources, and every gate check passes.
set report_mtime [file mtime $report_src]
if {[info exists ::env(POST_FLOW_MAX_REPORT_AGE_S)]} {
    set max_age $::env(POST_FLOW_MAX_REPORT_AGE_S)
} else {
    set max_age 3600
}
set report_age [expr {[clock seconds] - $report_mtime}]
if {$report_age > $max_age} {
    post_message -type error \
        "post_flow: $report_src is ${report_age}s old (limit ${max_age}s); it was \
         not produced by this compile. Run Compile Design before generating \
         provenance."
    return
}

file mkdir $output_dir
set report_dst [file join $output_dir "$revision.flow.rpt"]
if {[file normalize $report_src] ne [file normalize $report_dst]} {
    file copy -force $report_src $report_dst
}
post_message "post_flow: report at $report_dst"

# --- Collect provenance -------------------------------------------------------

if {[catch {git_must $repo_root rev-parse HEAD} commit]} {
    post_message -type error "post_flow: cannot resolve HEAD: $commit"
    return
}

# A tree with uncommitted tracked changes cannot be attributed to a revision.
# Record the fact rather than hiding it; CI decides what to do with it.
#
# Scoped to the *design sources*, deliberately. A whole-tree probe would report
# output_files/ as modified -- this script has just overwritten the tracked
# report there -- so every compile from the second one onwards would be flagged
# dirty and rejected on a pristine checkout.
set status_args [list status --porcelain --untracked-files=no --]
foreach spec $source_globs {
    lappend status_args $spec
}
set porcelain [git_try $repo_root ok {*}$status_args]
if {!$ok} {
    # Never assume clean on failure: that is the fail-open direction.
    post_message -type error \
        "post_flow: cannot determine working-tree state: $porcelain"
    return
}
set dirty [expr {$porcelain eq "" ? "false" : "true"}]
if {$dirty eq "true"} {
    post_message -type warning \
        "post_flow: design sources have uncommitted changes; CI will reject this artifact"
}

# Every tracked design source, hashed as it currently sits on disk. Paths are
# root-relative (--full-name) so they match what the gate resolves.
set sources {}
foreach spec $source_globs {
    set listing [git_try $repo_root ok ls-tree -r --full-name --name-only HEAD -- $spec]
    if {!$ok} {
        post_message -type error "post_flow: cannot list sources under $spec: $listing"
        return
    }
    foreach path [split $listing "\n"] {
        set path [string trim $path]
        if {$path eq ""} continue
        set abs [file join $repo_root $path]
        if {![file exists $abs]} {
            post_message -type error \
                "post_flow: $path is tracked but missing on disk; refusing to emit \
                 an incomplete provenance record"
            return
        }
        set blob [git_try $repo_root ok hash-object -- $abs]
        if {!$ok || $blob eq ""} {
            # Dropping a source silently is precisely how a changed file would
            # be hidden from the gate. Abort instead.
            post_message -type error "post_flow: cannot hash $path: $blob"
            return
        }
        lappend sources [list $path $blob]
    }
}

if {[llength $sources] == 0} {
    post_message -type error "post_flow: no tracked sources found under $source_globs"
    return
}

set report_rel [string range [file normalize $report_dst] \
    [expr {[string length [file normalize $repo_root]] + 1}] end]
set report_blob [git_try $repo_root ok hash-object -- $report_dst]
if {!$ok || $report_blob eq ""} {
    post_message -type error "post_flow: cannot hash the report: $report_blob"
    return
}

set quartus_version [string trim [regsub -all {\s+} $::quartus(version) " "]]
set device [get_global_assignment -name DEVICE]
set family [get_global_assignment -name FAMILY]
set generated ""
if {[catch {clock format [clock seconds] -format "%Y-%m-%dT%H:%M:%SZ" -gmt 1} generated]} {
    set generated ""
}

# --- Emit the sidecar ---------------------------------------------------------

set sidecar "$report_dst.provenance.json"
set fh [open $sidecar w]
puts $fh "{"
puts $fh "  \"schema\": 1,"
puts $fh "  \"commit\": \"[json_escape $commit]\","
puts $fh "  \"dirty\": $dirty,"
puts $fh "  \"generated_at_utc\": \"[json_escape $generated]\","
puts $fh "  \"sources\": {"
set n [llength $sources]
for {set i 0} {$i < $n} {incr i} {
    lassign [lindex $sources $i] path blob
    set comma [expr {$i < $n - 1 ? "," : ""}]
    puts $fh "    \"[json_escape $path]\": \"[json_escape $blob]\"$comma"
}
puts $fh "  },"
puts $fh "  \"report\": {"
puts $fh "    \"path\": \"[json_escape $report_rel]\","
puts $fh "    \"blob\": \"[json_escape $report_blob]\""
puts $fh "  },"
puts $fh "  \"tool\": {"
puts $fh "    \"quartus_version\": \"[json_escape $quartus_version]\","
puts $fh "    \"device\": \"[json_escape $device]\","
puts $fh "    \"family\": \"[json_escape $family]\","
puts $fh "    \"revision\": \"[json_escape $revision]\""
puts $fh "  }"
puts $fh "}"
close $fh

post_message "post_flow: provenance written to $sidecar ($n source file(s), commit [string range $commit 0 11])"
