# Operations

## Commands

Install the package in an isolated environment and use either `checksum-offload` or `python -m fpga_packet_checksum_offload`.

```bash
checksum-offload checksum 123456 --seed 0 --expected 0x9753
checksum-offload inspect FRAME_HEX --format markdown --output inspection.md
checksum-offload batch examples/frames.jsonl --output batch.json
checksum-offload campaign --format text
```

`checksum` accepts even-length hexadecimal bytes, an optional uncomplemented seed from 0 through 65535, and an optional expected checksum. `inspect` accepts one Ethernet frame. Direct `checksum` and `inspect` hexadecimal arguments are each limited to 65,535 decoded bytes. `batch` reads strict JSONL records containing exactly `name` and `frame_hex`. `campaign` executes the deterministic cycle-model cases.

## Exit codes

| Exit code | Meaning |
| ---: | --- |
| 0 | Operation completed and applicable validation passed |
| 1 | Report completed, but expected checksum, frame, batch, or campaign validation failed |
| 2 | Input, structure, argument, or I/O failure prevented a completed report |

Standard output contains only the selected report. Standard error contains concise diagnostics only. When `--output` is present, the process writes a UTF-8 sibling temporary file, flushes it, and atomically replaces the target; standard output remains empty. The implementation does not expose partial reports after an operational failure.

## Bounded batch defaults

The default batch reader allows at most 8 MiB of input, 256 KiB per physical line, 10,000 records, 65,535 decoded bytes per frame, and JSON nesting depth 4. These batch bounds are independent of the matching 65,535-byte direct-argument limit. It rejects nonregular files, duplicate or unknown fields, numeric JSON tokens, non-finite constants, invalid UTF-8, and non-hexadecimal frame data.

## Package checks

```bash
python -m build
python -m twine check dist/*
python -m pytest tests/test_package_contract.py -q
```

The package test inspects archive paths and contents, runs tests from an extracted source distribution in a path containing spaces, and installs the wheel into an isolated environment before invoking both entry points. No network is needed after build dependencies are installed.
