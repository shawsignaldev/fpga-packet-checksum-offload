from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from fpga_packet_checksum_offload import reporting
from fpga_packet_checksum_offload.packet import inspect_ethernet_frame
from fpga_packet_checksum_offload.reporting import (
    SCHEMA_VERSION,
    BatchOutcome,
    inspect_records,
    inspection_to_dict,
    render_batch_json,
    render_batch_markdown,
    render_inspection_json,
    render_inspection_markdown,
    write_text_atomic,
)
from fpga_packet_checksum_offload.trace_io import FrameRecord

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "frames.json").read_text(encoding="ascii")
)
UDP_FRAME = bytes.fromhex(FIXTURES["udp_odd"]["frame_hex"])


def _udp_inspection():
    return inspect_ethernet_frame(UDP_FRAME)


def _success(name: str = "udp") -> BatchOutcome:
    return BatchOutcome(
        name=name,
        frame_length=len(UDP_FRAME),
        inspection=_udp_inspection(),
        error=None,
    )


def test_batch_outcome_is_an_immutable_slot_with_exactly_one_result():
    outcome = _success()

    with pytest.raises(FrozenInstanceError):
        outcome.name = "changed"
    with pytest.raises((AttributeError, TypeError)):
        outcome.extra = 1

    with pytest.raises(
        ValueError,
        match="exactly one of inspection or error must be set",
    ):
        BatchOutcome(name="missing", frame_length=0, inspection=None, error=None)
    with pytest.raises(
        ValueError,
        match="exactly one of inspection or error must be set",
    ):
        BatchOutcome(
            name="both",
            frame_length=len(UDP_FRAME),
            inspection=_udp_inspection(),
            error="also failed",
        )


@pytest.mark.parametrize(
    ("arguments", "exception", "message"),
    [
        (
            {"name": 1, "frame_length": 0, "inspection": None, "error": "bad"},
            TypeError,
            "name must be a string",
        ),
        (
            {
                "name": "x",
                "frame_length": True,
                "inspection": None,
                "error": "bad",
            },
            TypeError,
            "frame_length must be an integer",
        ),
        (
            {
                "name": "x",
                "frame_length": 1.5,
                "inspection": None,
                "error": "bad",
            },
            TypeError,
            "frame_length must be an integer",
        ),
        (
            {
                "name": "x",
                "frame_length": -1,
                "inspection": None,
                "error": "bad",
            },
            ValueError,
            "frame_length must be nonnegative",
        ),
        (
            {
                "name": "x",
                "frame_length": 0,
                "inspection": object(),
                "error": None,
            },
            TypeError,
            "inspection must be a FrameInspection",
        ),
        (
            {"name": "x", "frame_length": 0, "inspection": None, "error": 1},
            TypeError,
            "error must be a string",
        ),
    ],
)
def test_batch_outcome_validates_fields(arguments, exception, message):
    with pytest.raises(exception, match=rf"^{message}$"):
        BatchOutcome(**arguments)


def test_inspect_records_preserves_order_and_contains_packet_errors_per_record():
    records = (
        FrameRecord(line_number=1, name="valid", frame=UDP_FRAME),
        FrameRecord(line_number=2, name="malformed", frame=b"\x00"),
        FrameRecord(line_number=3, name="valid-again", frame=UDP_FRAME),
    )

    outcomes = inspect_records(records)

    assert tuple(outcome.name for outcome in outcomes) == (
        "valid",
        "malformed",
        "valid-again",
    )
    assert outcomes[0].inspection == _udp_inspection()
    assert outcomes[0].error is None
    assert outcomes[1] == BatchOutcome(
        name="malformed",
        frame_length=1,
        inspection=None,
        error="truncated Ethernet header at byte offset 1",
    )
    assert outcomes[2].inspection == _udp_inspection()


def test_inspection_to_dict_contains_all_ipv4_udp_and_checksum_metadata():
    assert inspection_to_dict(_udp_inspection()) == {
        "destination_mac": "00:11:22:33:44:55",
        "source_mac": "66:77:88:99:aa:bb",
        "outer_ethertype": 2048,
        "payload_ethertype": 2048,
        "vlan_tags": [],
        "ipv4": {
            "offset": 14,
            "source": "192.0.2.1",
            "destination": "198.51.100.2",
            "dscp": 0,
            "ecn": 0,
            "identification": 4660,
            "ttl": 64,
            "protocol": 17,
            "flags": 2,
            "fragment_offset": 0,
            "more_fragments": False,
            "header_length": 20,
            "total_length": 33,
            "payload_length": 13,
            "checksum": {
                "value": 15457,
                "calculated": 15457,
                "state": "valid",
                "offset": 24,
            },
        },
        "transport": {
            "protocol": "UDP",
            "offset": 34,
            "source_port": 12345,
            "destination_port": 54321,
            "header_length": 8,
            "length": 13,
            "payload_length": 5,
            "checksum": {
                "value": 1580,
                "calculated": 1580,
                "state": "valid",
                "offset": 40,
            },
        },
        "checksum_state": "valid",
    }


def test_inspection_json_is_deterministic_newline_terminated_and_round_trips():
    first = render_inspection_json(_udp_inspection())
    second = render_inspection_json(_udp_inspection())

    assert first == second
    assert first.endswith("\n")
    assert not first.endswith("\n\n")
    assert list(json.loads(first)) == ["schema_version", "inspection"]
    assert json.loads(first) == {
        "schema_version": 1,
        "inspection": inspection_to_dict(_udp_inspection()),
    }


@pytest.mark.parametrize(
    "operation",
    [inspection_to_dict, render_inspection_json, render_inspection_markdown],
)
def test_inspection_render_apis_reject_non_inspection_values(operation):
    with pytest.raises(TypeError, match=r"^inspection must be a FrameInspection$"):
        operation(object())


def test_json_renderers_reject_injected_nan_values():
    invalid = replace(_udp_inspection(), outer_ethertype=float("nan"))
    outcome = BatchOutcome(
        name="nan",
        frame_length=len(UDP_FRAME),
        inspection=invalid,
        error=None,
    )

    with pytest.raises(ValueError, match=r"^Out of range float values"):
        render_inspection_json(invalid)
    with pytest.raises(ValueError, match=r"^Out of range float values"):
        render_batch_json((outcome,))


def test_empty_batch_reports_have_stable_snapshots_and_explicit_counts():
    assert SCHEMA_VERSION == 1
    assert render_batch_json(()) == (
        "{\n"
        '  "schema_version": 1,\n'
        '  "summary": {\n'
        '    "total": 0,\n'
        '    "success": 0,\n'
        '    "error": 0\n'
        "  },\n"
        '  "outcomes": []\n'
        "}\n"
    )
    assert render_batch_markdown(()) == (
        "# Batch Inspection Report\n\n"
        "- Schema version: 1\n"
        "- Total: 0\n"
        "- Success: 0\n"
        "- Errors: 0\n\n"
        "_No records._\n"
    )


def test_batch_json_preserves_order_counts_status_and_is_byte_deterministic():
    outcomes = (
        _success("first"),
        BatchOutcome(
            name="bad",
            frame_length=0,
            inspection=None,
            error="truncated Ethernet header at byte offset 0",
        ),
        _success("last"),
    )

    first = render_batch_json(outcomes)
    second = render_batch_json(outcomes)
    parsed = json.loads(first)

    assert first == second
    assert first.endswith("\n")
    assert parsed["summary"] == {"total": 3, "success": 2, "error": 1}
    assert [item["name"] for item in parsed["outcomes"]] == [
        "first",
        "bad",
        "last",
    ]
    assert list(parsed["outcomes"][0]) == [
        "name",
        "frame_length",
        "status",
        "inspection",
    ]
    assert parsed["outcomes"][0]["status"] == "success"
    assert parsed["outcomes"][1] == {
        "name": "bad",
        "frame_length": 0,
        "status": "error",
        "error": "truncated Ethernet header at byte offset 0",
    }


def test_inspection_markdown_is_deterministic_and_includes_protocol_metadata():
    first = render_inspection_markdown(_udp_inspection())
    second = render_inspection_markdown(_udp_inspection())

    assert first == second
    assert first.endswith("\n")
    for expected in (
        "# Frame Inspection Report",
        "00:11:22:33:44:55",
        "66:77:88:99:aa:bb",
        "192&#46;0&#46;2&#46;1",
        "198&#46;51&#46;100&#46;2",
        "UDP",
        "12345",
        "54321",
        "15457",
        "1580",
        "valid",
    ):
        assert expected in first


def test_batch_markdown_reports_counts_and_keeps_hostile_text_inert():
    hostile_name = "bad|`[]<>\r\n## injected [link](https://invalid)"
    hostile_error = "boom|`[]<>\n<script>alert(1)</script>"
    outcomes = (
        _success("safe"),
        BatchOutcome(
            name=hostile_name,
            frame_length=7,
            inspection=None,
            error=hostile_error,
        ),
    )

    rendered = render_batch_markdown(outcomes)

    assert "- Total: 2" in rendered
    assert "- Success: 1" in rendered
    assert "- Errors: 1" in rendered
    assert hostile_name not in rendered
    assert hostile_error not in rendered
    assert "## injected" not in rendered
    assert "[link]" not in rendered
    assert "<script>" not in rendered
    assert "&#124;" in rendered
    assert "&#96;" in rendered
    assert "&#91;" in rendered
    assert "&#60;" in rendered
    assert len(rendered.splitlines()) == 11


def test_markdown_sanitizes_hostile_dynamic_inspection_values_without_json_changes():
    protocol = "UDP|`[]<>\r\n## injected [link](https://invalid) <script>"
    original = _udp_inspection()
    assert original.transport is not None
    hostile_transport = replace(original.transport, protocol=protocol)
    hostile_inspection = replace(original, transport=hostile_transport)
    outcome = BatchOutcome(
        name="hostile protocol",
        frame_length=len(UDP_FRAME),
        inspection=hostile_inspection,
        error=None,
    )

    inspection_markdown = render_inspection_markdown(hostile_inspection)
    batch_markdown = render_batch_markdown((outcome,))

    assert (
        json.loads(render_inspection_json(hostile_inspection))["inspection"][
            "transport"
        ]["protocol"]
        == protocol
    )
    assert (
        json.loads(render_batch_json((outcome,)))["outcomes"][0]["inspection"][
            "transport"
        ]["protocol"]
        == protocol
    )
    for rendered in (inspection_markdown, batch_markdown):
        assert protocol not in rendered
        assert "## injected" not in rendered
        assert "[link]" not in rendered
        assert "<script>" not in rendered
        assert "&#124;" in rendered
        assert "&#96;" in rendered
        assert "&#91;" in rendered
        assert "&#60;" in rendered
    assert len(inspection_markdown.splitlines()) == len(
        render_inspection_markdown(original).splitlines()
    )
    assert len(batch_markdown.splitlines()) == 10


def test_markdown_breaks_bare_autolinks_without_changing_json_content():
    autolink = "www.evil.com"
    original = _udp_inspection()
    assert original.transport is not None
    hostile_inspection = replace(
        original,
        transport=replace(original.transport, protocol=f"UDP {autolink}"),
    )
    outcomes = (
        BatchOutcome(
            name=autolink,
            frame_length=len(UDP_FRAME),
            inspection=hostile_inspection,
            error=None,
        ),
        BatchOutcome(
            name=f"failed {autolink}",
            frame_length=0,
            inspection=None,
            error=f"visit {autolink}",
        ),
    )

    inspection_markdown = render_inspection_markdown(hostile_inspection)
    batch_markdown = render_batch_markdown(outcomes)
    inspection_json = json.loads(render_inspection_json(hostile_inspection))
    batch_json = json.loads(render_batch_json(outcomes))

    for rendered in (inspection_markdown, batch_markdown):
        assert autolink not in rendered
        assert "www&#46;evil&#46;com" in rendered
    assert inspection_json["inspection"]["transport"]["protocol"] == (f"UDP {autolink}")
    assert batch_json["outcomes"][0]["name"] == autolink
    assert batch_json["outcomes"][0]["inspection"]["transport"]["protocol"] == (
        f"UDP {autolink}"
    )
    assert batch_json["outcomes"][1]["name"] == f"failed {autolink}"
    assert batch_json["outcomes"][1]["error"] == f"visit {autolink}"


def test_write_text_atomic_creates_parents_and_replaces_with_utf8_lf(tmp_path):
    target = tmp_path / "nested" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("old\r\n", encoding="utf-8")

    write_text_atomic(target, "alpha\nbeta\n")

    assert target.read_bytes() == b"alpha\nbeta\n"
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []

    second = tmp_path / "new" / "path" / "report.json"
    write_text_atomic(second, '{"ok": true}\n')
    assert second.read_bytes() == b'{"ok": true}\n'


def test_write_text_atomic_fsyncs_parent_after_replace(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "report.txt"
    events = []
    actual_replace = os.replace

    def capture_replace(source, destination):
        actual_replace(source, destination)
        events.append(("replace", Path(destination)))

    def capture_directory_fsync(directory):
        events.append(("fsync", Path(directory)))

    monkeypatch.setattr(reporting.os, "replace", capture_replace)
    monkeypatch.setattr(
        reporting,
        "_fsync_directory",
        capture_directory_fsync,
        raising=False,
    )

    write_text_atomic(target, "durable\n")

    assert events == [("replace", target), ("fsync", target.parent)]


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory fsync required")
def test_fsync_directory_uses_and_closes_directory_descriptor(tmp_path, monkeypatch):
    actual_open = os.open
    actual_fsync = os.fsync
    actual_close = os.close
    opened = []
    fsynced = []
    closed = []

    def capture_open(path, flags):
        descriptor = actual_open(path, flags)
        opened.append((Path(path), flags, descriptor))
        return descriptor

    def capture_fsync(descriptor):
        fsynced.append(descriptor)
        actual_fsync(descriptor)

    def capture_close(descriptor):
        closed.append(descriptor)
        actual_close(descriptor)

    monkeypatch.setattr(reporting.os, "open", capture_open)
    monkeypatch.setattr(reporting.os, "fsync", capture_fsync)
    monkeypatch.setattr(reporting.os, "close", capture_close)

    reporting._fsync_directory(tmp_path)

    assert len(opened) == 1
    assert opened[0][0] == tmp_path
    assert opened[0][1] & getattr(os, "O_DIRECTORY", 0) == getattr(os, "O_DIRECTORY", 0)
    assert fsynced == [opened[0][2]]
    assert closed == [opened[0][2]]


@pytest.mark.skipif(os.name == "posix", reason="non-POSIX fallback required")
def test_fsync_directory_is_noop_off_posix(tmp_path, monkeypatch):
    def unexpected_open(path, flags):
        raise AssertionError("directory descriptors are not portable off POSIX")

    monkeypatch.setattr(reporting.os, "open", unexpected_open)

    reporting._fsync_directory(tmp_path)


def test_write_text_atomic_cleans_sibling_temp_when_replace_fails(
    tmp_path, monkeypatch
):
    target = tmp_path / "report.txt"
    target.write_bytes(b"original\n")

    def fail_replace(source, destination):
        raise OSError("replace denied")

    monkeypatch.setattr(reporting.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace denied"):
        write_text_atomic(target, "replacement\n")

    assert target.read_bytes() == b"original\n"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_write_text_atomic_cleanup_does_not_mask_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "report.txt"

    def fail_replace(source, destination):
        raise OSError("replace denied")

    def fail_unlink(self, *args, **kwargs):
        raise OSError("cleanup denied")

    monkeypatch.setattr(reporting.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(OSError, match=r"^replace denied$"):
        write_text_atomic(target, "replacement\n")


def test_write_text_atomic_cleanup_does_not_mask_write_failure(tmp_path, monkeypatch):
    target = tmp_path / "report.txt"

    def fail_unlink(self, *args, **kwargs):
        raise OSError("cleanup denied")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(TypeError, match=r"write\(\) argument must be str"):
        write_text_atomic(target, object())


def test_stable_reporting_api_is_reexported_from_package():
    import fpga_packet_checksum_offload as package

    expected = {
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "BatchOutcome": BatchOutcome,
        "inspect_records": inspect_records,
        "inspection_to_dict": inspection_to_dict,
        "render_inspection_json": render_inspection_json,
        "render_inspection_markdown": render_inspection_markdown,
        "render_batch_json": render_batch_json,
        "render_batch_markdown": render_batch_markdown,
        "write_text_atomic": write_text_atomic,
    }
    for name, value in expected.items():
        assert getattr(package, name) is value
