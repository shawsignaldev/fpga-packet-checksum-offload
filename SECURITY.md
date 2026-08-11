# Security Policy

## Supported version

Security fixes are applied to the current 1.x release line.

## Reporting

Report vulnerabilities privately to `shawsignaldev@proton.me`. Include the affected version, a minimal reproduction, impact, and any suggested mitigation. Do not include live credentials or sensitive packet captures.

## Security boundary

The CLI treats frame and JSONL input as untrusted. The reader bounds total bytes, line bytes, record count, decoded frame size, and JSON nesting; rejects duplicate or unknown fields, non-finite values, integers, invalid UTF-8, and malformed hexadecimal data; and opens only regular files. Reports escape untrusted Markdown content and atomic writes avoid exposing partial files.

The RTL is a reference checksum accumulator, not a complete network security boundary. It does not authenticate traffic, reassemble fragments, parse IPv6, insert checksums, manage DMA, or protect a host interface. Integration must add those controls and must validate timing closure on the selected device.
