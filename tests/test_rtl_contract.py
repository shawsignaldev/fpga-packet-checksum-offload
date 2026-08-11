from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "rtl"
VECTORS = ROOT / "tests" / "vectors" / "checksum_vectors.txt"
VECTOR_INCLUDE = ROOT / "tests" / "vectors" / "checksum_vectors.svh"

CORE = RTL / "checksum16_stream.sv"
PSEUDO = RTL / "ipv4_pseudo_header_seed.sv"
MONITOR = RTL / "checksum_stream_monitor.sv"
TESTBENCH = RTL / "tb_checksum16_stream.sv"
INVALID_TESTBENCH = RTL / "tb_invalid_parameters.sv"
FORMAL = ROOT / "formal"
FORMAL_HARNESS = FORMAL / "checksum16_stream_formal.sv"
FORMAL_CONFIG = FORMAL / "checksum16_stream.sby"
DESIGN_SPECIFICATION = ROOT / "docs" / "design-specification.md"

RTL_FILES = (CORE, PSEUDO, MONITOR, TESTBENCH, INVALID_TESTBENCH)
GUARD_MARKERS = (
    "DUT_GUARD_DATA_WIDTH",
    "DUT_GUARD_KEEP_WIDTH",
    "DUT_GUARD_LENGTH_WIDTH",
)
WATCHDOG_MARKER = "TB_WATCHDOG_PARAMETER_GUARD_MISSING"
FORMAL_FAILURE_MARKERS = {
    "odd_padding_mutation": "ASSERT_ODD_PADDING_CONTROL",
    "stall_state_mutation": "ASSERT_STALL_STABILITY_CONTROL",
}
FORMAL_COVER_MARKERS = (
    "COVER_SUCCESS",
    "COVER_MISSING_FIRST",
    "COVER_UNEXPECTED_FIRST",
    "COVER_INVALID_KEEP",
    "COVER_EMPTY_FINAL",
    "COVER_LENGTH_OVERFLOW",
    "COVER_MULTIBEAT_COMPLETION",
    "COVER_RESPONSE_STALL",
    "COVER_ZERO_BUBBLE_REPLACEMENT",
    "COVER_RECURRENT_RESET",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="ascii")


def _suite_root(environ: dict[str, str] | None = None) -> Path | None:
    environment = os.environ if environ is None else environ
    value = environment.get("OSS_CAD_SUITE_ROOT")
    return Path(value).expanduser() if value else None


def _tool(name: str, *, environ: dict[str, str] | None = None) -> str | None:
    """Find native executables and common Windows command wrappers."""

    environment = os.environ if environ is None else environ
    if name == "verilator":
        aliases = (
            ("verilator_bin", "verilator")
            if os.name == "nt"
            else (
                "verilator",
                "verilator_bin",
            )
        )
    else:
        aliases = (name,)
    suffixes = ("", ".exe", ".cmd", ".bat") if os.name == "nt" else ("",)
    suite_root = _suite_root(environment)
    for alias in aliases:
        for suffix in suffixes:
            candidate = f"{alias}{suffix}"
            if suite_root is not None:
                suite_candidate = suite_root / "bin" / candidate
                if suite_candidate.is_file():
                    return str(suite_candidate)
            found = shutil.which(candidate, path=environment.get("PATH"))
            if found:
                return found
    return None


def _tool_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if environ is None else environ)
    suite_root = _suite_root(environment)
    if suite_root is not None:
        suite_paths = os.pathsep.join(
            (str(suite_root / "bin"), str(suite_root / "lib"))
        )
        environment["PATH"] = os.pathsep.join(
            (suite_paths, environment.get("PATH", ""))
        )
        environment.setdefault(
            "VERILATOR_ROOT", str(suite_root / "share" / "verilator")
        )
    return environment


def _required_tool(name: str) -> str:
    found = _tool(name)
    if found is not None:
        return found
    if os.environ.get("REQUIRE_HDL_TOOLS") == "1":
        pytest.fail(f"required HDL tool is missing: {name}")
    pytest.skip(f"HDL tool is not installed: {name}")


def _run(
    command: list[str], *, expected_success: bool = True
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=_tool_environment(),
        timeout=120,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if expected_success:
        assert completed.returncode == 0, output
    else:
        assert completed.returncode != 0, output
    return completed


def _assert_dut_guard_failure(
    completed: subprocess.CompletedProcess[str], marker: str
) -> None:
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0, output
    assert WATCHDOG_MARKER not in output, "testbench watchdog caused the failure"
    assert marker in output, f"missing exact DUT guard marker {marker}: {output}"


def _yosys_quote(path: Path) -> str:
    value = path.resolve().as_posix().replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _formal_output_directory(parent: Path, leaf: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", leaf) is None:
        raise ValueError(f"formal output leaf must be shell-safe: {leaf!r}")
    return parent / leaf


def _assert_fresh_nonempty_artifact(
    path: Path, invocation_started_ns: int, description: str
) -> None:
    assert path.is_file(), f"missing {description}: {path}"
    metadata = path.stat()
    assert metadata.st_size > 0, f"{description} is empty: {path}"
    assert metadata.st_mtime_ns >= invocation_started_ns, (
        f"{description} predates invocation: {path}"
    )


def _run_formal_task(
    task: str,
    work_directory: Path,
    *,
    expected_status: str,
    assertion_marker: str | None = None,
) -> None:
    assert not work_directory.exists(), (
        f"formal work directory already exists: {work_directory}"
    )
    sby = _required_tool("sby")
    _required_tool("yosys")
    _required_tool("boolector")
    invocation_started_ns = time.time_ns()
    completed = _run(
        [sby, "-f", "-d", str(work_directory), str(FORMAL_CONFIG), task],
        expected_success=expected_status == "PASS",
    )
    status_file = work_directory / "status"
    _assert_fresh_nonempty_artifact(status_file, invocation_started_ns, "formal status")
    status_fields = status_file.read_text(encoding="ascii").split()
    assert status_fields and status_fields[0] == expected_status, status_fields

    log_files = sorted(
        path
        for path in work_directory.rglob("*")
        if path.is_file() and ("logfile" in path.name or path.suffix == ".log")
    )
    assert log_files, "formal invocation created no log files"
    for log_file in log_files:
        _assert_fresh_nonempty_artifact(log_file, invocation_started_ns, "formal log")
    logs = "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in log_files
    )
    lowered = logs.lower()
    for forbidden in (
        "syntax error",
        "command not found",
        "no such file or directory",
        "timeout",
        "status: unknown",
        "setup failed",
        "unknown task",
        "invalid task",
        "no such task",
        "unrecognized task",
        "error:",
    ):
        assert forbidden not in lowered, (
            f"formal log contains forbidden marker {forbidden}:\n{logs}"
        )

    if assertion_marker is not None:
        assert completed.returncode != 0
        assert assertion_marker in logs
        for other_marker in FORMAL_FAILURE_MARKERS.values():
            if other_marker != assertion_marker:
                assert other_marker not in logs
        for suffix in (".vcd", ".yw", ".smtc"):
            traces = sorted(work_directory.rglob(f"trace*{suffix}"))
            assert traces, (
                f"mutation failure produced no {suffix} counterexample: {logs}"
            )
            for trace in traces:
                _assert_fresh_nonempty_artifact(
                    trace, invocation_started_ns, f"mutation {suffix} trace"
                )


def _run_formal_cover_task(work_directory: Path) -> None:
    _run_formal_task("cover", work_directory, expected_status="PASS")
    engine_log_path = work_directory / "engine_0" / "logfile.txt"
    assert engine_log_path.is_file()
    engine_log = engine_log_path.read_text(encoding="utf-8", errors="replace")
    reached_traces: dict[str, str] = {}
    for marker in FORMAL_COVER_MARKERS:
        match = re.search(
            rf"Reached cover statement in step \d+ at [^\r\n]+: {marker}\r?\n"
            rf"##[^\r\n]*Writing trace to VCD file: engine_0/(trace\d+\.vcd)",
            engine_log,
        )
        assert match is not None, f"cover was not reached: {marker}\n{engine_log}"
        reached_traces[marker] = match.group(1)

    assert len(set(reached_traces.values())) == len(FORMAL_COVER_MARKERS)
    for trace_name in reached_traces.values():
        trace_stem = Path(trace_name).stem
        for suffix in (".vcd", ".yw", ".smtc"):
            witness = work_directory / "engine_0" / f"{trace_stem}{suffix}"
            assert witness.is_file(), witness
            assert witness.stat().st_size > 0, witness


def _fake_formal_run(
    work_directory: Path,
    *,
    status: str,
    log: str,
    returncode: int,
    trace_payload: bytes | None = None,
    stale_timestamp: float | None = None,
):
    def run(
        command: list[str], *, expected_success: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del expected_success
        engine_directory = work_directory / "engine_0"
        engine_directory.mkdir(parents=True)
        artifacts = [
            work_directory / "status",
            work_directory / "logfile.txt",
            engine_directory / "logfile.txt",
        ]
        artifacts[0].write_text(status, encoding="ascii")
        artifacts[1].write_text(log, encoding="ascii")
        artifacts[2].write_text(log, encoding="ascii")
        if trace_payload is not None:
            for suffix in (".vcd", ".yw", ".smtc"):
                trace = engine_directory / f"trace{suffix}"
                trace.write_bytes(trace_payload)
                artifacts.append(trace)
        if stale_timestamp is not None:
            for artifact in artifacts:
                os.utime(artifact, (stale_timestamp, stale_timestamp))
        return subprocess.CompletedProcess(command, returncode, "", "")

    return run


def _pseudo_seed(source: int, destination: int, protocol: int, length: int) -> int:
    total = (
        (source >> 16)
        + (source & 0xFFFF)
        + (destination >> 16)
        + (destination & 0xFFFF)
        + protocol
        + length
    )
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total


def test_tool_discovery_uses_explicit_suite_root_without_home_fallback(
    tmp_path: Path,
) -> None:
    suite = tmp_path / "portable suite"
    binary = (
        suite / "bin" / ("verilator_bin.exe" if os.name == "nt" else "verilator_bin")
    )
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"tool")
    binary.chmod(0o755)
    environment = {"OSS_CAD_SUITE_ROOT": str(suite), "PATH": ""}

    assert Path(_tool("verilator", environ=environment) or "") == binary
    assert _suite_root({"PATH": ""}) is None


def test_tool_environment_changes_only_for_explicit_suite_root(tmp_path: Path) -> None:
    original = {"PATH": "original-path"}
    assert _tool_environment(original) == original

    suite = tmp_path / "suite with spaces"
    explicit = _tool_environment(
        {"PATH": "original-path", "OSS_CAD_SUITE_ROOT": str(suite)}
    )
    assert explicit["PATH"].split(os.pathsep)[:2] == [
        str(suite / "bin"),
        str(suite / "lib"),
    ]
    assert explicit["VERILATOR_ROOT"] == str(suite / "share" / "verilator")

    preserved = _tool_environment(
        {
            "PATH": "original-path",
            "OSS_CAD_SUITE_ROOT": str(suite),
            "VERILATOR_ROOT": "caller-selected-root",
        }
    )
    assert preserved["VERILATOR_ROOT"] == "caller-selected-root"


def test_required_tool_mode_fails_instead_of_skipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OSS_CAD_SUITE_ROOT", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("REQUIRE_HDL_TOOLS", "1")

    with pytest.raises(pytest.fail.Exception, match="required HDL tool is missing"):
        _required_tool("definitely-not-an-hdl-tool")


def test_formal_helper_refuses_preexisting_stale_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_directory = tmp_path / "seeded stale formal output"
    engine_directory = work_directory / "engine_0"
    engine_directory.mkdir(parents=True)
    (work_directory / "status").write_text("FAIL 2 0\n", encoding="ascii")
    (engine_directory / "logfile.txt").write_text(
        "Assert failed: ASSERT_ODD_PADDING_CONTROL\n", encoding="ascii"
    )
    (engine_directory / "trace.vcd").write_bytes(b"")
    (engine_directory / "trace.yw").write_bytes(b"stale witness")
    (engine_directory / "trace.smtc").write_bytes(b"stale constraints")
    invocation_attempted = False

    def forbidden_run(*args, **kwargs):
        del args, kwargs
        nonlocal invocation_attempted
        invocation_attempted = True
        raise AssertionError("SBY invocation should not occur")

    monkeypatch.setattr(f"{__name__}._required_tool", lambda name: name)
    monkeypatch.setattr(f"{__name__}._run", forbidden_run)
    with pytest.raises(AssertionError, match="formal work directory already exists"):
        _run_formal_task(
            "unknown_invalid_task",
            work_directory,
            expected_status="FAIL",
            assertion_marker="ASSERT_ODD_PADDING_CONTROL",
        )

    assert not invocation_attempted
    assert (work_directory / "status").read_text(encoding="ascii") == "FAIL 2 0\n"
    assert (engine_directory / "trace.vcd").stat().st_size == 0


def test_formal_helper_rejects_unknown_task_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_directory = tmp_path / "unknown task output"
    log = "ERROR: Unknown task\nAssert failed: ASSERT_ODD_PADDING_CONTROL\n"
    monkeypatch.setattr(f"{__name__}._required_tool", lambda name: name)
    monkeypatch.setattr(
        f"{__name__}._run",
        _fake_formal_run(
            work_directory,
            status="FAIL 0 0\n",
            log=log,
            returncode=2,
            trace_payload=b"current trace",
        ),
    )

    with pytest.raises(AssertionError, match="unknown task"):
        _run_formal_task(
            "unknown_invalid_task",
            work_directory,
            expected_status="FAIL",
            assertion_marker="ASSERT_ODD_PADDING_CONTROL",
        )


@pytest.mark.parametrize(
    ("log", "stale_timestamp", "message"),
    (("", None, "formal log is empty"), ("PASS\n", 1.0, "predates invocation")),
)
def test_formal_helper_rejects_empty_or_backdated_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log: str,
    stale_timestamp: float | None,
    message: str,
) -> None:
    work_directory = tmp_path / f"invalid artifacts {message}"
    monkeypatch.setattr(f"{__name__}._required_tool", lambda name: name)
    monkeypatch.setattr(
        f"{__name__}._run",
        _fake_formal_run(
            work_directory,
            status="PASS 0 0\n",
            log=log,
            returncode=0,
            stale_timestamp=stale_timestamp,
        ),
    )

    with pytest.raises(AssertionError, match=message):
        _run_formal_task("positive", work_directory, expected_status="PASS")


def test_task_five_files_exist_and_are_ascii() -> None:
    for path in RTL_FILES:
        assert path.is_file(), f"missing Task 5 artifact: {path.relative_to(ROOT)}"
        raw = path.read_bytes()
        assert raw
        raw.decode("ascii")


def test_checksum_core_has_complete_nonvacuous_streaming_structure() -> None:
    source = _source(CORE)
    required_fragments = (
        "module checksum16_stream",
        "parameter integer DATA_WIDTH = 64",
        "parameter integer KEEP_WIDTH = 8",
        "parameter integer LENGTH_WIDTH = 16",
        "input  logic                     request_valid",
        "output logic                     request_ready",
        "input  logic [DATA_WIDTH-1:0]    request_data",
        "input  logic [KEEP_WIDTH-1:0]    request_keep",
        "input  logic                     request_first",
        "input  logic                     request_last",
        "input  logic [15:0]              request_seed",
        "input  logic                     response_ready",
        "output logic                     response_valid",
        "output logic [2:0]               response_status",
        "output logic [15:0]              response_checksum",
        "output logic [15:0]              response_folded_sum",
        "output logic [LENGTH_WIDTH-1:0]  response_byte_length",
        "always_ff @(posedge clk)",
        "assign request_ready",
    )
    for fragment in required_fragments:
        assert fragment in source

    assert len(source.splitlines()) >= 180
    assert re.search(r"for\s*\([^)]*(?:lane|index)", source)
    assert re.search(r"request_keep\s*\+\s*1", source)
    assert "response_valid && !response_ready" in source
    assert "DATA_WIDTH != 64" in source
    assert "KEEP_WIDTH != 8" in source
    assert "LENGTH_WIDTH < 1" in source

    lowered = source.lower()
    for forbidden in (
        "even_length",
        "poisoned_partial",
        "stall_and_zero_bubble",
        "16'h9753",
        "16'h97cb",
        "xpm_",
        "altera_",
        "cyclone",
        "artix",
    ):
        assert forbidden not in lowered


def test_status_encoding_and_interface_contract_are_explicit() -> None:
    source = _source(CORE)
    expected = {
        "STATUS_SUCCESS": "3'd0",
        "STATUS_MISSING_FIRST": "3'd1",
        "STATUS_UNEXPECTED_FIRST": "3'd2",
        "STATUS_INVALID_KEEP": "3'd3",
        "STATUS_EMPTY_FINAL": "3'd4",
        "STATUS_LENGTH_OVERFLOW": "3'd5",
    }
    for name, value in expected.items():
        assert re.search(rf"{name}\s*=\s*{re.escape(value)}", source)

    for phrase in (
        "pre-edge",
        "post-edge",
        "ascending lane order",
        "active-low synchronous reset",
        "network byte order",
    ):
        assert phrase in source.lower()


def test_pseudo_header_seed_is_combinational_and_has_independent_vectors() -> None:
    source = _source(PSEUDO)
    bench = _source(TESTBENCH)
    assert "module ipv4_pseudo_header_seed" in source
    assert "always_comb" in source
    assert "source_ipv4[31:16]" in source
    assert "source_ipv4[15:0]" in source
    assert "destination_ipv4[31:16]" in source
    assert "destination_ipv4[15:0]" in source
    assert "{8'h00, protocol}" in source
    assert "transport_length" in source
    assert "always_ff" not in source

    examples = (
        (0xC0000201, 0xC6336402, 17, 32),
        (0x0A000001, 0x0A0000FE, 6, 1460),
        (0xFFFFFFFF, 0xFFFFFFFF, 255, 65535),
    )
    for source_ip, destination_ip, protocol, length in examples:
        expected = _pseudo_seed(source_ip, destination_ip, protocol, length)
        assert f"32'h{source_ip:08x}" in bench.lower()
        assert f"32'h{destination_ip:08x}" in bench.lower()
        assert f"16'h{expected:04x}" in bench.lower()


def test_monitor_is_simulation_only_and_checks_handshake_invariants() -> None:
    source = _source(MONITOR)
    bench = _source(TESTBENCH)
    assert "module checksum_stream_monitor" in source
    assert "`ifndef SYNTHESIS" in source
    assert "response_valid && !response_ready" in source
    assert "$fatal" in source
    assert "request_accepted" not in source
    assert "request_accepted" not in bench
    assert (
        "$isunknown({request_first, request_last, request_keep, request_data, "
        "request_seed})"
    ) in source
    assert (
        "$isunknown({response_status, response_checksum, response_folded_sum, "
        "response_byte_length})"
    ) in source
    for phrase in (
        "response changed while stalled",
        "request ready asserted while response stalled",
        "MONITOR_UNKNOWN_RESET_N",
        "MONITOR_UNKNOWN_REQUEST_VALID",
        "MONITOR_UNKNOWN_RESPONSE_READY",
        "MONITOR_UNKNOWN_REQUEST_PAYLOAD",
        "MONITOR_UNKNOWN_RESPONSE_VALID",
        "MONITOR_UNKNOWN_RESPONSE_PAYLOAD",
    ):
        assert phrase.lower() in source.lower()


def test_self_checking_bench_uses_generated_vectors_without_manual_duplicates() -> None:
    bench = _source(TESTBENCH)
    assert "module tb_checksum16_stream" in bench
    assert "$fatal" in bench
    assert "ALL CHECKSUM STREAM TESTS PASSED" in bench
    assert "LENGTH_WIDTH(16)" in bench
    assert "LENGTH_WIDTH(4)" in bench
    assert "checksum_stream_monitor" in bench
    assert "ipv4_pseudo_header_seed" in bench
    assert '`include "checksum_vectors.svh"' in bench
    assert "run_checksum_vectors_v1();" in bench
    assert "run_reset_stall_recovery" in bench
    assert "RESET_FIELD_RESPONSE_VALID" in bench
    assert "RESET_FIELD_STATUS" in bench
    assert "RESET_FIELD_CHECKSUM" in bench
    assert "RESET_FIELD_FOLDED_SUM" in bench
    assert "RESET_FIELD_BYTE_LENGTH" in bench

    canonical_values = (
        "ddccbbaa78563412",
        "e5d4c3b2a1563412",
        "a50e0d0c0b0a0908",
        "16'h9753",
        "16'h97cb",
        '"seeded_multibeat"',
        '"stall_and_zero_bubble"',
    )
    for value in canonical_values:
        assert value not in bench.lower()


def test_generated_vector_include_is_ascii_and_not_embedded_in_dut() -> None:
    include = _source(VECTOR_INCLUDE)
    assert include.startswith("// CHECKSUM_VECTOR_INCLUDE_FORMAT 1\n")
    assert "CANONICAL_TEXT_SHA256" in include
    assert "run_checksum_vectors_v1" in include
    assert "ddccbbaa78563412" in include
    assert "ddccbbaa78563412" not in _source(CORE).lower()


@pytest.mark.parametrize(
    ("injection", "marker"),
    (
        ("reset_n = 1'bx;", "MONITOR_UNKNOWN_RESET_N"),
        ("request_valid = 1'bx;", "MONITOR_UNKNOWN_REQUEST_VALID"),
        ("response_ready = 1'bx;", "MONITOR_UNKNOWN_RESPONSE_READY"),
        (
            "request_valid = 1'b1; request_ready = 1'b1; request_data = 'x;",
            "MONITOR_UNKNOWN_REQUEST_PAYLOAD",
        ),
        ("response_valid = 1'bx;", "MONITOR_UNKNOWN_RESPONSE_VALID"),
        (
            "response_valid = 1'b1; response_status = 'x;",
            "MONITOR_UNKNOWN_RESPONSE_PAYLOAD",
        ),
    ),
)
def test_monitor_rejects_unknown_state_in_simulation(
    tmp_path: Path, injection: str, marker: str
) -> None:
    iverilog = _required_tool("iverilog")
    vvp = _required_tool("vvp")
    bench = tmp_path / "tb_monitor_unknown.sv"
    bench.write_text(
        f"""`timescale 1ns/1ps
module tb_monitor_unknown;
  logic clk = 0;
  logic reset_n = 1;
  logic request_valid = 0;
  logic request_ready = 0;
  logic [63:0] request_data = 0;
  logic [7:0] request_keep = 0;
  logic request_first = 0;
  logic request_last = 0;
  logic [15:0] request_seed = 0;
  logic response_valid = 0;
  logic response_ready = 0;
  logic [2:0] response_status = 0;
  logic [15:0] response_checksum = 0;
  logic [15:0] response_folded_sum = 0;
  logic [15:0] response_byte_length = 0;
  always #1 clk = ~clk;
  checksum_stream_monitor monitor (.*);
  initial begin
    {injection}
    #6 $fatal(1, "MONITOR_INJECTION_WATCHDOG");
  end
endmodule
""",
        encoding="ascii",
    )
    executable = tmp_path / "monitor_unknown.vvp"
    _run(
        [
            iverilog,
            "-g2012",
            "-s",
            "tb_monitor_unknown",
            "-o",
            str(executable),
            str(MONITOR),
            str(bench),
        ]
    )
    completed = _run([vvp, str(executable)], expected_success=False)
    output = f"{completed.stdout}\n{completed.stderr}"
    assert marker in output
    assert "MONITOR_INJECTION_WATCHDOG" not in output


def test_invalid_parameter_bench_covers_each_guard() -> None:
    bench = _source(INVALID_TESTBENCH)
    core = _source(CORE)
    assert "module tb_invalid_parameters" in bench
    assert "parameter integer CASE_ID" in bench
    assert "DATA_WIDTH(32)" in bench
    assert "KEEP_WIDTH(4)" in bench
    assert "LENGTH_WIDTH(0)" in bench
    assert WATCHDOG_MARKER in bench
    for marker in GUARD_MARKERS:
        assert core.count(marker) == 1


def test_formal_mutation_parameters_are_isolated_and_default_off() -> None:
    source = _source(CORE)
    for parameter in ("FAULT_DROP_ODD_PAD", "FAULT_MUTATE_STALL"):
        assert re.search(rf"parameter bit {parameter}\s*=\s*1'b0", source)
        assert source.count(parameter) >= 2
    assert "Evidence-only mutation controls" in source


def test_formal_observation_ports_alias_actual_dut_packet_state() -> None:
    core = _source(CORE)
    harness = _source(FORMAL_HARNESS)
    assert "`ifdef FORMAL" in core
    for state in ("packet_active", "packet_sum", "packet_byte_length"):
        observed = f"formal_{state}"
        assert re.search(rf"output wire[^;]*\b{observed}\b", core)
        assert f"assign {observed} = {state};" in core
        assert f".{observed}(dut_{state})" in harness
        assert harness.count(f"dut_{state}") >= 5


def test_formal_harness_wraps_real_dut_and_names_required_properties() -> None:
    source = _source(FORMAL_HARNESS)
    assert source.count("module checksum16_stream_formal") == 1
    assert source.count("checksum16_stream #(") == 1
    assert "(* gclk *)" in source
    assert source.count("(* anyseq *)") >= 8
    assert "assume(!reset_n)" in source
    assert "assume(reset_n)" not in source
    assert source.count("assume(") == 1
    assert "LENGTH_WIDTH(4)" in source
    assert "checksum_vectors" not in source

    assertions = (
        "ASSERT_REQUEST_READY",
        "ASSERT_STALL_RESPONSE",
        "ASSERT_ACCEPTED_STATE_STABLE",
        "ASSERT_RESET_CLEARS",
        "ASSERT_STATUS_RANGE",
        "ASSERT_SUCCESS_COMPLEMENT",
        "ASSERT_ERROR_ZERO_FIELDS",
        "ASSERT_RESULT_LENGTH",
        "ASSERT_ERROR_TERMINATES",
        "ASSERT_ACCEPTED_TRANSITION",
        "ASSERT_DUT_SHADOW_EQUIVALENCE",
        "ASSERT_DUT_STATE_STABLE",
        "ASSERT_DUT_RESET_CLEARS",
        "ASSERT_DUT_INACTIVE_NORMALIZED",
        "ASSERT_DUT_NONFINAL_TRANSITION",
        "ASSERT_DUT_SUCCESS_CLEARS",
        "ASSERT_DUT_ERROR_CLEARS",
        "ASSERT_ODD_PADDING_CONTROL",
        "ASSERT_STALL_STABILITY_CONTROL",
    )
    for assertion in assertions:
        assert assertion in source

    for cover in FORMAL_COVER_MARKERS:
        assert cover in source


def test_sby_config_exposes_exact_proof_and_mutation_tasks() -> None:
    config = _source(FORMAL_CONFIG)
    assert "[tasks]" in config
    for task in ("positive", "cover", "odd_padding_mutation", "stall_state_mutation"):
        assert re.search(rf"(?m)^{task}$", config)
    assert "positive: mode bmc" in config
    assert "positive: depth 20" in config
    assert "cover: mode cover" in config
    assert "cover: depth 20" in config
    assert "odd_padding_mutation: mode bmc" in config
    assert "stall_state_mutation: mode bmc" in config
    assert "smtbmc boolector" in config
    assert "FORMAL_ODD_CONTROL" in config
    assert "FORMAL_STALL_CONTROL" in config
    assert "chparam -set FAULT_DROP_ODD_PAD 1" in config
    assert "chparam -set FAULT_MUTATE_STALL 1" in config
    assert "prep -top checksum16_stream_formal" in config


def test_formal_evidence_wording_is_bounded_and_depth_pinned() -> None:
    specification = _source(DESIGN_SPECIFICATION)
    assert "bounded depth-20 formal proof" in specification
    assert "does not claim an unbounded formal proof" in specification
    assert "cover depth 20" in specification


def test_formal_output_directory_requires_a_shell_safe_leaf(tmp_path: Path) -> None:
    assert _formal_output_directory(tmp_path, "positive_proof_output") == (
        tmp_path / "positive_proof_output"
    )
    for unsafe_leaf in ("positive proof output", "../escape", "", "bad/name"):
        with pytest.raises(ValueError, match="shell-safe"):
            _formal_output_directory(tmp_path, unsafe_leaf)


def test_sby_positive_proof_passes(tmp_path: Path) -> None:
    _run_formal_task(
        "positive",
        _formal_output_directory(tmp_path, "positive_proof_output"),
        expected_status="PASS",
    )


def test_sby_cover_reaches_every_required_statement(tmp_path: Path) -> None:
    _run_formal_cover_task(_formal_output_directory(tmp_path, "formal_cover_output"))


@pytest.mark.parametrize(("task", "assertion_marker"), FORMAL_FAILURE_MARKERS.items())
def test_sby_mutation_fails_only_targeted_assertion(
    tmp_path: Path, task: str, assertion_marker: str
) -> None:
    _run_formal_task(
        task,
        _formal_output_directory(tmp_path, f"{task}_counterexample_output"),
        expected_status="FAIL",
        assertion_marker=assertion_marker,
    )


def test_verilator_lint_when_available() -> None:
    verilator = _required_tool("verilator")
    _run(
        [
            verilator,
            "--lint-only",
            "--timing",
            "-Wall",
            "-Wno-DECLFILENAME",
            f"-I{VECTOR_INCLUDE.parent}",
            "--top-module",
            "tb_checksum16_stream",
            *(str(path) for path in (CORE, PSEUDO, MONITOR, TESTBENCH)),
        ]
    )


def test_icarus_vector_simulation_when_available(tmp_path: Path) -> None:
    iverilog = _required_tool("iverilog")
    vvp = _required_tool("vvp")
    executable = tmp_path / "checksum_stream.vvp"
    _run(
        [
            iverilog,
            "-g2012",
            "-s",
            "tb_checksum16_stream",
            "-I",
            str(VECTOR_INCLUDE.parent),
            "-o",
            str(executable),
            *(str(path) for path in (CORE, PSEUDO, MONITOR, TESTBENCH)),
        ]
    )
    completed = _run([vvp, str(executable)])
    assert "ALL CHECKSUM STREAM TESTS PASSED" in completed.stdout


@pytest.mark.parametrize("case_id", range(3))
def test_icarus_invalid_parameters_fail_when_available(
    tmp_path: Path, case_id: int
) -> None:
    iverilog = _required_tool("iverilog")
    vvp = _required_tool("vvp")
    executable = tmp_path / f"invalid_{case_id}.vvp"
    _run(
        [
            iverilog,
            "-g2012",
            "-s",
            "tb_invalid_parameters",
            f"-Ptb_invalid_parameters.CASE_ID={case_id}",
            "-o",
            str(executable),
            str(CORE),
            str(INVALID_TESTBENCH),
        ]
    )
    completed = _run([vvp, str(executable)], expected_success=False)
    _assert_dut_guard_failure(completed, GUARD_MARKERS[case_id])


def test_invalid_parameter_watchdog_cannot_masquerade_as_dut_guard(
    tmp_path: Path,
) -> None:
    iverilog = _required_tool("iverilog")
    vvp = _required_tool("vvp")
    original = _source(CORE)
    guard = '$fatal(1, "DUT_GUARD_DATA_WIDTH");'
    assert guard in original
    mutated_core = tmp_path / "checksum16_stream.sv"
    mutated_core.write_text(original.replace(guard, ";", 1), encoding="ascii")
    executable = tmp_path / "guard_bypassed.vvp"
    _run(
        [
            iverilog,
            "-g2012",
            "-s",
            "tb_invalid_parameters",
            "-Ptb_invalid_parameters.CASE_ID=0",
            "-o",
            str(executable),
            str(mutated_core),
            str(INVALID_TESTBENCH),
        ]
    )
    completed = _run([vvp, str(executable)], expected_success=False)
    with pytest.raises(AssertionError, match="watchdog caused the failure"):
        _assert_dut_guard_failure(completed, GUARD_MARKERS[0])


def test_yosys_synthesizes_nonempty_default_core_when_available(tmp_path: Path) -> None:
    yosys = _required_tool("yosys")
    source_directory = tmp_path / "project sources with spaces"
    output_directory = tmp_path / "tool output with spaces"
    source_directory.mkdir()
    output_directory.mkdir()
    copied_core = source_directory / CORE.name
    copied_pseudo = source_directory / PSEUDO.name
    shutil.copyfile(CORE, copied_core)
    shutil.copyfile(PSEUDO, copied_pseudo)
    netlist = output_directory / "checksum stream.json"
    script = "; ".join(
        (
            "read_verilog -sv "
            f"{_yosys_quote(copied_core)} {_yosys_quote(copied_pseudo)}",
            "hierarchy -check -top checksum16_stream",
            "proc",
            "opt",
            "check -assert",
            "stat",
            f"write_json {_yosys_quote(netlist)}",
        )
    )
    completed = _run([yosys, "-q", "-p", script])
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    assert "latch inferred" not in output
    assert "unsupported" not in output

    design = json.loads(netlist.read_text(encoding="utf-8"))
    module = design["modules"]["checksum16_stream"]
    assert len(module["cells"]) >= 10
    assert len(module["netnames"]) >= 20
