from __future__ import annotations

import argparse
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
            byte << (lane * 8)
            for lane, byte in enumerate(valid_bytes + poison_bytes)
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
            (CycleInput(_beat(bytes.fromhex("12345678"), first=True, last=True), False),),
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
            (CycleInput(_beat(bytes.fromhex("ffffffff"), first=True, last=True), False),),
        ),
        VectorCase(
            "seeded_multibeat",
            16,
            (
                CycleInput(_beat(bytes(range(1, 9)), first=True, last=False, seed=0x1234), False),
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
                CycleInput(_beat(bytes.fromhex("cafe01"), first=True, last=True), False),
            ),
        ),
        VectorCase(
            "stall_and_zero_bubble",
            16,
            (
                CycleInput(_beat(bytes.fromhex("1234"), first=True, last=True), False),
                CycleInput(None, False),
                CycleInput(None, False),
                CycleInput(_beat(bytes.fromhex("56789a"), first=True, last=True, seed=7), True),
                CycleInput(None, True),
            ),
        ),
    )


def render_vectors() -> str:
    lines = [
        "VECTOR_FORMAT checksum16_stream_cycle 1",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate checksum stream RTL vectors")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the output already matches without rewriting it",
    )
    arguments = parser.parse_args(argv)
    expected = render_vectors().encode("ascii")
    if arguments.check:
        try:
            current = arguments.output.read_bytes()
        except FileNotFoundError:
            print(f"vector file missing: {arguments.output}", file=sys.stderr)
            return 1
        except OSError as error:
            print(f"cannot read vector file: {error}", file=sys.stderr)
            return 1
        if current != expected:
            print(f"vector file stale: {arguments.output}", file=sys.stderr)
            return 1
        return 0

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
