from __future__ import annotations

from ipaddress import IPv4Address
from typing import Any


def _validate_integer(
    value: Any,
    name: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"{name} must be at least {minimum}")
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _buffer_bytes(data: Any) -> bytes:
    try:
        return memoryview(data).tobytes()
    except TypeError as error:
        raise TypeError("data must be a bytes-like object") from error


def fold_sum(total: int) -> int:
    """Fold a non-negative integer into a 16-bit one's-complement sum."""
    total = _validate_integer(total, "total")
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return total


class ChecksumAccumulator:
    """Incrementally accumulate network-order bytes for an Internet checksum."""

    __slots__ = ("_byte_count", "_pending_byte", "_total")

    def __init__(self, seed: int = 0) -> None:
        self._total = _validate_integer(seed, "seed", maximum=0xFFFF)
        self._pending_byte: int | None = None
        self._byte_count = 0

    @property
    def byte_count(self) -> int:
        return self._byte_count

    def update(self, data: bytes | bytearray | memoryview) -> None:
        chunk = _buffer_bytes(data)
        self._byte_count += len(chunk)
        index = 0

        if self._pending_byte is not None and chunk:
            self._total += (self._pending_byte << 8) | chunk[0]
            self._pending_byte = None
            index = 1

        paired_end = index + ((len(chunk) - index) // 2) * 2
        for position in range(index, paired_end, 2):
            self._total += (chunk[position] << 8) | chunk[position + 1]

        if paired_end < len(chunk):
            self._pending_byte = chunk[paired_end]

        self._total = fold_sum(self._total)

    def folded_sum(self) -> int:
        total = self._total
        if self._pending_byte is not None:
            total += self._pending_byte << 8
        return fold_sum(total)

    def digest(self) -> int:
        return (~self.folded_sum()) & 0xFFFF

    def copy(self) -> ChecksumAccumulator:
        duplicate = ChecksumAccumulator()
        duplicate._total = self._total
        duplicate._pending_byte = self._pending_byte
        duplicate._byte_count = self._byte_count
        return duplicate


def checksum_bytes(
    data: bytes | bytearray | memoryview,
    *,
    seed: int = 0,
) -> int:
    """Return the Internet checksum of a bytes-like object."""
    accumulator = ChecksumAccumulator(seed)
    accumulator.update(data)
    return accumulator.digest()


def verify_bytes(
    data: bytes | bytearray | memoryview,
    *,
    seed: int = 0,
) -> bool:
    """Return whether data including its checksum has the valid residue."""
    accumulator = ChecksumAccumulator(seed)
    accumulator.update(data)
    return accumulator.folded_sum() == 0xFFFF


def _ipv4_address(value: Any, name: str) -> IPv4Address:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be IPv4Address-compatible")
    try:
        return IPv4Address(value)
    except TypeError as error:
        raise TypeError(f"{name} must be IPv4Address-compatible") from error
    except ValueError as error:
        raise ValueError(f"{name} must be a valid IPv4 address") from error


def ipv4_pseudo_header_seed(
    source: Any,
    destination: Any,
    protocol: int,
    transport_length: int,
) -> int:
    """Return the folded, uncomplemented IPv4 pseudo-header sum."""
    source_address = _ipv4_address(source, "source")
    destination_address = _ipv4_address(destination, "destination")
    protocol = _validate_integer(protocol, "protocol", maximum=0xFF)
    transport_length = _validate_integer(
        transport_length,
        "transport_length",
        maximum=0xFFFF,
    )

    pseudo_header = (
        source_address.packed
        + destination_address.packed
        + bytes((0, protocol))
        + transport_length.to_bytes(2, "big")
    )
    accumulator = ChecksumAccumulator()
    accumulator.update(pseudo_header)
    return accumulator.folded_sum()


def replace_word_checksum(checksum: int, old_word: int, new_word: int) -> int:
    """Update a checksum after replacing one 16-bit word per RFC 1624."""
    checksum = _validate_integer(checksum, "checksum", maximum=0xFFFF)
    old_word = _validate_integer(old_word, "old_word", maximum=0xFFFF)
    new_word = _validate_integer(new_word, "new_word", maximum=0xFFFF)

    total = (~checksum & 0xFFFF) + (~old_word & 0xFFFF) + new_word
    return (~fold_sum(total)) & 0xFFFF
