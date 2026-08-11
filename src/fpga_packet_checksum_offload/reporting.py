from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fpga_packet_checksum_offload.packet import (
    ChecksumField,
    FrameInspection,
    PacketFormatError,
    inspect_ethernet_frame,
)
from fpga_packet_checksum_offload.trace_io import FrameRecord

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    name: str
    frame_length: int
    inspection: FrameInspection | None
    error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("name must be a string")
        if isinstance(self.frame_length, bool) or not isinstance(
            self.frame_length, int
        ):
            raise TypeError("frame_length must be an integer")
        if self.frame_length < 0:
            raise ValueError("frame_length must be nonnegative")
        if self.inspection is not None and not isinstance(
            self.inspection, FrameInspection
        ):
            raise TypeError("inspection must be a FrameInspection")
        if self.error is not None and not isinstance(self.error, str):
            raise TypeError("error must be a string")
        if (self.inspection is None) == (self.error is None):
            raise ValueError("exactly one of inspection or error must be set")


def inspect_records(records: Iterable[FrameRecord]) -> tuple[BatchOutcome, ...]:
    """Inspect each frame independently while retaining packet format failures."""

    outcomes: list[BatchOutcome] = []
    for record in records:
        try:
            inspection = inspect_ethernet_frame(record.frame)
        except PacketFormatError as error:
            outcomes.append(
                BatchOutcome(
                    name=record.name,
                    frame_length=len(record.frame),
                    inspection=None,
                    error=str(error),
                )
            )
        else:
            outcomes.append(
                BatchOutcome(
                    name=record.name,
                    frame_length=len(record.frame),
                    inspection=inspection,
                    error=None,
                )
            )
    return tuple(outcomes)


def _mac_address(value: bytes) -> str:
    return ":".join(f"{byte:02x}" for byte in value)


def _checksum_to_dict(checksum: ChecksumField) -> dict[str, Any]:
    return {
        "value": checksum.value,
        "calculated": checksum.calculated,
        "state": checksum.state.value,
        "offset": checksum.offset,
    }


def _require_inspection(value: Any) -> FrameInspection:
    if not isinstance(value, FrameInspection):
        raise TypeError("inspection must be a FrameInspection")
    return value


def inspection_to_dict(inspection: FrameInspection) -> dict[str, Any]:
    """Convert a packet inspection into deterministic JSON-compatible data."""

    inspection = _require_inspection(inspection)
    ipv4 = inspection.ipv4
    transport = inspection.transport
    return {
        "destination_mac": _mac_address(inspection.destination_mac),
        "source_mac": _mac_address(inspection.source_mac),
        "outer_ethertype": inspection.outer_ethertype,
        "payload_ethertype": inspection.payload_ethertype,
        "vlan_tags": [
            {
                "tpid": tag.tpid,
                "pcp": tag.pcp,
                "dei": tag.dei,
                "vid": tag.vid,
            }
            for tag in inspection.vlan_tags
        ],
        "ipv4": (
            None
            if ipv4 is None
            else {
                "offset": ipv4.offset,
                "source": str(ipv4.source),
                "destination": str(ipv4.destination),
                "dscp": ipv4.dscp,
                "ecn": ipv4.ecn,
                "identification": ipv4.identification,
                "ttl": ipv4.ttl,
                "protocol": ipv4.protocol,
                "flags": ipv4.flags,
                "fragment_offset": ipv4.fragment_offset,
                "more_fragments": ipv4.more_fragments,
                "header_length": ipv4.header_length,
                "total_length": ipv4.total_length,
                "payload_length": ipv4.payload_length,
                "checksum": _checksum_to_dict(ipv4.checksum),
            }
        ),
        "transport": (
            None
            if transport is None
            else {
                "protocol": transport.protocol,
                "offset": transport.offset,
                "source_port": transport.source_port,
                "destination_port": transport.destination_port,
                "header_length": transport.header_length,
                "length": transport.length,
                "payload_length": transport.payload_length,
                "checksum": _checksum_to_dict(transport.checksum),
            }
        ),
        "checksum_state": inspection.checksum_state.value,
    }


def _json_document(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2) + "\n"


def render_inspection_json(inspection: FrameInspection) -> str:
    return _json_document(
        {
            "schema_version": SCHEMA_VERSION,
            "inspection": inspection_to_dict(inspection),
        }
    )


def _display(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _markdown_inert(value: str) -> str:
    flattened = " ".join(value.splitlines()).replace("\t", " ")
    return "".join(
        character
        if character.isalnum() or character in " :"
        else f"&#{ord(character)};"
        for character in flattened
    )


def _field_table(rows: Iterable[tuple[str, Any]]) -> list[str]:
    lines = ["| Field | Value |", "| --- | --- |"]
    lines.extend(
        f"| {_markdown_inert(name)} | {_markdown_inert(_display(value))} |"
        for name, value in rows
    )
    return lines


def _checksum_rows(prefix: str, checksum: ChecksumField) -> list[tuple[str, Any]]:
    return [
        (f"{prefix} checksum value", checksum.value),
        (f"{prefix} checksum calculated", checksum.calculated),
        (f"{prefix} checksum state", checksum.state.value),
        (f"{prefix} checksum offset", checksum.offset),
    ]


def render_inspection_markdown(inspection: FrameInspection) -> str:
    inspection = _require_inspection(inspection)
    lines = [
        "# Frame Inspection Report",
        "",
        f"- Schema version: {SCHEMA_VERSION}",
        f"- Checksum state: {_markdown_inert(inspection.checksum_state.value)}",
        "",
        "## Ethernet",
        "",
    ]
    lines.extend(
        _field_table(
            [
                ("Destination MAC", _mac_address(inspection.destination_mac)),
                ("Source MAC", _mac_address(inspection.source_mac)),
                ("Outer EtherType", inspection.outer_ethertype),
                ("Payload EtherType", inspection.payload_ethertype),
            ]
        )
    )

    lines.extend(["", "## VLAN Tags", ""])
    if inspection.vlan_tags:
        lines.extend(
            [
                "| Index | TPID | PCP | DEI | VID |",
                "| ---: | ---: | ---: | --- | ---: |",
            ]
        )
        lines.extend(
            "| "
            + " | ".join(
                _markdown_inert(_display(value))
                for value in (index, tag.tpid, tag.pcp, tag.dei, tag.vid)
            )
            + " |"
            for index, tag in enumerate(inspection.vlan_tags, start=1)
        )
    else:
        lines.append("_No VLAN tags._")

    lines.extend(["", "## IPv4", ""])
    if inspection.ipv4 is None:
        lines.append("_Not present._")
    else:
        ipv4 = inspection.ipv4
        ipv4_rows: list[tuple[str, Any]] = [
            ("Offset", ipv4.offset),
            ("Source", ipv4.source),
            ("Destination", ipv4.destination),
            ("DSCP", ipv4.dscp),
            ("ECN", ipv4.ecn),
            ("Identification", ipv4.identification),
            ("TTL", ipv4.ttl),
            ("Protocol", ipv4.protocol),
            ("Flags", ipv4.flags),
            ("Fragment offset", ipv4.fragment_offset),
            ("More fragments", ipv4.more_fragments),
            ("Header length", ipv4.header_length),
            ("Total length", ipv4.total_length),
            ("Payload length", ipv4.payload_length),
        ]
        ipv4_rows.extend(_checksum_rows("IPv4", ipv4.checksum))
        lines.extend(_field_table(ipv4_rows))

    lines.extend(["", "## Transport", ""])
    if inspection.transport is None:
        lines.append("_Not present._")
    else:
        transport = inspection.transport
        transport_rows: list[tuple[str, Any]] = [
            ("Protocol", transport.protocol),
            ("Offset", transport.offset),
            ("Source port", transport.source_port),
            ("Destination port", transport.destination_port),
            ("Header length", transport.header_length),
            ("Length", transport.length),
            ("Payload length", transport.payload_length),
        ]
        transport_rows.extend(_checksum_rows("Transport", transport.checksum))
        lines.extend(_field_table(transport_rows))

    return "\n".join(lines) + "\n"


def _outcome_to_dict(outcome: BatchOutcome) -> dict[str, Any]:
    if outcome.inspection is not None:
        return {
            "name": outcome.name,
            "frame_length": outcome.frame_length,
            "status": "success",
            "inspection": inspection_to_dict(outcome.inspection),
        }
    return {
        "name": outcome.name,
        "frame_length": outcome.frame_length,
        "status": "error",
        "error": outcome.error,
    }


def _batch_counts(outcomes: tuple[BatchOutcome, ...]) -> tuple[int, int]:
    success = sum(outcome.inspection is not None for outcome in outcomes)
    return success, len(outcomes) - success


def render_batch_json(outcomes: Iterable[BatchOutcome]) -> str:
    ordered = tuple(outcomes)
    success, error = _batch_counts(ordered)
    return _json_document(
        {
            "schema_version": SCHEMA_VERSION,
            "summary": {
                "total": len(ordered),
                "success": success,
                "error": error,
            },
            "outcomes": [_outcome_to_dict(outcome) for outcome in ordered],
        }
    )


def _success_detail(inspection: FrameInspection) -> str:
    if inspection.transport is not None:
        transport = inspection.transport
        if inspection.ipv4 is None:
            return transport.protocol
        return (
            f"{transport.protocol} "
            f"{inspection.ipv4.source}:{_display(transport.source_port)} -> "
            f"{inspection.ipv4.destination}:"
            f"{_display(transport.destination_port)}"
        )
    if inspection.ipv4 is not None:
        return f"IPv4 protocol {inspection.ipv4.protocol}"
    return f"EtherType {inspection.payload_ethertype}"


def render_batch_markdown(outcomes: Iterable[BatchOutcome]) -> str:
    ordered = tuple(outcomes)
    success, error = _batch_counts(ordered)
    lines = [
        "# Batch Inspection Report",
        "",
        f"- Schema version: {SCHEMA_VERSION}",
        f"- Total: {len(ordered)}",
        f"- Success: {success}",
        f"- Errors: {error}",
        "",
    ]
    if not ordered:
        lines.append("_No records._")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Name | Frame bytes | Status | Checksum | Details |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for outcome in ordered:
        name = _markdown_inert(outcome.name)
        frame_length = _markdown_inert(_display(outcome.frame_length))
        if outcome.inspection is None:
            details = _markdown_inert(outcome.error or "")
            lines.append(f"| {name} | {frame_length} | error | - | {details} |")
        else:
            inspection = outcome.inspection
            lines.append(
                f"| {name} | {frame_length} | success | "
                f"{_markdown_inert(inspection.checksum_state.value)} | "
                f"{_markdown_inert(_success_detail(inspection))} |"
            )
    return "\n".join(lines) + "\n"


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        with suppress(OSError):
            os.close(descriptor)


def write_text_atomic(path: str | Path, text: str) -> None:
    """Replace a text file using a UTF-8 sibling temporary file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        _fsync_directory(target.parent)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()


__all__ = [
    "SCHEMA_VERSION",
    "BatchOutcome",
    "inspect_records",
    "inspection_to_dict",
    "render_batch_json",
    "render_batch_markdown",
    "render_inspection_json",
    "render_inspection_markdown",
    "write_text_atomic",
]
