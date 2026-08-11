from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class StreamStatus(IntEnum):
    """Status emitted for a successful packet or a terminating stream error."""

    SUCCESS = 0
    MISSING_FIRST = 1
    UNEXPECTED_FIRST = 2
    INVALID_KEEP = 3
    EMPTY_FINAL = 4
    LENGTH_OVERFLOW = 5


def _integer(
    value: Any,
    name: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a boolean")
    return value


@dataclass(frozen=True, slots=True)
class StreamBeat:
    """One 64-bit request beat in the checksum stream.

    Bytes are consumed in ascending lane order, with lane zero in the least
    significant data byte. Adjacent valid bytes form words in network byte
    order. Non-final beats require ``keep=0xff``; final keep masks must be
    nonzero and contiguous from lane zero. The seed is sampled on a first beat.
    """

    data: int
    keep: int
    first: bool
    last: bool
    seed: int = 0

    def __post_init__(self) -> None:
        _integer(self.data, "data", maximum=(1 << 64) - 1)
        _integer(self.keep, "keep", maximum=0xFF)
        _boolean(self.first, "first")
        _boolean(self.last, "last")
        _integer(self.seed, "seed", maximum=0xFFFF)


@dataclass(frozen=True, slots=True)
class StreamResult:
    """Immutable packet result containing status, sums, and accepted length."""

    status: StreamStatus
    checksum: int
    folded_sum: int
    byte_length: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, StreamStatus):
            raise TypeError("status must be a StreamStatus")
        _integer(self.checksum, "checksum", maximum=0xFFFF)
        _integer(self.folded_sum, "folded_sum", maximum=0xFFFF)
        _integer(self.byte_length, "byte_length")


@dataclass(frozen=True, slots=True)
class CycleObservation:
    """Signals observed around one model edge.

    Request ready and acceptance describe the pre-edge state. Response valid
    and result describe the post-edge output register.
    """

    request_ready: bool
    request_accepted: bool
    response_valid: bool
    result: StreamResult | None

    def __post_init__(self) -> None:
        _boolean(self.request_ready, "request_ready")
        _boolean(self.request_accepted, "request_accepted")
        _boolean(self.response_valid, "response_valid")
        if self.request_accepted and not self.request_ready:
            raise ValueError("an accepted request must have request_ready asserted")
        if self.result is not None and not isinstance(self.result, StreamResult):
            raise TypeError("result must be a StreamResult or None")
        if self.response_valid != (self.result is not None):
            raise ValueError("response_valid must indicate whether result is present")


def _fold(total: int) -> int:
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total


class ChecksumCycleModel:
    """Cycle oracle for the 64-bit ready/valid checksum stream.

    A low ``reset_n`` dominates the edge and clears packet and response state.
    A stream error terminates the packet without consuming the offending beat.
    A valid response and its result remain stable while the output is stalled.
    """

    __slots__ = (
        "_active",
        "_byte_length",
        "_maximum_length",
        "_pending_byte",
        "_response",
        "_total",
        "length_width",
    )

    def __init__(self, length_width: int = 16) -> None:
        self.length_width = _integer(length_width, "length_width", minimum=1)
        self._maximum_length = (1 << self.length_width) - 1
        self.reset()

    def reset(self) -> None:
        """Clear the active packet accumulator and pending response."""

        self._active = False
        self._total = 0
        self._pending_byte: int | None = None
        self._byte_length = 0
        self._response: StreamResult | None = None

    def step(
        self,
        beat: StreamBeat | None,
        *,
        response_ready: bool,
        reset_n: bool = True,
    ) -> CycleObservation:
        """Advance one edge using request and response handshake inputs.

        Request readiness is computed from pre-edge response state. Reset
        dominates request acceptance; returned response fields are post-edge.
        """

        if beat is not None and not isinstance(beat, StreamBeat):
            raise TypeError("beat must be a StreamBeat or None")
        _boolean(response_ready, "response_ready")
        _boolean(reset_n, "reset_n")

        request_ready = self._response is None or response_ready
        request_accepted = reset_n and beat is not None and request_ready

        if not reset_n:
            self.reset()
        else:
            if self._response is not None and response_ready:
                self._response = None
            if request_accepted:
                assert beat is not None
                self._accept(beat)

        return CycleObservation(
            request_ready=request_ready,
            request_accepted=request_accepted,
            response_valid=self._response is not None,
            result=self._response,
        )

    def _accept(self, beat: StreamBeat) -> None:
        if not self._active:
            if not beat.first:
                self._fail(StreamStatus.MISSING_FIRST)
                return
            self._active = True
            self._total = beat.seed
            self._pending_byte = None
            self._byte_length = 0
        elif beat.first:
            self._fail(StreamStatus.UNEXPECTED_FIRST)
            return

        byte_count = self._valid_byte_count(beat)
        if byte_count is None:
            return
        if self._byte_length + byte_count > self._maximum_length:
            self._fail(StreamStatus.LENGTH_OVERFLOW)
            return

        self._consume(beat.data, byte_count)
        self._byte_length += byte_count
        if beat.last:
            self._finish()

    def _valid_byte_count(self, beat: StreamBeat) -> int | None:
        if not beat.last:
            if beat.keep != 0xFF:
                self._fail(StreamStatus.INVALID_KEEP)
                return None
            return 8

        if beat.keep == 0:
            self._fail(StreamStatus.EMPTY_FINAL)
            return None
        if beat.keep & (beat.keep + 1):
            self._fail(StreamStatus.INVALID_KEEP)
            return None
        return beat.keep.bit_count()

    def _consume(self, data: int, byte_count: int) -> None:
        for lane in range(byte_count):
            byte = (data >> (lane * 8)) & 0xFF
            if self._pending_byte is None:
                self._pending_byte = byte
            else:
                self._total += (self._pending_byte << 8) | byte
                self._pending_byte = None
        self._total = _fold(self._total)

    def _finish(self) -> None:
        if self._pending_byte is not None:
            self._total += self._pending_byte << 8
        folded_sum = _fold(self._total)
        self._response = StreamResult(
            status=StreamStatus.SUCCESS,
            checksum=(~folded_sum) & 0xFFFF,
            folded_sum=folded_sum,
            byte_length=self._byte_length,
        )
        self._clear_packet()

    def _fail(self, status: StreamStatus) -> None:
        self._response = StreamResult(
            status=status,
            checksum=0,
            folded_sum=0,
            byte_length=self._byte_length,
        )
        self._clear_packet()

    def _clear_packet(self) -> None:
        self._active = False
        self._total = 0
        self._pending_byte = None
        self._byte_length = 0


__all__ = [
    "ChecksumCycleModel",
    "CycleObservation",
    "StreamBeat",
    "StreamResult",
    "StreamStatus",
]
