from ipaddress import IPv4Address
from random import Random

import pytest

from fpga_packet_checksum_offload.arithmetic import (
    ChecksumAccumulator,
    checksum_bytes,
    fold_sum,
    ipv4_pseudo_header_seed,
    replace_word_checksum,
    verify_bytes,
)


def _byte_pair_oracle(data: bytes, seed: int) -> int:
    total = seed
    for offset in range(0, len(data), 2):
        low_byte = data[offset + 1] if offset + 1 < len(data) else 0
        total += (data[offset] << 8) | low_byte

    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def test_checksum_bytes_uses_network_order_for_even_and_odd_buffers():
    assert checksum_bytes(bytes.fromhex("12345678")) == 0x9753
    assert checksum_bytes(bytes.fromhex("123456")) == 0x97CB


def test_checksum_bytes_accepts_bytes_like_input_without_mutating_it():
    data = bytearray.fromhex("12345678")

    assert checksum_bytes(memoryview(data)) == 0x9753
    assert data == bytearray.fromhex("12345678")


def test_checksum_bytes_matches_independent_randomized_byte_pair_oracle():
    random = Random(0xD1FF3E)
    lengths = [*range(65), 127, 128, 129, 255, 256, 257, 511]

    for length in lengths:
        data = random.randbytes(length)
        for seed in (0, 0xFFFF, random.randrange(0x1_0000)):
            assert checksum_bytes(data, seed=seed) == _byte_pair_oracle(data, seed)


def test_fold_sum_repeats_end_around_carry_folding():
    assert fold_sum(0xFFFF_FFFF) == 0xFFFF


@pytest.mark.parametrize(
    ("operation", "argument"),
    [
        (fold_sum, True),
        (fold_sum, 1.0),
        (checksum_bytes, "not bytes"),
        (ChecksumAccumulator, False),
    ],
)
def test_arithmetic_rejects_invalid_input_types(operation, argument):
    with pytest.raises(TypeError):
        operation(argument)


@pytest.mark.parametrize(
    ("operation", "argument"),
    [
        (fold_sum, -1),
        (lambda seed: checksum_bytes(b"", seed=seed), -1),
        (lambda seed: checksum_bytes(b"", seed=seed), 0x1_0000),
        (ChecksumAccumulator, -1),
        (ChecksumAccumulator, 0x1_0000),
    ],
)
def test_arithmetic_rejects_out_of_range_values(operation, argument):
    with pytest.raises(ValueError):
        operation(argument)


def test_accumulator_is_invariant_across_arbitrary_chunk_boundaries():
    data = bytes(range(1, 32))
    expected = checksum_bytes(data, seed=0x1234)

    for first in range(len(data) + 1):
        for second in range(first, len(data) + 1):
            accumulator = ChecksumAccumulator(seed=0x1234)
            accumulator.update(data[:first])
            accumulator.update(memoryview(data)[first:second])
            accumulator.update(data[second:])

            assert accumulator.digest() == expected
            assert accumulator.byte_count == len(data)


def test_accumulator_randomized_chunking_invariance_across_lengths_and_seeds():
    random = Random(0xC8A11)
    lengths = [
        0,
        1,
        2,
        3,
        7,
        8,
        9,
        31,
        32,
        33,
        255,
        256,
        257,
        1024,
        *(random.randrange(1025) for _ in range(64)),
    ]

    for length in lengths:
        data = random.randbytes(length)
        for seed in (0, 0xFFFF, random.randrange(0x1_0000)):
            accumulator = ChecksumAccumulator(seed=seed)
            position = 0

            accumulator.update(b"")
            while position < length:
                chunk_length = random.randint(1, min(64, length - position))
                next_position = position + chunk_length
                accumulator.update(memoryview(data)[position:next_position])
                position = next_position
                if random.randrange(4) == 0:
                    accumulator.update(b"")

            assert accumulator.digest() == checksum_bytes(data, seed=seed)
            assert accumulator.byte_count == length


def test_accumulator_copy_and_finalization_do_not_mutate_pending_state():
    accumulator = ChecksumAccumulator(seed=1)
    accumulator.update(b"\x12")

    assert accumulator.folded_sum() == 0x1201
    assert accumulator.digest() == 0xEDFE
    assert accumulator.digest() == 0xEDFE

    snapshot = accumulator.copy()
    accumulator.update(b"\x34")
    snapshot.update(b"\x56")

    assert accumulator.digest() == 0xEDCA
    assert snapshot.digest() == 0xEDA8
    assert accumulator.byte_count == 2
    assert snapshot.byte_count == 2


def test_accumulator_byte_count_is_read_only():
    accumulator = ChecksumAccumulator()

    with pytest.raises(AttributeError):
        accumulator.byte_count = 3


def test_ipv4_pseudo_header_seed_uses_addresses_protocol_and_length():
    assert (
        ipv4_pseudo_header_seed(
            IPv4Address("192.0.2.1"),
            "198.51.100.2",
            protocol=17,
            transport_length=8,
        )
        == 0xEC50
    )


@pytest.mark.parametrize(
    ("protocol", "transport_length", "exception"),
    [
        (True, 8, TypeError),
        (17, False, TypeError),
        (256, 8, ValueError),
        (17, 0x1_0000, ValueError),
    ],
)
def test_ipv4_pseudo_header_seed_validates_numeric_fields(
    protocol, transport_length, exception
):
    with pytest.raises(exception):
        ipv4_pseudo_header_seed(
            "192.0.2.1",
            "198.51.100.2",
            protocol,
            transport_length,
        )


def test_ipv4_pseudo_header_seed_rejects_non_ipv4_addresses():
    with pytest.raises(ValueError):
        ipv4_pseudo_header_seed("2001:db8::1", "198.51.100.2", 17, 8)

    with pytest.raises(TypeError):
        ipv4_pseudo_header_seed(True, "198.51.100.2", 17, 8)


def test_verify_bytes_checks_residue_when_checksum_bytes_are_present():
    data = bytes.fromhex("45000073000040004011")
    checksum = checksum_bytes(data)
    protected = data + checksum.to_bytes(2, "big")

    assert verify_bytes(protected) is True
    assert verify_bytes(protected[:-1] + bytes([protected[-1] ^ 1])) is False


def test_replace_word_checksum_matches_rfc_1624_and_full_recomputation():
    old_checksum = checksum_bytes(bytes.fromhex("cd7a5555"))
    recomputed = checksum_bytes(bytes.fromhex("cd7a3285"))

    assert old_checksum == 0xDD2F
    assert recomputed == 0x0000
    assert replace_word_checksum(old_checksum, 0x5555, 0x3285) == recomputed


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_replace_word_checksum_rejects_invalid_types(value):
    with pytest.raises(TypeError):
        replace_word_checksum(value, 0, 0)

    with pytest.raises(TypeError):
        replace_word_checksum(0, value, 0)

    with pytest.raises(TypeError):
        replace_word_checksum(0, 0, value)


@pytest.mark.parametrize("value", [-1, 0x1_0000])
def test_replace_word_checksum_rejects_out_of_range_words(value):
    with pytest.raises(ValueError):
        replace_word_checksum(value, 0, 0)

    with pytest.raises(ValueError):
        replace_word_checksum(0, value, 0)

    with pytest.raises(ValueError):
        replace_word_checksum(0, 0, value)


def test_stable_arithmetic_api_is_reexported_from_package():
    import fpga_packet_checksum_offload as package

    assert package.ChecksumAccumulator is ChecksumAccumulator
    assert package.checksum_bytes is checksum_bytes
    assert package.fold_sum is fold_sum
    assert package.ipv4_pseudo_header_seed is ipv4_pseudo_header_seed
    assert package.replace_word_checksum is replace_word_checksum
    assert package.verify_bytes is verify_bytes
