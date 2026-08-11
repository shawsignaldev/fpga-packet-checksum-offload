# Verification Methodology

## Independent layers

The project avoids one self-consistent implementation serving as its own oracle. Arithmetic tests compare against explicit byte-pair calculations and published checksum identities. Packet fixtures contain static complete frame bytes. The cycle model implements stream behavior without importing the arithmetic checksum API. Versioned vectors connect that model to an Icarus Verilog testbench. Yosys elaborates and synthesizes the RTL independently, while SymbiYosys explores bounded state transitions.

## Software matrix

Pytest covers arithmetic, parser structure, checksum classification, bounded input, deterministic rendering, atomic writes, cycle transitions, campaign coverage, CLI exits, and release archives. CI runs Python 3.10, 3.11, 3.12, 3.13, and 3.14. Ruff checks and formats the complete tracked Python surface.

## RTL gates

1. `python tools/generate_rtl_vectors.py --check` proves the TXT and SystemVerilog snapshots match the current independent generator byte for byte.
2. Verilator lints the RTL and monitor.
3. Icarus Verilog (`iverilog`) simulates complete cycle vectors, reset during stall, poisoned invalid lanes, exact length boundaries, zero-bubble replacement, and invalid parameter guards.
4. Yosys synthesizes a nonempty, latch-free hierarchy.
5. SymbiYosys runs positive and cover depth 20 tasks plus odd-padding and stalled-state mutations.

## Distribution gates

Build creates one wheel and one source distribution. Twine validates metadata and long-description rendering. Archive scans reject unsafe paths and caches. The source distribution includes tests, fixtures, vectors, RTL, formal files, tools, docs, examples, reports, and CI. Its tests run after extraction into a path containing spaces. The wheel contains only runtime package and metadata, installs without dependencies into an isolated environment, and runs both CLI entry points without source-tree import leakage.

## Evidence limits

Passing these checks supports the included semantics on the tested interpreters and tools. It does not establish unbounded formal correctness, physical timing closure, device-specific throughput, production reliability, IPv6 coverage, fragment reassembly, or checksum insertion. Those claims require separate implementation and evidence.
