from __future__ import annotations

import json
import os
import stat
import string
import sys
from contextlib import suppress
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TraceLimits:
    max_bytes: int = 8 * 1024 * 1024
    max_line_bytes: int = 256 * 1024
    max_records: int = 10_000
    max_frame_bytes: int = 65_535
    max_nesting: int = 4

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field.name} must be a positive integer")
        if self.max_line_bytes >= sys.maxsize:
            raise ValueError("max_line_bytes must be less than sys.maxsize")


@dataclass(frozen=True, slots=True)
class FrameRecord:
    line_number: int
    name: str
    frame: bytes

    def __post_init__(self) -> None:
        if isinstance(self.line_number, bool) or not isinstance(self.line_number, int):
            raise TypeError("line_number must be an integer")
        if self.line_number <= 0:
            raise ValueError("line_number must be positive")
        if not isinstance(self.name, str):
            raise TypeError("name must be a string")
        if not isinstance(self.frame, bytes):
            raise TypeError("frame must be bytes")


class _DuplicateKeyError(ValueError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class _FloatingPointError(ValueError):
    pass


class _IntegerError(ValueError):
    pass


class _NonFiniteError(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_float(value: str) -> Any:
    raise _FloatingPointError(value)


def _reject_integer(value: str) -> Any:
    raise _IntegerError(value)


def _reject_constant(value: str) -> Any:
    raise _NonFiniteError(value)


def _exceeds_nesting_limit(value: Any, limit: int) -> bool:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if not isinstance(current, (dict, list)):
            continue
        if depth > limit:
            return True
        children = current.values() if isinstance(current, dict) else current
        pending.extend((child, depth + 1) for child in children)
    return False


def _field_error(kind: str, names: set[str]) -> str:
    ordered = ", ".join(
        json.dumps(name, ensure_ascii=True)[1:-1] for name in sorted(names)
    )
    suffix = "field" if len(names) == 1 else "fields"
    return f"{kind} {suffix}: {ordered}"


def _parse_record(text: str, line_number: int, limits: TraceLimits) -> FrameRecord:
    prefix = f"line {line_number}: "
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_float=_reject_float,
            parse_int=_reject_integer,
            parse_constant=_reject_constant,
        )
    except _DuplicateKeyError as error:
        raise ValueError(f"{prefix}duplicate key {error.key!r}") from None
    except _FloatingPointError:
        raise ValueError(f"{prefix}floating-point values are not permitted") from None
    except _IntegerError:
        raise ValueError(f"{prefix}integer values are not permitted") from None
    except _NonFiniteError:
        raise ValueError(f"{prefix}non-finite values are not permitted") from None
    except RecursionError:
        raise ValueError(
            f"{prefix}JSON nesting exceeds max_nesting ({limits.max_nesting})"
        ) from None
    except (json.JSONDecodeError, ValueError):
        raise ValueError(f"{prefix}invalid JSON") from None

    if _exceeds_nesting_limit(value, limits.max_nesting):
        raise ValueError(
            f"{prefix}JSON nesting exceeds max_nesting ({limits.max_nesting})"
        )
    if not isinstance(value, dict):
        raise ValueError(f"{prefix}record must be a JSON object")

    expected_fields = {"name", "frame_hex"}
    actual_fields = set(value)
    missing = expected_fields - actual_fields
    if missing:
        raise ValueError(f"{prefix}{_field_error('missing required', missing)}")
    unknown = actual_fields - expected_fields
    if unknown:
        raise ValueError(f"{prefix}{_field_error('unknown', unknown)}")

    name = value["name"]
    frame_hex = value["frame_hex"]
    if not isinstance(name, str):
        raise ValueError(f"{prefix}name must be a string")
    if not isinstance(frame_hex, str):
        raise ValueError(f"{prefix}frame_hex must be a string")
    if any(character not in string.hexdigits for character in frame_hex):
        raise ValueError(f"{prefix}frame_hex contains non-hexadecimal characters")
    if len(frame_hex) % 2:
        raise ValueError(
            f"{prefix}frame_hex must contain an even number of hexadecimal digits"
        )

    frame_length = len(frame_hex) // 2
    if frame_length > limits.max_frame_bytes:
        raise ValueError(
            f"{prefix}frame exceeds max_frame_bytes ({limits.max_frame_bytes})"
        )

    return FrameRecord(
        line_number=line_number,
        name=name,
        frame=bytes.fromhex(frame_hex),
    )


def read_frame_batch(
    path: str | Path,
    *,
    limits: TraceLimits | None = None,
) -> tuple[FrameRecord, ...]:
    """Read a bounded JSONL file containing named hexadecimal frames."""

    if limits is None:
        limits = TraceLimits()
    elif not isinstance(limits, TraceLimits):
        raise ValueError("limits must be a TraceLimits instance")

    try:
        source = Path(path)
    except TypeError:
        raise ValueError("path must be path-like") from None

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(source, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"trace file is not a regular file: {source}")
        if metadata.st_size > limits.max_bytes:
            raise ValueError(f"trace file exceeds max_bytes ({limits.max_bytes})")
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
    except ValueError:
        raise
    except OSError:
        raise ValueError(f"cannot read trace file: {source}") from None
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)

    records: list[FrameRecord] = []
    raw_byte_count = 0
    try:
        with stream:
            line_number = 0
            while raw_line := stream.readline(limits.max_line_bytes + 1):
                line_number += 1
                raw_byte_count += len(raw_line)
                if raw_byte_count > limits.max_bytes:
                    raise ValueError(
                        f"trace file exceeds max_bytes ({limits.max_bytes})"
                    )
                if len(raw_line) > limits.max_line_bytes:
                    raise ValueError(
                        f"line {line_number}: exceeds max_line_bytes "
                        f"({limits.max_line_bytes})"
                    )
                try:
                    text = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    raise ValueError(f"line {line_number}: invalid UTF-8") from None
                if not text.strip():
                    continue
                if len(records) >= limits.max_records:
                    raise ValueError(
                        f"line {line_number}: record count exceeds max_records "
                        f"({limits.max_records})"
                    )
                records.append(_parse_record(text, line_number, limits))
    except ValueError:
        raise
    except OSError:
        raise ValueError(f"cannot read trace file: {source}") from None

    return tuple(records)


__all__ = ["FrameRecord", "TraceLimits", "read_frame_batch"]
