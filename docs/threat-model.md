# Threat Model

## Assets and trust boundaries

The protected assets are deterministic inspection results, resource availability during batch parsing, intact output files, stable stream state under backpressure, and reproducible verification evidence. Frame bytes, JSONL records, command arguments, paths, and downstream ready signals are untrusted.

## Software controls

- Descriptor-first regular-file checks reduce path metadata races and reject pipes or devices.
- Total bytes, physical line length, record count, frame length, and JSON nesting are bounded before expensive work.
- Direct `checksum` and `inspect` hexadecimal arguments are limited to 65,535 decoded bytes. The batch reader separately limits input to 8 MiB, each physical line to 256 KiB, the trace to 10,000 records, and each decoded frame to 65,535 bytes.
- Duplicate keys, unknown keys, numbers, non-finite constants, malformed UTF-8, malformed hex, and structural packet errors fail closed.
- JSON serialization rejects non-finite values; Markdown rendering encodes punctuation from untrusted names and diagnostics.
- Atomic replacement and file synchronization reduce partial-output exposure after interruption.
- CLI operational errors return exit code `2`, one concise line on standard error, and no report bytes.

## Hardware controls

The ready/valid contract rejects missing or repeated packet starts, noncontiguous `keep`, empty final beats, and length overflow. Error results terminate packet state. Reset clears packet and response state. Stalled responses remain stable, and formal controls check the bounded transition relation to depth 20.

## Residual risks

The parser is not an intrusion-detection system. It does not authenticate inputs, reassemble fragments, parse IPv6, enforce application policy, or defend an external DMA/MAC interface. The RTL has no clock-domain crossing, ECC, redundancy, host isolation, or checksum insertion. Formal depth 20 is not an unbounded guarantee, synthesis is not timing closure, and no device-specific line-rate or production deployment claim is made.
