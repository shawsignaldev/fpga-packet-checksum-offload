# Recruiter Brief

## Three-to-five-minute review path

1. Read the first two sections of [README.md](../README.md) for the system boundary and evidence table.
2. Inspect `rtl/checksum16_stream.sv` for the 64-bit ready/valid datapath, explicit framing statuses, backpressure stability, and zero-bubble response replacement.
3. Inspect `src/fpga_packet_checksum_offload/packet.py` and `cycle_model.py` to see protocol semantics separated from the independent hardware oracle.
4. Open `formal/checksum16_stream_formal.sv` and `formal/checksum16_stream.sby` for 22 assertions, ten reachable covers, positive depth 20 evidence, and two targeted mutation controls.
5. Review `.github/workflows/ci.yml` and `tests/test_package_contract.py` for Python 3.10-3.14, required Verilator/Icarus/Yosys/SymbiYosys gates, and isolated distribution checks.

## Concrete engineering signals

- Network-order one's-complement arithmetic handles carry folding, odd bytes, seeds, incremental chunking, pseudo-headers, and word replacement.
- Strict frame parsing validates offsets and lengths for Ethernet II, two VLAN layers, IPv4 options, UDP, TCP, and fragmented transport classification.
- The stream contract specifies accepted transfers, reset dominance, mask legality, output stability, length overflow, and clean error recovery.
- Deterministic fixtures, reports, vectors, synthesis, bounded formal reachability, and negative controls make results reproducible rather than assertion-only.
- Public limitations are explicit: no physical timing closure, device line-rate result, production deployment, IPv6, fragment reassembly, or checksum insertion claim.

The repository is most relevant to FPGA, low-latency systems, network datapath, verification, and hardware/software boundary roles.
