# Packet Checksum Offload Design Specification

## Objective

Build a reproducible checksum-offload reference that connects protocol-aware software semantics to a synthesizable streaming datapath. The implementation must detect malformed input, preserve packet boundaries under backpressure, cover IPv4 and IPv4 UDP/TCP checksum rules, and publish evidence that distinguishes the reference design from known-bad mutations.

## Supported Protocol Boundary

The software packet model accepts Ethernet II frames with zero, one, or two IEEE 802.1Q tags. It parses IPv4 headers with valid internet-header lengths, rejects truncated and structurally invalid headers, and classifies UDP and TCP payloads. IPv6, IP reassembly, tunnel decapsulation, and checksum insertion into an outgoing frame are explicit future integration boundaries.

IPv4 header checksum verification covers the complete header including options. UDP and TCP verification includes the IPv4 pseudo-header. An IPv4 UDP checksum field of zero is reported as disabled rather than valid or invalid. Fragmented transport payloads are classified as incomplete because a single frame does not contain the complete transport checksum domain.

## Arithmetic Contract

The checksum core implements 16-bit one's-complement addition in network byte order. An odd final byte is padded in the low-order byte position with zero. Accumulation accepts an uncomplemented 16-bit seed so a transport pseudo-header can be reduced separately and supplied to the streaming datapath.

The public software API includes whole-buffer checksum calculation, residue verification, a stateful accumulator, IPv4 pseudo-header seeding, and RFC 1624 incremental replacement for a changed 16-bit word. Inputs are range-checked and immutable result records carry both calculated values and validation state.

## Streaming Hardware Contract

`checksum16_stream` uses a 64-bit ready/valid request channel with eight byte-valid bits, `first`, `last`, and a 16-bit seed. Bytes are consumed in ascending lane order. Every non-final beat must contain all eight bytes; the final beat must contain a nonzero contiguous mask from lane zero. A single-beat packet asserts both `first` and `last`.

The engine owns one output register containing checksum, folded sum, byte length, and status. Output remains stable during backpressure. A result may retire in the same cycle a new packet begins. Protocol errors terminate the current packet with a result but never contaminate the next packet. Reset clears packet and response state.

Status values distinguish success, missing first, unexpected first, invalid keep, empty final beat, and length overflow. The engine does not parse Ethernet or IP fields; software and integration logic provide the checksum domain and optional pseudo-header seed.

## Model and Evidence Flow

The Python packet parser produces protocol-level inspection records. A separate cycle model implements the hardware handshake contract and emits deterministic simulation vectors without calling the RTL. The campaign includes even and odd lengths, carry folding, seeded transport domains, stalls, zero-bubble replacement, malformed masks, empty final beats, reset recovery, and maximum-width length behavior.

Reports are deterministic, schema-versioned JSON and Markdown. The CLI supports raw byte checksum calculation, Ethernet-frame inspection, deterministic campaign execution, and bounded JSONL batch input. Operational failures and completed validation failures use distinct exit codes.

## Verification Strategy

Python tests cover published RFC arithmetic examples, differential comparison with an independent byte-pair oracle, randomized chunking invariance, incremental update equivalence, packet parsing, malformed inputs, bounded readers, reports, CLI exits, and package contents.

Hardware verification includes Verilator lint, Icarus vector simulation, explicit backpressure and zero-bubble tests, invalid-parameter simulation, Yosys synthesis, and bounded formal assertions. The formal suite runs a bounded depth-20 formal proof for state stability under stalls, packet-state transitions, output stability, reset recovery, and valid result ranges. A separate cover depth 20 run requires witnesses for successful completion, every error status, multibeat completion, response stall, zero-bubble replacement, and recurrent reset. Two intentional mutations, odd-byte padding removal and stalled-state mutation, must each yield a counterexample.

Distribution verification builds source and wheel artifacts, checks metadata, executes tests from the extracted source archive, and runs the installed command from an isolated wheel environment. CI spans every declared Python version.

## Evidence Boundary

The project proves arithmetic, parsing, and bounded streaming-control properties for the included reference implementation. It does not claim an unbounded formal proof, physical timing closure, line-rate performance on a specific device, PCIe or MAC integration, production network deployment, or complete protocol coverage.
