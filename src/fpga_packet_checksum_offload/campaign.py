from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fpga_packet_checksum_offload.cycle_model import (
    ChecksumCycleModel,
    StreamBeat,
    StreamResult,
    StreamStatus,
)


class CampaignVerificationError(RuntimeError):
    """Raised when a deterministic campaign condition does not hold."""


def _verify(condition: bool, message: str) -> None:
    if not condition:
        raise CampaignVerificationError(message)


def _verify_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise CampaignVerificationError(message)


@dataclass(frozen=True, slots=True)
class CampaignCase:
    name: str
    behavior_names: tuple[str, ...]
    passed: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise ValueError("name must be a non-empty string")
        if type(self.behavior_names) is not tuple or not self.behavior_names:
            raise ValueError("behavior_names must be a non-empty tuple")
        if any(type(name) is not str or not name for name in self.behavior_names):
            raise ValueError("behavior names must be non-empty strings")
        if type(self.passed) is not bool:
            raise TypeError("passed must be a boolean")
        if type(self.detail) is not str:
            raise TypeError("detail must be a string")


@dataclass(frozen=True, slots=True)
class CampaignResult:
    cases: tuple[CampaignCase, ...]
    total: int
    passed: int
    failed_case_names: tuple[str, ...]
    covered_behavior_names: tuple[str, ...]
    summary: str

    def __post_init__(self) -> None:
        if type(self.cases) is not tuple or any(
            not isinstance(case, CampaignCase) for case in self.cases
        ):
            raise TypeError("cases must be a tuple of CampaignCase records")
        if self.total != len(self.cases):
            raise ValueError("total must match the number of cases")
        if self.passed != sum(case.passed for case in self.cases):
            raise ValueError("passed must match the passing case count")
        expected_failures = tuple(case.name for case in self.cases if not case.passed)
        if self.failed_case_names != expected_failures:
            raise ValueError("failed_case_names must match failing cases")
        expected_coverage = tuple(
            sorted(
                {
                    behavior
                    for case in self.cases
                    for behavior in case.behavior_names
                }
            )
        )
        if self.covered_behavior_names != expected_coverage:
            raise ValueError("covered_behavior_names must match case coverage")
        if type(self.summary) is not str or not self.summary:
            raise ValueError("summary must be a non-empty string")


def _fold(total: int) -> int:
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total


def _expected(data: bytes, seed: int = 0) -> StreamResult:
    total = seed
    for offset in range(0, len(data), 2):
        second = data[offset + 1] if offset + 1 < len(data) else 0
        total += (data[offset] << 8) | second
    folded_sum = _fold(total)
    return StreamResult(
        StreamStatus.SUCCESS,
        (~folded_sum) & 0xFFFF,
        folded_sum,
        len(data),
    )


def _beat(chunk: bytes, *, first: bool, last: bool, seed: int = 0) -> StreamBeat:
    return StreamBeat(
        data=sum(byte << (lane * 8) for lane, byte in enumerate(chunk)),
        keep=(1 << len(chunk)) - 1,
        first=first,
        last=last,
        seed=seed,
    )


def _beats(data: bytes, seed: int = 0) -> tuple[StreamBeat, ...]:
    _verify(bool(data), "packet data must not be empty")
    return tuple(
        _beat(
            data[offset : offset + 8],
            first=offset == 0,
            last=offset + 8 >= len(data),
            seed=seed if offset == 0 else 0,
        )
        for offset in range(0, len(data), 8)
    )


def _packet_result(
    data: bytes,
    seed: int = 0,
    *,
    length_width: int = 16,
) -> StreamResult:
    model = ChecksumCycleModel(length_width=length_width)
    observation = None
    for beat_index, beat in enumerate(_beats(data, seed)):
        observation = model.step(beat, response_ready=False)
        _verify(
            observation.request_accepted,
            f"packet beat {beat_index} was not accepted",
        )
    if observation is None or observation.result is None:
        raise CampaignVerificationError("packet did not produce a result")
    return observation.result


def _even_length() -> None:
    data = bytes.fromhex("12345678")
    _verify_equal(_packet_result(data), _expected(data), "even-length result mismatch")


def _odd_length() -> None:
    data = bytes.fromhex("123456")
    _verify_equal(_packet_result(data), _expected(data), "odd-length result mismatch")


def _carry_folding() -> None:
    data = bytes.fromhex("ffffffff")
    result = _packet_result(data)
    _verify_equal(result, _expected(data), "carry-folding result mismatch")
    _verify_equal(result.folded_sum, 0xFFFF, "carry folding did not repeat")


def _seeded_sum() -> None:
    data = bytes.fromhex("000100020003")
    _verify_equal(
        _packet_result(data, seed=0xBEEF),
        _expected(data, seed=0xBEEF),
        "seeded result mismatch",
    )


def _multibeat_pairing() -> None:
    data = bytes(range(1, 22))
    _verify_equal(len(_beats(data)), 3, "multibeat fixture does not contain three beats")
    _verify_equal(
        _packet_result(data, seed=0x1234),
        _expected(data, seed=0x1234),
        "multibeat result mismatch",
    )


def _missing_first() -> None:
    model = ChecksumCycleModel()
    observation = model.step(
        StreamBeat(0, 0x01, False, True),
        response_ready=False,
    )
    _verify_equal(
        observation.result,
        StreamResult(StreamStatus.MISSING_FIRST, 0, 0, 0),
        "missing-first status mismatch",
    )


def _unexpected_first() -> None:
    model = ChecksumCycleModel()
    model.step(_beat(bytes(range(8)), first=True, last=False), response_ready=False)
    observation = model.step(
        _beat(b"\x08", first=True, last=True),
        response_ready=False,
    )
    _verify_equal(
        observation.result,
        StreamResult(StreamStatus.UNEXPECTED_FIRST, 0, 0, 8),
        "unexpected-first status mismatch",
    )


def _invalid_nonfinal_keep() -> None:
    model = ChecksumCycleModel()
    observation = model.step(
        StreamBeat(0, 0x7F, True, False),
        response_ready=False,
    )
    _verify_equal(
        observation.result,
        StreamResult(StreamStatus.INVALID_KEEP, 0, 0, 0),
        "nonfinal keep status mismatch",
    )


def _sparse_final_keep() -> None:
    model = ChecksumCycleModel()
    observation = model.step(
        StreamBeat(0, 0x55, True, True),
        response_ready=False,
    )
    _verify_equal(
        observation.result,
        StreamResult(StreamStatus.INVALID_KEEP, 0, 0, 0),
        "sparse final keep status mismatch",
    )


def _empty_final() -> None:
    model = ChecksumCycleModel()
    observation = model.step(
        StreamBeat(0, 0, True, True),
        response_ready=False,
    )
    _verify_equal(
        observation.result,
        StreamResult(StreamStatus.EMPTY_FINAL, 0, 0, 0),
        "empty-final status mismatch",
    )


def _length_overflow() -> None:
    boundary_data = bytes(range(15))
    _verify_equal(
        _packet_result(boundary_data, length_width=4),
        _expected(boundary_data),
        "length boundary did not accept fifteen bytes",
    )

    model = ChecksumCycleModel(length_width=4)
    model.step(_beat(bytes(range(8)), first=True, last=False), response_ready=False)
    observation = model.step(
        _beat(bytes(range(8, 16)), first=False, last=True),
        response_ready=False,
    )
    _verify_equal(
        observation.result,
        StreamResult(StreamStatus.LENGTH_OVERFLOW, 0, 0, 8),
        "length overflow did not reject sixteen bytes",
    )


def _reset_recovery() -> None:
    model = ChecksumCycleModel()
    model.step(_beat(bytes(range(8)), first=True, last=False), response_ready=False)
    model.step(None, response_ready=False, reset_n=False)
    data = bytes.fromhex("cafe01")
    observation = model.step(_beats(data)[0], response_ready=False)
    _verify_equal(observation.result, _expected(data), "reset recovery result mismatch")


def _stalled_output_stability() -> None:
    data = bytes.fromhex("0123456789")
    model = ChecksumCycleModel()
    result = model.step(_beats(data)[0], response_ready=False).result
    _verify_equal(result, _expected(data), "stalled output initial result mismatch")
    for stall_cycle in range(8):
        observation = model.step(None, response_ready=False)
        _verify(
            not observation.request_ready,
            f"stalled output asserted request_ready on cycle {stall_cycle}",
        )
        _verify_equal(
            observation.result,
            result,
            f"stalled output changed on cycle {stall_cycle}",
        )


def _zero_bubble_replacement() -> None:
    first_data = bytes.fromhex("1234")
    second_data = bytes.fromhex("56789a")
    model = ChecksumCycleModel()
    first = model.step(_beats(first_data)[0], response_ready=False)
    _verify_equal(first.result, _expected(first_data), "initial result mismatch")
    replacement = model.step(_beats(second_data, seed=7)[0], response_ready=True)
    _verify(replacement.request_accepted, "replacement request was not accepted")
    _verify(replacement.response_valid, "replacement response was not valid")
    _verify_equal(
        replacement.result,
        _expected(second_data, seed=7),
        "replacement result mismatch",
    )


_CASES: tuple[tuple[str, tuple[str, ...], Callable[[], None]], ...] = (
    ("even_length", ("even-length",), _even_length),
    ("odd_length", ("odd-length",), _odd_length),
    ("carry_folding", ("carry-folding",), _carry_folding),
    ("seeded_sum", ("seeded-sum",), _seeded_sum),
    ("multibeat_pairing", ("multibeat-pairing",), _multibeat_pairing),
    ("missing_first", ("missing-first",), _missing_first),
    ("unexpected_first", ("unexpected-first",), _unexpected_first),
    (
        "invalid_nonfinal_keep",
        ("invalid-nonfinal-keep",),
        _invalid_nonfinal_keep,
    ),
    ("sparse_final_keep", ("sparse-final-keep",), _sparse_final_keep),
    ("empty_final", ("empty-final",), _empty_final),
    ("length_overflow", ("length-overflow",), _length_overflow),
    ("reset_recovery", ("reset-recovery",), _reset_recovery),
    (
        "stalled_output_stability",
        ("stalled-output-stability",),
        _stalled_output_stability,
    ),
    (
        "zero_bubble_replacement",
        ("zero-bubble-replacement",),
        _zero_bubble_replacement,
    ),
)


def run_campaign() -> CampaignResult:
    cases = []
    for name, behaviors, operation in _CASES:
        try:
            operation()
        except Exception as error:  # noqa: BLE001  # pragma: no cover
            cases.append(
                CampaignCase(
                    name=name,
                    behavior_names=behaviors,
                    passed=False,
                    detail=f"{type(error).__name__}: {error}",
                )
            )
        else:
            cases.append(
                CampaignCase(
                    name=name,
                    behavior_names=behaviors,
                    passed=True,
                )
            )

    case_records = tuple(cases)
    failed_names = tuple(case.name for case in case_records if not case.passed)
    coverage = tuple(
        sorted(
            {
                behavior
                for case in case_records
                for behavior in case.behavior_names
            }
        )
    )
    passed = sum(case.passed for case in case_records)
    failed_text = ",".join(failed_names) if failed_names else "none"
    summary = (
        f"checksum-cycle-campaign-v1 {passed}/{len(case_records)} passed; "
        f"failed={failed_text}; coverage={','.join(coverage)}"
    )
    return CampaignResult(
        cases=case_records,
        total=len(case_records),
        passed=passed,
        failed_case_names=failed_names,
        covered_behavior_names=coverage,
        summary=summary,
    )


__all__ = ["CampaignCase", "CampaignResult", "run_campaign"]
