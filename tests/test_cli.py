from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fpga_packet_checksum_offload import cli
from fpga_packet_checksum_offload.trace_io import TraceLimits

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests/fixtures/frames.json").read_text(encoding="utf-8"))
VALID_UDP = FIXTURES["udp_odd"]["frame_hex"]
FRAGMENTED_UDP = FIXTURES["udp_first_fragment"]["frame_hex"]


def _run(*arguments: str, module: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source = str(ROOT / "src")
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    command = [sys.executable]
    if module:
        command.extend(["-m", "fpga_packet_checksum_offload"])
    command.extend(arguments)
    return subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _invalid_ipv4_checksum(frame_hex: str) -> str:
    frame = bytearray.fromhex(frame_hex)
    frame[24] ^= 0x01
    return frame.hex()


@pytest.mark.parametrize(
    "command",
    [
        (),
        ("checksum", "--help"),
        ("inspect", "--help"),
        ("batch", "--help"),
        ("campaign", "--help"),
    ],
)
def test_help_is_available_without_tracebacks(command):
    result = _run(*command, "--help") if not command else _run(*command)

    assert result.returncode == cli.EXIT_SUCCESS
    assert "usage:" in result.stdout.lower()
    assert "traceback" not in result.stderr.lower()


def test_checksum_json_is_deterministic_and_supports_numeric_seed():
    first = _run("checksum", "0001f203", "--seed", "0x10")
    second = _run("checksum", "0001f203", "--seed", "0x10")
    decimal = _run("checksum", "0001f203", "--seed", "0016")

    assert first.returncode == cli.EXIT_SUCCESS
    assert first.stderr == ""
    assert first.stdout == second.stdout
    assert decimal.returncode == cli.EXIT_SUCCESS
    assert json.loads(decimal.stdout)["seed"] == 16
    document = json.loads(first.stdout)
    assert document == {
        "schema_version": 1,
        "operation": "checksum",
        "byte_length": 4,
        "seed": 16,
        "checksum": 3563,
        "expected_checksum": None,
        "matches_expected": None,
    }


def test_checksum_expected_value_controls_validation_exit_and_text_output():
    value = json.loads(_run("checksum", "123456").stdout)["checksum"]
    matched = _run(
        "checksum",
        "123456",
        "--expected",
        str(value),
        "--format",
        "text",
    )
    mismatched = _run("checksum", "123456", "--expected", "0")

    assert matched.returncode == cli.EXIT_SUCCESS
    assert matched.stdout.endswith("match=true\n")
    assert matched.stderr == ""
    assert mismatched.returncode == cli.EXIT_VALIDATION_FAILED
    assert json.loads(mismatched.stdout)["matches_expected"] is False
    assert mismatched.stderr == ""


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("checksum", "0"), "even number"),
        (("checksum", "zz"), "hexadecimal"),
        (("checksum", "00", "--seed", "true"), "integer"),
        (("checksum", "00", "--seed", "1.0"), "integer"),
        (("checksum", "00", "--seed", "12garbage"), "integer"),
        (("checksum", "00", "--seed", "65536"), "between 0 and 65535"),
        (("checksum", "00", "--expected", "-1"), "between 0 and 65535"),
    ],
)
def test_checksum_rejects_malformed_inputs_as_operational_errors(arguments, message):
    result = _run(*arguments)

    assert result.returncode == cli.EXIT_OPERATIONAL_ERROR
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert result.stderr.count("\n") == 1
    assert message in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


@pytest.mark.parametrize(
    ("command", "argument_name"),
    [("checksum", "data"), ("inspect", "frame")],
)
def test_direct_hex_arguments_reject_more_than_65535_decoded_bytes(
    command, argument_name, capsys
):
    limit = TraceLimits().max_frame_bytes

    exit_code = cli.main([command, "00" * (limit + 1)])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_OPERATIONAL_ERROR
    assert captured.out == ""
    assert captured.err == (
        f"error: argument {argument_name}: hex input exceeds maximum decoded "
        f"size ({limit} bytes)\n"
    )
    assert captured.err.count("\n") == 1
    assert "traceback" not in captured.err.lower()


def test_direct_hex_arguments_accept_exactly_65535_decoded_bytes(capsys):
    limit = TraceLimits().max_frame_bytes

    checksum_exit = cli.main(["checksum", "00" * limit])
    checksum_output = capsys.readouterr()
    padded_frame = VALID_UDP + "00" * (limit - len(bytes.fromhex(VALID_UDP)))
    inspect_exit = cli.main(["inspect", padded_frame])
    inspect_output = capsys.readouterr()

    assert checksum_exit == cli.EXIT_SUCCESS
    assert checksum_output.err == ""
    assert json.loads(checksum_output.out)["byte_length"] == limit
    assert inspect_exit == cli.EXIT_SUCCESS
    assert inspect_output.err == ""
    assert json.loads(inspect_output.out)["inspection"]["checksum_state"] == "valid"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ((), "required: command"),
        (("unknown-command",), "invalid choice"),
        (("checksum",), "required: data"),
        (("checksum", "00", "--unknown-option"), "unrecognized arguments"),
        (("checksum", "00", "--seed"), "expected one argument"),
        (("campaign", "--format", "yaml"), "invalid choice"),
    ],
)
def test_argument_failures_are_single_line_operational_errors(arguments, message):
    result = _run(*arguments)

    assert result.returncode == cli.EXIT_OPERATIONAL_ERROR
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert result.stderr.endswith("\n")
    assert result.stderr.count("\n") == 1
    assert message in result.stderr.lower()
    assert "usage:" not in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_inspect_renders_valid_json_and_invalid_checksum_is_validation_failure():
    valid = _run("inspect", VALID_UDP)
    invalid = _run("inspect", _invalid_ipv4_checksum(VALID_UDP))

    assert valid.returncode == cli.EXIT_SUCCESS
    assert valid.stderr == ""
    assert json.loads(valid.stdout)["inspection"]["checksum_state"] == "valid"
    assert invalid.returncode == cli.EXIT_VALIDATION_FAILED
    assert (
        json.loads(invalid.stdout)["inspection"]["ipv4"]["checksum"]["state"]
        == "invalid"
    )
    assert invalid.stderr == ""


def test_inspect_accepts_nonapplicable_and_disabled_states_by_policy():
    arp_like = "00112233445566778899aabb0806"
    disabled_udp = bytearray.fromhex(VALID_UDP)
    disabled_udp[40:42] = b"\x00\x00"

    not_applicable = _run("inspect", arp_like)
    disabled = _run("inspect", disabled_udp.hex())

    assert not_applicable.returncode == cli.EXIT_SUCCESS
    assert (
        json.loads(not_applicable.stdout)["inspection"]["checksum_state"]
        == "not_applicable"
    )
    assert disabled.returncode == cli.EXIT_SUCCESS
    assert json.loads(disabled.stdout)["inspection"]["checksum_state"] == "disabled"


def test_inspect_fragmented_udp_reports_incomplete_as_success_deterministically():
    first = _run("inspect", FRAGMENTED_UDP)
    second = _run("inspect", FRAGMENTED_UDP)

    assert first.returncode == cli.EXIT_SUCCESS
    assert first.stderr == ""
    assert first.stdout == second.stdout
    inspection = json.loads(first.stdout)["inspection"]
    assert inspection["checksum_state"] == "incomplete"
    assert inspection["ipv4"]["checksum"]["state"] == "valid"
    assert inspection["transport"]["protocol"] == "UDP"
    assert inspection["transport"]["checksum"]["state"] == "incomplete"


def test_inspect_markdown_can_be_written_atomically(tmp_path):
    target = tmp_path / "nested" / "inspection.md"
    result = _run(
        "inspect",
        VALID_UDP,
        "--format",
        "markdown",
        "--output",
        str(target),
    )

    assert result.returncode == cli.EXIT_SUCCESS
    assert result.stdout == ""
    assert result.stderr == ""
    assert target.read_text(encoding="utf-8").startswith("# Frame Inspection Report\n")
    assert not tuple(target.parent.glob(".*.tmp"))


def test_inspect_structural_failure_has_concise_diagnostic():
    result = _run("inspect", "00")

    assert result.returncode == cli.EXIT_OPERATIONAL_ERROR
    assert result.stdout == ""
    assert result.stderr.startswith("error: truncated Ethernet header")
    assert result.stderr.count("\n") == 1
    assert "traceback" not in result.stderr.lower()


def test_batch_preserves_order_and_failed_records_control_exit(tmp_path):
    invalid = _invalid_ipv4_checksum(VALID_UDP)
    trace = tmp_path / "frames.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps({"name": "valid", "frame_hex": VALID_UDP}),
                json.dumps({"name": "bad-checksum", "frame_hex": invalid}),
                json.dumps({"name": "truncated", "frame_hex": "00"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run("batch", str(trace))

    assert result.returncode == cli.EXIT_VALIDATION_FAILED
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert [item["name"] for item in report["outcomes"]] == [
        "valid",
        "bad-checksum",
        "truncated",
    ]
    assert report["summary"] == {"total": 3, "success": 2, "error": 1}


def test_batch_accepts_valid_and_incomplete_fragment_records(tmp_path):
    trace = tmp_path / "frames.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps({"name": "valid", "frame_hex": VALID_UDP}),
                json.dumps({"name": "fragment", "frame_hex": FRAGMENTED_UDP}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run("batch", str(trace))

    assert result.returncode == cli.EXIT_SUCCESS
    assert result.stderr == ""
    outcomes = json.loads(result.stdout)["outcomes"]
    assert [outcome["inspection"]["checksum_state"] for outcome in outcomes] == [
        "valid",
        "incomplete",
    ]


def test_batch_reader_failure_is_operational_and_has_no_report(tmp_path):
    malformed = tmp_path / "bad.jsonl"
    malformed.write_text(
        '{"name":"x","frame_hex":"00","extra":true}\n', encoding="utf-8"
    )

    result = _run("batch", str(malformed), "--format", "markdown")

    assert result.returncode == cli.EXIT_OPERATIONAL_ERROR
    assert result.stdout == ""
    assert result.stderr.startswith("error: line 1: unknown field")
    assert "traceback" not in result.stderr.lower()


def test_campaign_formats_are_deterministic_and_report_complete_coverage():
    first = _run("campaign")
    second = _run("campaign")
    text_result = _run("campaign", "--format", "text")
    markdown = _run("campaign", "--format", "markdown")

    assert first.returncode == cli.EXIT_SUCCESS
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["summary"] == {"total": 14, "passed": 14, "failed": 0}
    assert len(report["cases"]) == 14
    assert text_result.stdout.startswith("checksum-cycle-campaign-v1 14/14 passed")
    assert markdown.stdout.startswith("# Checksum Cycle Campaign\n")


def test_campaign_failure_returns_validation_exit(monkeypatch, capsys):
    original = cli.run_campaign()
    failed = original.__class__(
        cases=(
            original.cases[0].__class__(
                name="controlled_failure",
                behavior_names=("controlled",),
                passed=False,
                detail="expected test failure",
            ),
        ),
        total=1,
        passed=0,
        failed_case_names=("controlled_failure",),
        covered_behavior_names=("controlled",),
        summary=(
            "checksum-cycle-campaign-v1 0/1 passed; "
            "failed=controlled_failure; coverage=controlled"
        ),
    )
    monkeypatch.setattr(cli, "run_campaign", lambda: failed)

    exit_code = cli.main(["campaign"])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_VALIDATION_FAILED
    assert json.loads(captured.out)["summary"]["failed"] == 1
    assert captured.err == ""


def test_atomic_output_failure_is_an_operational_error(monkeypatch, capsys):
    def fail_write(path, text):
        raise OSError("platform detail must stay private")

    monkeypatch.setattr(cli, "write_text_atomic", fail_write)

    exit_code = cli.main(["campaign", "--output", "report.json"])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_OPERATIONAL_ERROR
    assert captured.out == ""
    assert captured.err == "error: cannot write output file: report.json\n"


def test_checked_in_examples_and_reports_are_reproducible(tmp_path):
    example = ROOT / "examples/frames.jsonl"
    expected_commands = {
        ROOT / "reports/batch-report.json": ("batch", str(example)),
        ROOT / "reports/batch-report.md": (
            "batch",
            str(example),
            "--format",
            "markdown",
        ),
        ROOT / "reports/campaign.json": ("campaign",),
        ROOT / "reports/campaign.md": ("campaign", "--format", "markdown"),
        ROOT / "reports/frame-inspection.json": ("inspect", VALID_UDP),
    }

    for report_path, arguments in expected_commands.items():
        result = _run(*arguments)
        assert result.returncode == cli.EXIT_SUCCESS
        assert result.stderr == ""
        assert report_path.read_bytes() == result.stdout.encode("utf-8")


def test_public_exit_constants_are_stable():
    assert (
        cli.EXIT_SUCCESS,
        cli.EXIT_VALIDATION_FAILED,
        cli.EXIT_OPERATIONAL_ERROR,
    ) == (0, 1, 2)
