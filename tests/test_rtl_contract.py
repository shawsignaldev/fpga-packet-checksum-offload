from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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

RTL_FILES = (CORE, PSEUDO, MONITOR, TESTBENCH, INVALID_TESTBENCH)
GUARD_MARKERS = (
    "DUT_GUARD_DATA_WIDTH",
    "DUT_GUARD_KEEP_WIDTH",
    "DUT_GUARD_LENGTH_WIDTH",
)
WATCHDOG_MARKER = "TB_WATCHDOG_PARAMETER_GUARD_MISSING"


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
        aliases = ("verilator_bin", "verilator") if os.name == "nt" else (
            "verilator",
            "verilator_bin",
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
    binary = suite / "bin" / ("verilator_bin.exe" if os.name == "nt" else "verilator_bin")
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


def test_required_tool_mode_fails_instead_of_skipping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSS_CAD_SUITE_ROOT", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("REQUIRE_HDL_TOOLS", "1")

    with pytest.raises(pytest.fail.Exception, match="required HDL tool is missing"):
        _required_tool("definitely-not-an-hdl-tool")


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
            f"read_verilog -sv {_yosys_quote(copied_core)} {_yosys_quote(copied_pseudo)}",
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
