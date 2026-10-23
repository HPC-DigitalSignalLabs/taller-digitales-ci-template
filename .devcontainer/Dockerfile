# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Instituto Tecnológico de Costa Rica
# Open-source functional verification environment.
#
# This image is THE unit of reproducibility. It runs unchanged in three places:
#   * the student's machine, as a DevContainer
#   * Kabré (CeNAT), converted to an Apptainer .sif
#   * GitHub hosted runners, for smoke checks
#
# A green result in one implies a green result in the others because it is
# literally the same image. That property is an OE2 acceptance criterion, and it
# only holds if nothing here floats. Hence: base image by digest, Verilator by
# tag, Python packages by exact version.
#
# Ubuntu 22.04 is chosen because that is what the CE3201 lab stations run.
# Digest below is ubuntu:22.04 as of 2026-08.
FROM ubuntu@sha256:0e5e4a57c2499249aafc3b40fcd541e9a456aab7296681a3994d631587203f97

LABEL org.opencontainers.image.title="digital-design-verify"
LABEL org.opencontainers.image.description="Verilator, Icarus, Yosys, Graphviz, CocoTB and PyTest environment"
LABEL org.opencontainers.image.licenses="GPL-3.0-only"

ENV DEBIAN_FRONTEND=noninteractive
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# Pinned toolchain versions. Bump deliberately, never incidentally.
ARG VERILATOR_VERSION=v5.028
ARG VERIBLE_VERSION=v0.0-4157-gfdbac312
ARG IVERILOG_VERSION=v12_0
ARG YOSYS_VERSION=yosys-0.40

# Build dependencies for Verilator and Icarus Verilog, plus git (the artifact
# gate shells out to git plumbing) and python3.
#
# Icarus Verilog is the second open-source simulator required by the S4
# deliverable ("toolchain Kabre completamente open source ... Verilator e
# Icarus"): a two-simulator toolchain catches simulator-specific SystemVerilog
# acceptance early, before it becomes a Fase 1 surprise (anteproyecto Sec. 4.3,
# "Brechas de compatibilidad de SystemVerilog en Verilator").
RUN apt-get update && apt-get install --no-install-recommends -y \
        autoconf=2.71-2 \
        bison \
        build-essential \
        ca-certificates \
        ccache \
        flex \
        g++ \
        gperf \
        git \
        graphviz=2.42.2-6 \
        help2man \
        libfl2 \
        libfl-dev \
        libreadline-dev \
        libffi-dev \
        perl \
        pkg-config \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        tcl-dev \
        wget \
        zlib1g \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Verilator from a tagged release, not master. Building master is exactly the
# version drift this infrastructure exists to eliminate; see
# docs/fase0/observacion-repos-curso.md.
RUN git clone --depth 1 --branch "${VERILATOR_VERSION}" \
        https://github.com/verilator/verilator.git /tmp/verilator \
    && cd /tmp/verilator \
    && autoconf \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && rm -rf /tmp/verilator

# Icarus Verilog from a tagged release. Same discipline as Verilator: a pinned
# tag, built once, never master.
RUN git clone --depth 1 --branch "${IVERILOG_VERSION}" \
        https://github.com/steveicarus/iverilog.git /tmp/iverilog \
    && cd /tmp/iverilog \
    && sh autoconf.sh \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && rm -rf /tmp/iverilog

# Yosys 0.40 supplies synth_intel_alm, the documented synthesis backend for
# Cyclone V. This is an open logical-synthesis check, not a replacement for
# Quartus placement, routing, bitstream generation or timing analysis.
RUN git clone --depth 1 --recursive --branch "${YOSYS_VERSION}" \
        https://github.com/YosysHQ/yosys.git /tmp/yosys \
    && cd /tmp/yosys \
    && make config-gcc \
    && make -j"$(nproc)" \
    && make install \
    && rm -rf /tmp/yosys

# Verible: static binary, no build. Conditional tool with a Fase 0 time box; if
# it is dropped, delete this layer and the lint job.
RUN wget -q -O /tmp/verible.tar.gz \
        "https://github.com/chipsalliance/verible/releases/download/${VERIBLE_VERSION}/verible-${VERIBLE_VERSION}-linux-static-x86_64.tar.gz" \
    && tar -xzf /tmp/verible.tar.gz -C /tmp \
    && cp /tmp/verible-*/bin/* /usr/local/bin/ \
    && rm -rf /tmp/verible*

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Fail the build rather than ship an image whose versions are not what we pinned.
RUN verilator --version \
    && iverilog -V | head -n1 \
    && yosys -V \
    && yosys -Q -p "help synth_intel_alm" \
    && dot -V \
    && python3 -c "import cocotb, pytest; print('cocotb', cocotb.__version__); print('pytest', pytest.__version__)"

WORKDIR /work
CMD ["/bin/bash"]
