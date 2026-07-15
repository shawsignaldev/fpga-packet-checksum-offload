from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ChecksumResult:
    checksum: int
    valid: bool

def checksum16(words: list[int]) -> int:
    total = sum(word & 0xFFFF for word in words)
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF

def validate(words: list[int], expected: int) -> bool:
    return checksum16(words) == expected

def offload(words: list[int], expected: int) -> ChecksumResult:
    value = checksum16(words)
    return ChecksumResult(value, value == expected)
