# Research Note

## Thesis

Checksum offload looks arithmetic-heavy, but the difficult engineering risk sits at boundaries: byte order, odd tails, pseudo-header ownership, frame lengths, fragmentation, ready/valid stalls, reset, and evidence that does not reuse the implementation as its oracle. This project makes those boundaries explicit and independently checkable.

## Tradeoffs

The protocol parser remains in software while the RTL accepts a general checksum byte domain plus seed. That keeps the datapath small, reusable, and synthesizable without embedding a changing parser policy. The cost is an integration requirement: upstream logic must select the exact checksum domain and compute metadata correctly.

A one-entry response register gives deterministic backpressure and permits zero-bubble replacement when the prior result retires. It is not a queue, so sustained downstream stalls stop new requests. The bounded formal depth 20 evidence is intentionally narrow; named mutation controls show sensitivity to odd-byte and stalled-state defects without presenting bounded exploration as an unbounded theorem.

## Evidence

Static packet fixtures, randomized software comparisons, an independent cycle model, poisoned-lane and boundary vectors, Verilator lint, Icarus simulation, Yosys synthesis, SymbiYosys properties, deterministic reports, and isolated package checks each address a different failure class. No benchmark is published because this repository has not performed device placement, routing, timing closure, or a controlled line-rate measurement.

## Extension path

Useful next layers are a separately verified parser-to-seed pipeline, IPv6 pseudo-header support, fragment-aware reassembly outside the core, checksum insertion, deeper buffering, constrained device builds, and measured throughput. Each extension should add an independent oracle and negative control before expanding public claims.
