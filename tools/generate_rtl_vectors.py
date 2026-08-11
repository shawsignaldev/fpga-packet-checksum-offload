from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

cycle_model = import_module("fpga_packet_checksum_offload.cycle_model")
ChecksumCycleModel = cycle_model.ChecksumCycleModel
StreamBeat = cycle_model.StreamBeat
DEFAULT_OUTPUT = REPOSITORY_ROOT / "tests" / "vectors" / "checksum_vectors.txt"
DEFAULT_SV_OUTPUT = REPOSITORY_ROOT / "tests" / "vectors" / "checksum_vectors.svh"

_VECTOR_HEADER = "VECTOR_FORMAT checksum16_stream_cycle 1"
_EXPECTED_FIELDS = (
    "FIELDS case_cycle reset_n request_valid data keep first last seed "
    "response_ready request_ready request_accepted response_valid status "
    "status_name checksum folded_sum byte_length"
)
_CASE_PATTERN = re.compile(r"CASE ([a-z0-9_]+) LENGTH_WIDTH ([1-9][0-9]*)")
_HEX_PATTERN = re.compile(r"[0-9a-f]+")


@dataclass(frozen=True, slots=True)
class CycleInput:
    beat: StreamBeat | None
    response_ready: bool
    reset_n: bool = True


@dataclass(frozen=True, slots=True)
class VectorCase:
    name: str
    length_width: int
    cycles: tuple[CycleInput, ...]


def _beat(chunk: bytes, *, first: bool, last: bool, seed: int = 0) -> StreamBeat:
    return StreamBeat(
        data=sum(byte << (lane * 8) for lane, byte in enumerate(chunk)),
        keep=(1 << len(chunk)) - 1,
        first=first,
        last=last,
        seed=seed,
    )


def _poisoned_beat(
    valid_bytes: bytes,
    poison_bytes: bytes,
    *,
    first: bool,
    last: bool,
    seed: int = 0,
) -> StreamBeat:
    if not valid_bytes or len(valid_bytes) + len(poison_bytes) != 8:
        raise ValueError("poisoned beat must contain eight lanes and valid bytes")
    return StreamBeat(
        data=sum(
            byte << (lane * 8) for lane, byte in enumerate(valid_bytes + poison_bytes)
        ),
        keep=(1 << len(valid_bytes)) - 1,
        first=first,
        last=last,
        seed=seed,
    )


def _cases() -> tuple[VectorCase, ...]:
    return (
        VectorCase(
            "even_length",
            16,
            (
                CycleInput(
                    _beat(bytes.fromhex("12345678"), first=True, last=True), False
                ),
            ),
        ),
        VectorCase(
            "odd_length",
            16,
            (CycleInput(_beat(bytes.fromhex("123456"), first=True, last=True), False),),
        ),
        VectorCase(
            "poisoned_partial_even",
            16,
            (
                CycleInput(
                    _poisoned_beat(
                        bytes.fromhex("12345678"),
                        bytes.fromhex("aabbccdd"),
                        first=True,
                        last=True,
                    ),
                    False,
                ),
            ),
        ),
        VectorCase(
            "poisoned_partial_odd",
            16,
            (
                CycleInput(
                    _poisoned_beat(
                        bytes.fromhex("123456"),
                        bytes.fromhex("a1b2c3d4e5"),
                        first=True,
                        last=True,
                    ),
                    False,
                ),
            ),
        ),
        VectorCase(
            "carry_folding",
            16,
            (
                CycleInput(
                    _beat(bytes.fromhex("ffffffff"), first=True, last=True), False
                ),
            ),
        ),
        VectorCase(
            "seeded_multibeat",
            16,
            (
                CycleInput(
                    _beat(bytes(range(1, 9)), first=True, last=False, seed=0x1234),
                    False,
                ),
                CycleInput(_beat(bytes(range(9, 14)), first=False, last=True), False),
            ),
        ),
        VectorCase(
            "missing_first",
            16,
            (CycleInput(StreamBeat(0x12, 0x01, False, True), False),),
        ),
        VectorCase(
            "unexpected_first",
            16,
            (
                CycleInput(_beat(bytes(range(8)), first=True, last=False), False),
                CycleInput(_beat(b"\x08", first=True, last=True), False),
            ),
        ),
        VectorCase(
            "invalid_nonfinal_keep",
            16,
            (CycleInput(StreamBeat(0, 0x7F, True, False), False),),
        ),
        VectorCase(
            "sparse_final_keep",
            16,
            (CycleInput(StreamBeat(0, 0x55, True, True), False),),
        ),
        VectorCase(
            "empty_final",
            16,
            (CycleInput(StreamBeat(0, 0, True, True), False),),
        ),
        VectorCase(
            "length_boundary_success",
            4,
            (
                CycleInput(_beat(bytes(range(8)), first=True, last=False), False),
                CycleInput(
                    _poisoned_beat(
                        bytes(range(8, 15)),
                        b"\xa5",
                        first=False,
                        last=True,
                    ),
                    False,
                ),
            ),
        ),
        VectorCase(
            "length_boundary_overflow",
            4,
            (
                CycleInput(_beat(bytes(range(8)), first=True, last=False), False),
                CycleInput(_beat(bytes(range(8, 16)), first=False, last=True), False),
            ),
        ),
        VectorCase(
            "reset_recovery",
            16,
            (
                CycleInput(_beat(bytes(range(8)), first=True, last=False), False),
                CycleInput(None, False, reset_n=False),
                CycleInput(
                    _beat(bytes.fromhex("cafe01"), first=True, last=True), False
                ),
            ),
        ),
        VectorCase(
            "stall_and_zero_bubble",
            16,
            (
                CycleInput(_beat(bytes.fromhex("1234"), first=True, last=True), False),
                CycleInput(None, False),
                CycleInput(None, False),
                CycleInput(
                    _beat(bytes.fromhex("56789a"), first=True, last=True, seed=7), True
                ),
                CycleInput(None, True),
            ),
        ),
    )


def render_vectors() -> str:
    lines = [
        _VECTOR_HEADER,
        "SEMANTICS request_ready_request_accepted_pre_edge response_fields_post_edge",
        (
            "FIELDS case_cycle reset_n request_valid data keep first last seed "
            "response_ready request_ready request_accepted response_valid status "
            "status_name checksum folded_sum byte_length"
        ),
    ]

    for case in _cases():
        lines.append(f"CASE {case.name} LENGTH_WIDTH {case.length_width}")
        model = ChecksumCycleModel(length_width=case.length_width)
        for cycle_index, inputs in enumerate(case.cycles):
            beat = inputs.beat
            observation = model.step(
                beat,
                response_ready=inputs.response_ready,
                reset_n=inputs.reset_n,
            )
            if beat is None:
                data = 0
                keep = 0
                first = False
                last = False
                seed = 0
            else:
                data = beat.data
                keep = beat.keep
                first = beat.first
                last = beat.last
                seed = beat.seed

            if observation.result is None:
                result_fields = "- NONE ---- ---- -"
            else:
                result = observation.result
                result_fields = (
                    f"{int(result.status)} {result.status.name} "
                    f"{result.checksum:04x} {result.folded_sum:04x} "
                    f"{result.byte_length}"
                )

            lines.append(
                " ".join(
                    (
                        "CYCLE",
                        str(cycle_index),
                        str(int(inputs.reset_n)),
                        str(int(beat is not None)),
                        f"{data:016x}",
                        f"{keep:02x}",
                        str(int(first)),
                        str(int(last)),
                        f"{seed:04x}",
                        str(int(inputs.response_ready)),
                        str(int(observation.request_ready)),
                        str(int(observation.request_accepted)),
                        str(int(observation.response_valid)),
                        result_fields,
                    )
                )
            )
        lines.append("END_CASE")

    return "\n".join(lines) + "\n"


def _logic(value: str, name: str) -> str:
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be zero or one")
    return value


def _hex(value: str, digits: int, name: str) -> str:
    if len(value) != digits or _HEX_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must contain exactly {digits} lowercase hex digits")
    return value


def _render_cycle_call(
    case_name: str,
    length_width: int,
    fields: list[str],
) -> str:
    if len(fields) != 18 or fields[0] != "CYCLE":
        raise ValueError(f"malformed cycle in case {case_name}")

    cycle = int(fields[1], 10)
    reset_n = _logic(fields[2], "reset_n")
    request_valid = _logic(fields[3], "request_valid")
    data = _hex(fields[4], 16, "data")
    keep = _hex(fields[5], 2, "keep")
    first = _logic(fields[6], "first")
    last = _logic(fields[7], "last")
    seed = _hex(fields[8], 4, "seed")
    response_ready = _logic(fields[9], "response_ready")
    request_ready = _logic(fields[10], "request_ready")
    request_accepted = _logic(fields[11], "request_accepted")
    response_valid = _logic(fields[12], "response_valid")

    if response_valid == "1":
        status = int(fields[13], 10)
        if status not in range(6):
            raise ValueError(f"unsupported status in case {case_name}")
        if fields[14] == "NONE":
            raise ValueError(f"valid response lacks status name in case {case_name}")
        checksum = _hex(fields[15], 4, "checksum")
        folded_sum = _hex(fields[16], 4, "folded_sum")
        byte_length = int(fields[17], 10)
    else:
        if fields[13:] != ["-", "NONE", "----", "----", "-"]:
            raise ValueError(f"invalid empty response fields in case {case_name}")
        status = 0
        checksum = "0000"
        folded_sum = "0000"
        byte_length = 0

    if byte_length < 0 or byte_length >= (1 << length_width):
        raise ValueError(f"byte length does not fit case {case_name}")

    task_name = "vector_cycle16" if length_width == 16 else "vector_cycle4"
    length_literal = f"16'd{byte_length}" if length_width == 16 else f"4'd{byte_length}"
    return (
        f'        {task_name}("{case_name}", {cycle}, {reset_n}, '
        f"{request_valid}, 64'h{data}, 8'h{keep}, {first}, {last}, "
        f"16'h{seed}, {response_ready}, {request_ready}, {request_accepted}, "
        f"{response_valid}, 3'd{status}, 16'h{checksum}, 16'h{folded_sum}, "
        f"{length_literal});"
    )


def render_sv_include(vector_text: str) -> str:
    """Parse canonical text vectors into the sole SystemVerilog call sequence."""

    if not vector_text.endswith("\n"):
        raise ValueError("canonical vectors must end with one newline")
    lines = vector_text.splitlines()
    if len(lines) < 4 or lines[0] != _VECTOR_HEADER:
        raise ValueError("unsupported vector format")
    if not lines[1].startswith("SEMANTICS "):
        raise ValueError("missing vector semantics")
    if lines[2] != _EXPECTED_FIELDS:
        raise ValueError("unsupported vector fields")

    digest = hashlib.sha256(vector_text.encode("ascii")).hexdigest()
    rendered = [
        "// CHECKSUM_VECTOR_INCLUDE_FORMAT 1",
        f"// CANONICAL_TEXT_SHA256 {digest}",
        "// Generated from checksum_vectors.txt; do not edit by hand.",
        "task automatic run_checksum_vectors_v1;",
        "    begin",
    ]
    active_case: tuple[str, int] | None = None
    case_count = 0
    cycle_count = 0

    for line in lines[3:]:
        case_match = _CASE_PATTERN.fullmatch(line)
        if case_match is not None:
            if active_case is not None:
                raise ValueError("nested vector case")
            case_name = case_match.group(1)
            length_width = int(case_match.group(2), 10)
            if length_width not in {4, 16}:
                raise ValueError(f"unsupported LENGTH_WIDTH for case {case_name}")
            active_case = (case_name, length_width)
            reset_task = "vector_reset16" if length_width == 16 else "vector_reset4"
            rendered.append(f"        {reset_task}();")
            case_count += 1
            continue

        if line == "END_CASE":
            if active_case is None:
                raise ValueError("END_CASE without CASE")
            active_case = None
            continue

        if active_case is None:
            raise ValueError(f"unexpected vector line: {line}")
        rendered.append(_render_cycle_call(*active_case, line.split()))
        cycle_count += 1

    if active_case is not None:
        raise ValueError("unterminated vector case")
    if case_count == 0 or cycle_count == 0:
        raise ValueError("canonical vectors contain no cases")
    rendered.extend(("    end", "endtask", ""))
    return "\n".join(rendered)


def _read_checked(path: Path, expected: bytes, label: str) -> bool:
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        print(f"{label} file missing: {path}", file=sys.stderr)
        return False
    except OSError as error:
        print(f"cannot read {label} file: {error}", file=sys.stderr)
        return False
    if current != expected:
        print(f"{label} file stale: {path}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate checksum stream RTL vectors")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sv-output", type=Path, default=DEFAULT_SV_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the output already matches without rewriting it",
    )
    arguments = parser.parse_args(argv)
    vector_text = render_vectors()
    expected = vector_text.encode("ascii")
    expected_sv = render_sv_include(vector_text).encode("ascii")
    if arguments.check:
        try:
            current_text = arguments.output.read_bytes()
        except FileNotFoundError:
            print(f"vector file missing: {arguments.output}", file=sys.stderr)
            current_text = None
            text_current = False
        except OSError as error:
            print(f"cannot read vector file: {error}", file=sys.stderr)
            current_text = None
            text_current = False
        else:
            text_current = current_text == expected
            if not text_current:
                print(f"vector file stale: {arguments.output}", file=sys.stderr)

        if current_text is None:
            sv_current = _read_checked(arguments.sv_output, expected_sv, "SV include")
        else:
            try:
                disk_sv_expected = render_sv_include(
                    current_text.decode("ascii")
                ).encode("ascii")
            except (UnicodeDecodeError, ValueError) as error:
                print(
                    f"SV include file stale: {arguments.sv_output} "
                    f"(invalid vector source: {error})",
                    file=sys.stderr,
                )
                sv_current = False
            else:
                sv_current = _read_checked(
                    arguments.sv_output, disk_sv_expected, "SV include"
                )
        return 0 if text_current and sv_current else 1

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(expected)
    arguments.sv_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.sv_output.write_bytes(expected_sv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
