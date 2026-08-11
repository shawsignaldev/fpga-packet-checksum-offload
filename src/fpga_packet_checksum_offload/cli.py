from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fpga_packet_checksum_offload.arithmetic import checksum_bytes
from fpga_packet_checksum_offload.campaign import CampaignResult, run_campaign
from fpga_packet_checksum_offload.packet import (
    ChecksumState,
    FrameInspection,
    PacketFormatError,
    inspect_ethernet_frame,
)
from fpga_packet_checksum_offload.reporting import (
    SCHEMA_VERSION,
    inspect_records,
    render_batch_json,
    render_batch_markdown,
    render_inspection_json,
    render_inspection_markdown,
    write_text_atomic,
)
from fpga_packet_checksum_offload.trace_io import TraceLimits, read_frame_batch

EXIT_SUCCESS = 0
EXIT_VALIDATION_FAILED = 1
EXIT_OPERATIONAL_ERROR = 2
MAX_DIRECT_BYTES = TraceLimits().max_frame_bytes

_INTEGER_PATTERN = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")
_HEX_PATTERN = re.compile(r"[0-9a-fA-F]*\Z")


class _ArgumentParsingError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentParsingError(message)


def _hex_bytes(value: str) -> bytes:
    if len(value) % 2:
        raise argparse.ArgumentTypeError(
            "hex input must contain an even number of hexadecimal digits"
        )
    if _HEX_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "hex input contains non-hexadecimal characters"
        )
    if len(value) // 2 > MAX_DIRECT_BYTES:
        raise argparse.ArgumentTypeError(
            f"hex input exceeds maximum decoded size ({MAX_DIRECT_BYTES} bytes)"
        )
    return bytes.fromhex(value)


def _uint16(value: str) -> int:
    if _INTEGER_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("value must be an integer between 0 and 65535")
    number = int(value[2:], 16) if value.lower().startswith("0x") else int(value, 10)
    if not 0 <= number <= 0xFFFF:
        raise argparse.ArgumentTypeError("value must be an integer between 0 and 65535")
    return number


def _add_format(parser: argparse.ArgumentParser, choices: tuple[str, ...]) -> None:
    parser.add_argument(
        "--format",
        choices=choices,
        default=choices[0],
        help=f"report format (default: {choices[0]})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="atomically write the report instead of printing it",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="checksum-offload",
        description="Checksum arithmetic, packet inspection, and RTL campaign tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    checksum = subparsers.add_parser(
        "checksum", help="calculate a 16-bit Internet checksum"
    )
    checksum.add_argument("data", type=_hex_bytes, help="even-length hexadecimal bytes")
    checksum.add_argument(
        "--seed", type=_uint16, default=0, help="uncomplemented 16-bit seed"
    )
    checksum.add_argument("--expected", type=_uint16, help="expected checksum value")
    _add_format(checksum, ("json", "text"))

    inspect = subparsers.add_parser(
        "inspect", help="inspect an Ethernet II frame and checksum state"
    )
    inspect.add_argument(
        "frame", type=_hex_bytes, help="Ethernet frame as hexadecimal bytes"
    )
    _add_format(inspect, ("json", "markdown"))

    batch = subparsers.add_parser(
        "batch", help="inspect a bounded JSONL file of named frames"
    )
    batch.add_argument("input", type=Path, help="strict JSONL input path")
    _add_format(batch, ("json", "markdown"))

    campaign = subparsers.add_parser(
        "campaign", help="run the deterministic cycle-model campaign"
    )
    _add_format(campaign, ("json", "text", "markdown"))
    return parser


def _json_document(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2) + "\n"


def _render_checksum(
    data: bytes,
    seed: int,
    expected: int | None,
    output_format: str,
) -> tuple[str, bool]:
    value = checksum_bytes(data, seed=seed)
    matches = None if expected is None else value == expected
    if output_format == "text":
        expected_text = "none" if expected is None else f"0x{expected:04x}"
        match_text = "none" if matches is None else str(matches).lower()
        report = (
            f"checksum=0x{value:04x} bytes={len(data)} seed=0x{seed:04x} "
            f"expected={expected_text} match={match_text}\n"
        )
    else:
        report = _json_document(
            {
                "schema_version": SCHEMA_VERSION,
                "operation": "checksum",
                "byte_length": len(data),
                "seed": seed,
                "checksum": value,
                "expected_checksum": expected,
                "matches_expected": matches,
            }
        )
    return report, matches is not False


def _campaign_document(result: CampaignResult) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign": "checksum-cycle-campaign-v1",
        "summary": {
            "total": result.total,
            "passed": result.passed,
            "failed": result.total - result.passed,
        },
        "covered_behaviors": list(result.covered_behavior_names),
        "cases": [
            {
                "name": case.name,
                "behaviors": list(case.behavior_names),
                "passed": case.passed,
                "detail": case.detail or None,
            }
            for case in result.cases
        ],
    }


def _render_campaign(result: CampaignResult, output_format: str) -> str:
    if output_format == "text":
        return result.summary + "\n"
    if output_format == "json":
        return _json_document(_campaign_document(result))

    lines = [
        "# Checksum Cycle Campaign",
        "",
        f"- Schema version: {SCHEMA_VERSION}",
        f"- Passed: {result.passed}/{result.total}",
        f"- Failed: {result.total - result.passed}",
        "",
        "| Case | Behaviors | Result | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for case in result.cases:
        behaviors = ", ".join(case.behavior_names)
        status = "pass" if case.passed else "fail"
        detail = case.detail.replace("|", "&#124;").replace("\n", " ") or "-"
        lines.append(f"| {case.name} | {behaviors} | {status} | {detail} |")
    return "\n".join(lines) + "\n"


def _emit(report: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(report)
        return
    try:
        write_text_atomic(output, report)
    except OSError:
        raise RuntimeError(f"cannot write output file: {output}") from None


def _diagnostic(error: Exception) -> str:
    return " ".join(str(error).splitlines()) or type(error).__name__


def _inspection_passes_policy(inspection: FrameInspection) -> bool:
    states = [inspection.checksum_state]
    if inspection.ipv4 is not None:
        states.append(inspection.ipv4.checksum.state)
    if inspection.transport is not None:
        states.append(inspection.transport.checksum.state)
    return ChecksumState.INVALID not in states


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _build_parser().parse_args(argv)
        if arguments.command == "checksum":
            report, valid = _render_checksum(
                arguments.data,
                arguments.seed,
                arguments.expected,
                arguments.format,
            )
        elif arguments.command == "inspect":
            inspection = inspect_ethernet_frame(arguments.frame)
            report = (
                render_inspection_json(inspection)
                if arguments.format == "json"
                else render_inspection_markdown(inspection)
            )
            valid = _inspection_passes_policy(inspection)
        elif arguments.command == "batch":
            outcomes = inspect_records(read_frame_batch(arguments.input))
            report = (
                render_batch_json(outcomes)
                if arguments.format == "json"
                else render_batch_markdown(outcomes)
            )
            valid = all(
                outcome.error is None
                and outcome.inspection is not None
                and _inspection_passes_policy(outcome.inspection)
                for outcome in outcomes
            )
        else:
            result = run_campaign()
            report = _render_campaign(result, arguments.format)
            valid = not result.failed_case_names
        _emit(report, arguments.output)
    except (PacketFormatError, ValueError, OSError, RuntimeError) as error:
        sys.stderr.write(f"error: {_diagnostic(error)}\n")
        return EXIT_OPERATIONAL_ERROR
    return EXIT_SUCCESS if valid else EXIT_VALIDATION_FAILED


def entrypoint() -> None:
    raise SystemExit(main())


__all__ = [
    "EXIT_OPERATIONAL_ERROR",
    "EXIT_SUCCESS",
    "EXIT_VALIDATION_FAILED",
    "MAX_DIRECT_BYTES",
    "entrypoint",
    "main",
]
