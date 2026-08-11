from __future__ import annotations

import os
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints

import pytest

from fpga_packet_checksum_offload import trace_io
from fpga_packet_checksum_offload.trace_io import (
    FrameRecord,
    TraceLimits,
    read_frame_batch,
)


def _write_bytes(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_trace_limits_defaults_are_positive_immutable_slots():
    limits = TraceLimits()

    assert limits == TraceLimits(
        max_bytes=8 * 1024 * 1024,
        max_line_bytes=256 * 1024,
        max_records=10_000,
        max_frame_bytes=65_535,
        max_nesting=4,
    )
    assert limits.__slots__ == (
        "max_bytes",
        "max_line_bytes",
        "max_records",
        "max_frame_bytes",
        "max_nesting",
    )
    with pytest.raises(FrozenInstanceError):
        limits.max_bytes = 1
    with pytest.raises((AttributeError, TypeError)):
        limits.extra = 1


@pytest.mark.parametrize(
    "field",
    [
        "max_bytes",
        "max_line_bytes",
        "max_records",
        "max_frame_bytes",
        "max_nesting",
    ],
)
@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.5, "1", None])
def test_trace_limits_reject_non_positive_or_non_integer_values(field, invalid):
    with pytest.raises(ValueError, match=rf"^{field} must be a positive integer$"):
        TraceLimits(**{field: invalid})


@pytest.mark.parametrize("invalid", [sys.maxsize, sys.maxsize + 1])
def test_trace_limits_reject_line_bounds_that_overflow_readline(invalid):
    with pytest.raises(
        ValueError,
        match=r"^max_line_bytes must be less than sys\.maxsize$",
    ):
        TraceLimits(max_line_bytes=invalid)


def test_read_frame_batch_allows_blank_lines_and_preserves_source_lines(tmp_path):
    path = _write_bytes(
        tmp_path / "frames.jsonl",
        b"\n"
        b'{"name":"first","frame_hex":"0011"}\n'
        b"  \t\r\n"
        b'{"name":"second","frame_hex":"aAbB"}',
    )

    records = read_frame_batch(path)

    assert records == (
        FrameRecord(line_number=2, name="first", frame=b"\x00\x11"),
        FrameRecord(line_number=4, name="second", frame=b"\xaa\xbb"),
    )
    with pytest.raises(FrozenInstanceError):
        records[0].name = "changed"
    with pytest.raises((AttributeError, TypeError)):
        records[0].extra = 1


@pytest.mark.parametrize(
    ("arguments", "exception", "message"),
    [
        (
            {"line_number": True, "name": "x", "frame": b""},
            TypeError,
            "line_number must be an integer",
        ),
        (
            {"line_number": 1.5, "name": "x", "frame": b""},
            TypeError,
            "line_number must be an integer",
        ),
        (
            {"line_number": 0, "name": "x", "frame": b""},
            ValueError,
            "line_number must be positive",
        ),
        (
            {"line_number": -1, "name": "x", "frame": b""},
            ValueError,
            "line_number must be positive",
        ),
        (
            {"line_number": 1, "name": b"x", "frame": b""},
            TypeError,
            "name must be a string",
        ),
        (
            {"line_number": 1, "name": "x", "frame": bytearray()},
            TypeError,
            "frame must be bytes",
        ),
        (
            {"line_number": 1, "name": "x", "frame": memoryview(b"")},
            TypeError,
            "frame must be bytes",
        ),
    ],
)
def test_frame_record_validates_fields(arguments, exception, message):
    with pytest.raises(exception, match=rf"^{message}$"):
        FrameRecord(**arguments)


def test_read_frame_batch_accepts_every_limit_at_its_exact_boundary(tmp_path):
    raw = b'{"name":"x","frame_hex":"00"}\n'
    path = _write_bytes(tmp_path / "boundary.jsonl", raw)
    limits = TraceLimits(
        max_bytes=len(raw),
        max_line_bytes=len(raw),
        max_records=1,
        max_frame_bytes=1,
        max_nesting=1,
    )

    assert read_frame_batch(path, limits=limits) == (
        FrameRecord(line_number=1, name="x", frame=b"\x00"),
    )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"{\n", r"^line 1: invalid JSON$"),
        (
            b'{"name":"first","name":"second","frame_hex":"00"}\n',
            r"^line 1: duplicate key 'name'$",
        ),
        (
            b'{"name":1.25,"frame_hex":"00"}\n',
            r"^line 1: floating-point values are not permitted$",
        ),
        (
            b'{"name":NaN,"frame_hex":"00"}\n',
            r"^line 1: non-finite values are not permitted$",
        ),
        (b"[]\n", r"^line 1: record must be a JSON object$"),
        (
            b'{"name":"only"}\n',
            r"^line 1: missing required field: frame_hex$",
        ),
        (
            b'{"name":"x","frame_hex":"00","extra":"value"}\n',
            r"^line 1: unknown field: extra$",
        ),
        (
            b'{"name":1,"frame_hex":"00"}\n',
            r"^line 1: integer values are not permitted$",
        ),
        (
            b'{"name":true,"frame_hex":"00"}\n',
            r"^line 1: name must be a string$",
        ),
        (
            b'{"name":"x","frame_hex":false}\n',
            r"^line 1: frame_hex must be a string$",
        ),
        (
            b'{"name":"x","frame_hex":"0"}\n',
            r"^line 1: frame_hex must contain an even number of hexadecimal digits$",
        ),
        (
            b'{"name":"x","frame_hex":"0g"}\n',
            r"^line 1: frame_hex contains non-hexadecimal characters$",
        ),
        (
            b'{"name":"x","frame_hex":"00 11"}\n',
            r"^line 1: frame_hex contains non-hexadecimal characters$",
        ),
        (b"\xff\n", r"^line 1: invalid UTF-8$"),
    ],
)
def test_read_frame_batch_rejects_malformed_records_with_stable_messages(
    tmp_path, raw, message
):
    path = _write_bytes(tmp_path / "malformed.jsonl", raw)

    with pytest.raises(ValueError, match=message):
        read_frame_batch(path)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_read_frame_batch_rejects_every_nonfinite_json_constant(tmp_path, constant):
    path = _write_bytes(
        tmp_path / "nonfinite.jsonl",
        b'{"name":' + constant + b',"frame_hex":"00"}\n',
    )

    with pytest.raises(
        ValueError,
        match=r"^line 1: non-finite values are not permitted$",
    ):
        read_frame_batch(path)


def test_read_frame_batch_rejects_line_bound_integer_before_conversion(tmp_path):
    limits = TraceLimits()
    prefix = b'{"name":'
    suffix = b',"frame_hex":"00"}\n'
    integer = b"9" * (limits.max_line_bytes - len(prefix) - len(suffix))
    raw_line = prefix + integer + suffix
    assert len(raw_line) == limits.max_line_bytes
    path = _write_bytes(tmp_path / "integer.jsonl", b"\n" + raw_line)

    with pytest.raises(
        ValueError,
        match=r"^line 2: integer values are not permitted$",
    ):
        read_frame_batch(path, limits=limits)


def test_read_frame_batch_reports_physical_line_for_later_error(tmp_path):
    path = _write_bytes(
        tmp_path / "later.jsonl",
        b'\n{"name":"ok","frame_hex":"00"}\n\n{"name":"bad"}\n',
    )

    with pytest.raises(
        ValueError,
        match=r"^line 4: missing required field: frame_hex$",
    ):
        read_frame_batch(path)


def test_read_frame_batch_escapes_control_characters_in_unknown_field(tmp_path):
    path = _write_bytes(
        tmp_path / "hostile-key.jsonl",
        b'{"name":"x","frame_hex":"00","bad\\r\\nkey\\u0001":"value"}\n',
    )

    with pytest.raises(ValueError) as raised:
        read_frame_batch(path)

    assert str(raised.value) == r"line 1: unknown field: bad\r\nkey\u0001"
    assert len(str(raised.value).splitlines()) == 1


def test_read_frame_batch_enforces_descriptor_file_size_limit(tmp_path):
    raw = b'{"name":"x","frame_hex":"00"}\n'
    path = _write_bytes(tmp_path / "too-large.jsonl", raw)

    with pytest.raises(
        ValueError,
        match=rf"^trace file exceeds max_bytes \({len(raw) - 1}\)$",
    ):
        read_frame_batch(path, limits=TraceLimits(max_bytes=len(raw) - 1))


def test_read_frame_batch_counts_streamed_bytes_after_stale_descriptor_stat(
    tmp_path, monkeypatch
):
    raw = b'{"name":"x","frame_hex":"00"}\n'
    path = _write_bytes(tmp_path / "grew.jsonl", raw)
    actual = path.stat()

    monkeypatch.setattr(
        os,
        "fstat",
        lambda descriptor: SimpleNamespace(st_mode=actual.st_mode, st_size=0),
    )

    with pytest.raises(
        ValueError,
        match=rf"^trace file exceeds max_bytes \({len(raw) - 1}\)$",
    ):
        read_frame_batch(path, limits=TraceLimits(max_bytes=len(raw) - 1))


def test_read_frame_batch_enforces_raw_line_size_limit(tmp_path):
    path = _write_bytes(tmp_path / "line.jsonl", b"{}\n")

    with pytest.raises(
        ValueError,
        match=r"^line 1: exceeds max_line_bytes \(2\)$",
    ):
        read_frame_batch(path, limits=TraceLimits(max_line_bytes=2))


def test_read_frame_batch_enforces_record_limit_without_counting_blanks(tmp_path):
    path = _write_bytes(
        tmp_path / "records.jsonl",
        b'{"name":"one","frame_hex":""}\n\n{"name":"two","frame_hex":""}\n',
    )

    with pytest.raises(
        ValueError,
        match=r"^line 3: record count exceeds max_records \(1\)$",
    ):
        read_frame_batch(path, limits=TraceLimits(max_records=1))


def test_read_frame_batch_enforces_decoded_frame_limit(tmp_path):
    path = _write_bytes(
        tmp_path / "frame.jsonl",
        b'{"name":"two-bytes","frame_hex":"0001"}\n',
    )

    with pytest.raises(
        ValueError,
        match=r"^line 1: frame exceeds max_frame_bytes \(1\)$",
    ):
        read_frame_batch(path, limits=TraceLimits(max_frame_bytes=1))


def test_read_frame_batch_enforces_json_nesting_limit(tmp_path):
    path = _write_bytes(
        tmp_path / "nested.jsonl",
        b'{"name":{"nested":"x"},"frame_hex":"00"}\n',
    )

    with pytest.raises(
        ValueError,
        match=r"^line 1: JSON nesting exceeds max_nesting \(1\)$",
    ):
        read_frame_batch(path, limits=TraceLimits(max_nesting=1))


def test_read_frame_batch_normalizes_very_deep_valid_json_to_nesting_error(tmp_path):
    nested_value = b"[" * 500 + b'"deep"' + b"]" * 500
    path = _write_bytes(
        tmp_path / "very-deep.jsonl",
        b'{"name":' + nested_value + b',"frame_hex":"00"}\n',
    )

    with pytest.raises(
        ValueError,
        match=r"^line 1: JSON nesting exceeds max_nesting \(4\)$",
    ):
        read_frame_batch(path)


def test_read_frame_batch_rejects_missing_and_non_regular_paths(tmp_path):
    with pytest.raises(ValueError, match=r"^cannot read trace file:"):
        read_frame_batch(tmp_path / "missing.jsonl")

    directory_error = (
        r"^trace file is not a regular file:"
        if os.name == "posix"
        else r"^cannot read trace file:"
    )
    with pytest.raises(ValueError, match=directory_error):
        read_frame_batch(tmp_path)


def test_read_frame_batch_wraps_open_permission_failures(tmp_path, monkeypatch):
    path = _write_bytes(tmp_path / "unreadable.jsonl", b"")

    def deny_open(path, flags):
        raise PermissionError("platform-specific detail")

    monkeypatch.setattr(os, "open", deny_open)

    with pytest.raises(ValueError, match=r"^cannot read trace file:"):
        read_frame_batch(path)


def test_read_frame_batch_opens_once_with_binary_nonblocking_read_flags(
    tmp_path, monkeypatch
):
    path = _write_bytes(tmp_path / "flags.jsonl", b"")
    actual_open = os.open
    calls = []

    def capture_open(open_path, flags):
        calls.append((open_path, flags))
        return actual_open(open_path, flags)

    monkeypatch.setattr(os, "open", capture_open)

    assert read_frame_batch(path) == ()
    assert len(calls) == 1
    assert Path(calls[0][0]) == path
    flags = calls[0][1]
    if hasattr(os, "O_ACCMODE"):
        assert flags & os.O_ACCMODE == os.O_RDONLY
    for optional_flag in (getattr(os, "O_BINARY", 0), getattr(os, "O_NONBLOCK", 0)):
        assert flags & optional_flag == optional_flag


def test_read_frame_batch_does_not_check_path_metadata_before_open(
    tmp_path, monkeypatch
):
    path = _write_bytes(tmp_path / "descriptor.jsonl", b"")

    class DescriptorOnlyPath:
        def __init__(self, value):
            self.value = value

        def __fspath__(self):
            return os.fspath(self.value)

        def __str__(self):
            return str(self.value)

        def stat(self):
            raise AssertionError("path metadata creates a check-then-open race")

    monkeypatch.setattr(trace_io, "Path", DescriptorOnlyPath)

    assert read_frame_batch(path) == ()


def test_read_frame_batch_closes_descriptor_when_fstat_fails(tmp_path, monkeypatch):
    path = _write_bytes(tmp_path / "fstat-failure.jsonl", b"")
    descriptor = 91_231
    closed = []

    monkeypatch.setattr(os, "open", lambda path, flags: descriptor)

    def fail_fstat(received):
        assert received == descriptor
        raise OSError("fstat failed")

    monkeypatch.setattr(os, "fstat", fail_fstat)
    monkeypatch.setattr(os, "close", closed.append)

    with pytest.raises(ValueError, match=r"^cannot read trace file:"):
        read_frame_batch(path)
    assert closed == [descriptor]


def test_read_frame_batch_closes_descriptor_when_fdopen_fails(tmp_path, monkeypatch):
    path = _write_bytes(tmp_path / "fdopen-failure.jsonl", b"")
    metadata = path.stat()
    descriptor = 91_232
    closed = []

    monkeypatch.setattr(os, "open", lambda path, flags: descriptor)
    monkeypatch.setattr(os, "fstat", lambda received: metadata)

    def fail_fdopen(received, mode):
        assert (received, mode) == (descriptor, "rb")
        raise OSError("fdopen failed")

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    monkeypatch.setattr(os, "close", closed.append)

    with pytest.raises(ValueError, match=r"^cannot read trace file:"):
        read_frame_batch(path)
    assert closed == [descriptor]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO required")
def test_read_frame_batch_rejects_fifo_without_blocking(tmp_path):
    path = tmp_path / "frames.fifo"
    os.mkfifo(path)

    with pytest.raises(ValueError, match=r"^trace file is not a regular file:"):
        read_frame_batch(path)


def test_read_frame_batch_rejects_invalid_path_and_limits_objects(tmp_path):
    with pytest.raises(ValueError, match=r"^path must be path-like$"):
        read_frame_batch(None)

    path = _write_bytes(tmp_path / "valid.jsonl", b"")
    with pytest.raises(ValueError, match=r"^limits must be a TraceLimits instance$"):
        read_frame_batch(path, limits=object())


def test_read_frame_batch_rejects_bytes_paths_without_advertising_them(tmp_path):
    path = _write_bytes(tmp_path / "valid.jsonl", b"")

    assert get_type_hints(read_frame_batch)["path"] == str | Path
    with pytest.raises(ValueError, match=r"^path must be path-like$"):
        read_frame_batch(str(path).encode("utf-8"))


def test_stable_trace_io_api_is_reexported_from_package():
    import fpga_packet_checksum_offload as package

    assert package.FrameRecord is FrameRecord
    assert package.TraceLimits is TraceLimits
    assert package.read_frame_batch is read_frame_batch
