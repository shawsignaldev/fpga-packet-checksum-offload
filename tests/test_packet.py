from __future__ import annotations

import json
import pickle
from dataclasses import FrozenInstanceError
from ipaddress import IPv4Address
from pathlib import Path
from typing import get_type_hints

import pytest

from fpga_packet_checksum_offload.packet import (
    ChecksumField,
    ChecksumState,
    FrameInspection,
    IPv4Info,
    PacketFormatError,
    TransportInfo,
    VlanTag,
    inspect_ethernet_frame,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "frames.json").read_text(encoding="ascii")
)
DESTINATION_MAC = bytes.fromhex("001122334455")
SOURCE_MAC = bytes.fromhex("66778899aabb")
SOURCE_IP = IPv4Address("192.0.2.1")
DESTINATION_IP = IPv4Address("198.51.100.2")


def _oracle_checksum(data: bytes) -> int:
    total = 0
    for offset in range(0, len(data), 2):
        low_byte = data[offset + 1] if offset + 1 < len(data) else 0
        total += (data[offset] << 8) | low_byte
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _pseudo_header(
    source: IPv4Address,
    destination: IPv4Address,
    protocol: int,
    length: int,
) -> bytes:
    return (
        source.packed
        + destination.packed
        + bytes((0, protocol))
        + length.to_bytes(2, "big")
    )


def _udp_segment(
    payload: bytes,
    *,
    source: IPv4Address = SOURCE_IP,
    destination: IPv4Address = DESTINATION_IP,
    source_port: int = 12345,
    destination_port: int = 54321,
    checksum_enabled: bool = True,
) -> tuple[bytes, int]:
    length = 8 + len(payload)
    segment = (
        source_port.to_bytes(2, "big")
        + destination_port.to_bytes(2, "big")
        + length.to_bytes(2, "big")
        + b"\x00\x00"
        + payload
    )
    calculated = _oracle_checksum(
        _pseudo_header(source, destination, 17, length) + segment
    )
    transmitted = 0 if not checksum_enabled else (calculated or 0xFFFF)
    return segment[:6] + transmitted.to_bytes(2, "big") + segment[8:], transmitted


def _tcp_segment(
    payload: bytes,
    *,
    source: IPv4Address = SOURCE_IP,
    destination: IPv4Address = DESTINATION_IP,
    source_port: int = 443,
    destination_port: int = 49152,
    sequence: int = 0x12345678,
    acknowledgment: int = 0x89ABCDEF,
    options: bytes = b"",
) -> tuple[bytes, int]:
    assert len(options) % 4 == 0
    data_offset = 5 + len(options) // 4
    header = (
        source_port.to_bytes(2, "big")
        + destination_port.to_bytes(2, "big")
        + sequence.to_bytes(4, "big")
        + acknowledgment.to_bytes(4, "big")
        + bytes((data_offset << 4, 0x18))
        + (4096).to_bytes(2, "big")
        + b"\x00\x00"
        + b"\x00\x00"
        + options
    )
    segment = header + payload
    checksum = _oracle_checksum(
        _pseudo_header(source, destination, 6, len(segment)) + segment
    )
    return segment[:16] + checksum.to_bytes(2, "big") + segment[18:], checksum


def _ipv4_packet(
    payload: bytes,
    *,
    source: IPv4Address = SOURCE_IP,
    destination: IPv4Address = DESTINATION_IP,
    protocol: int = 17,
    options: bytes = b"",
    dscp: int = 0,
    ecn: int = 0,
    identification: int = 0x1234,
    ttl: int = 64,
    flags: int = 2,
    fragment_offset_bytes: int = 0,
    total_length: int | None = None,
) -> bytes:
    assert len(options) % 4 == 0
    assert fragment_offset_bytes % 8 == 0
    header_length = 20 + len(options)
    encoded_total_length = (
        header_length + len(payload) if total_length is None else total_length
    )
    flags_and_offset = (flags << 13) | (fragment_offset_bytes // 8)
    header = (
        bytes(((4 << 4) | (header_length // 4), (dscp << 2) | ecn))
        + encoded_total_length.to_bytes(2, "big")
        + identification.to_bytes(2, "big")
        + flags_and_offset.to_bytes(2, "big")
        + bytes((ttl, protocol))
        + b"\x00\x00"
        + source.packed
        + destination.packed
        + options
    )
    checksum = _oracle_checksum(header)
    return header[:10] + checksum.to_bytes(2, "big") + header[12:] + payload


def _ethernet_frame(
    payload: bytes,
    *,
    ethertype: int = 0x0800,
    destination: bytes = DESTINATION_MAC,
    source: bytes = SOURCE_MAC,
    tags: tuple[tuple[int, int, bool, int], ...] = (),
) -> bytes:
    header = destination + source
    if not tags:
        return header + ethertype.to_bytes(2, "big") + payload

    header += tags[0][0].to_bytes(2, "big")
    for index, (tpid, pcp, dei, vid) in enumerate(tags):
        assert tpid in (0x8100, 0x88A8)
        tci = (pcp << 13) | (int(dei) << 12) | vid
        next_type = tags[index + 1][0] if index + 1 < len(tags) else ethertype
        header += tci.to_bytes(2, "big") + next_type.to_bytes(2, "big")
    return header + payload


def _fixture_udp_frame() -> tuple[bytes, int]:
    fixture = FIXTURES["udp_odd"]
    source = IPv4Address(fixture["source_ip"])
    destination = IPv4Address(fixture["destination_ip"])
    udp, checksum = _udp_segment(
        bytes.fromhex(fixture["payload"]),
        source=source,
        destination=destination,
        source_port=fixture["source_port"],
        destination_port=fixture["destination_port"],
    )
    packet = _ipv4_packet(udp, source=source, destination=destination)
    return (
        _ethernet_frame(
            packet,
            destination=bytes.fromhex(fixture["destination_mac"]),
            source=bytes.fromhex(fixture["source_mac"]),
        ),
        checksum,
    )


def _fixture_tcp_frame() -> tuple[bytes, int]:
    fixture = FIXTURES["tcp"]
    source = IPv4Address(fixture["source_ip"])
    destination = IPv4Address(fixture["destination_ip"])
    tcp, checksum = _tcp_segment(
        bytes.fromhex(fixture["payload"]),
        source=source,
        destination=destination,
        source_port=fixture["source_port"],
        destination_port=fixture["destination_port"],
        sequence=fixture["sequence"],
        acknowledgment=fixture["acknowledgment"],
        options=bytes.fromhex(fixture["options"]),
    )
    return (
        _ethernet_frame(
            _ipv4_packet(tcp, source=source, destination=destination, protocol=6),
            destination=bytes.fromhex(fixture["destination_mac"]),
            source=bytes.fromhex(fixture["source_mac"]),
        ),
        checksum,
    )


def _assert_format_error(data: bytes, reason: str, offset: int) -> None:
    with pytest.raises(PacketFormatError) as raised:
        inspect_ethernet_frame(data)
    assert raised.value.reason == reason
    assert raised.value.offset == offset


def test_packet_format_error_pickle_round_trip_preserves_public_state():
    original = PacketFormatError("invalid test field", 37)

    assert PacketFormatError.__slots__ == ()
    with pytest.raises(AttributeError):
        original.reason = "changed"
    with pytest.raises(AttributeError):
        original.offset = 99

    restored = pickle.loads(pickle.dumps(original))

    assert restored.args == ("invalid test field", 37)
    assert restored.reason == original.reason
    assert restored.offset == original.offset
    assert str(restored) == "invalid test field at byte offset 37"
    assert str(restored) == str(original)
    with pytest.raises(AttributeError):
        restored.reason = "changed"
    with pytest.raises(AttributeError):
        restored.offset = 99


@pytest.mark.parametrize("fixture_name", sorted(FIXTURES))
def test_static_golden_frames_match_fixed_metadata(fixture_name):
    fixture = FIXTURES[fixture_name]
    expected = fixture["expected"]

    inspection = inspect_ethernet_frame(bytes.fromhex(fixture["frame_hex"]))

    assert inspection.destination_mac.hex() == expected["destination_mac"]
    assert inspection.source_mac.hex() == expected["source_mac"]
    assert inspection.outer_ethertype == expected["outer_ethertype"]
    assert inspection.payload_ethertype == expected["payload_ethertype"]
    assert inspection.ipv4 is not None
    assert str(inspection.ipv4.source) == expected["source_ip"]
    assert str(inspection.ipv4.destination) == expected["destination_ip"]
    assert inspection.ipv4.protocol == expected["ip_protocol"]
    assert inspection.ipv4.checksum.value == expected["ipv4_checksum"]
    assert inspection.ipv4.checksum.calculated == expected["ipv4_checksum"]
    assert inspection.ipv4.checksum.state.value == expected["ipv4_checksum_state"]

    if expected["transport_protocol"] is None:
        assert inspection.transport is None
    else:
        assert inspection.transport is not None
        assert inspection.transport.protocol == expected["transport_protocol"]
        assert inspection.transport.source_port == expected["source_port"]
        assert inspection.transport.destination_port == expected["destination_port"]
        assert inspection.transport.checksum.value == expected["transport_checksum"]
        assert inspection.transport.checksum.calculated == expected.get(
            "transport_checksum_calculated",
            expected["transport_checksum"],
        )
        assert (
            inspection.transport.checksum.state.value
            == expected["transport_checksum_state"]
        )


def test_valid_ethernet_ipv4_udp_with_odd_payload_is_fully_inspected():
    frame, expected_checksum = _fixture_udp_frame()

    inspection = inspect_ethernet_frame(frame)

    assert inspection.destination_mac == bytes.fromhex("001122334455")
    assert inspection.source_mac == bytes.fromhex("66778899aabb")
    assert inspection.outer_ethertype == 0x0800
    assert inspection.payload_ethertype == 0x0800
    assert inspection.vlan_tags == ()
    assert inspection.checksum_state is ChecksumState.VALID
    assert inspection.ipv4 is not None
    assert inspection.ipv4.source == SOURCE_IP
    assert inspection.ipv4.destination == DESTINATION_IP
    assert inspection.ipv4.dscp == 0
    assert inspection.ipv4.ecn == 0
    assert inspection.ipv4.identification == 0x1234
    assert inspection.ipv4.ttl == 64
    assert inspection.ipv4.protocol == 17
    assert inspection.ipv4.flags == 2
    assert inspection.ipv4.fragment_offset == 0
    assert inspection.ipv4.more_fragments is False
    assert inspection.ipv4.header_length == 20
    assert inspection.ipv4.payload_length == 13
    assert inspection.ipv4.checksum.state is ChecksumState.VALID
    assert inspection.ipv4.checksum.calculated == inspection.ipv4.checksum.value
    assert inspection.transport is not None
    assert inspection.transport.protocol == "UDP"
    assert inspection.transport.source_port == 12345
    assert inspection.transport.destination_port == 54321
    assert inspection.transport.header_length == 8
    assert inspection.transport.length == 13
    assert inspection.transport.payload_length == 5
    assert inspection.transport.checksum.value == expected_checksum
    assert inspection.transport.checksum.calculated == expected_checksum
    assert inspection.transport.checksum.state is ChecksumState.VALID


def test_valid_tcp_checksum_and_header_fields_are_inspected():
    frame, expected_checksum = _fixture_tcp_frame()

    inspection = inspect_ethernet_frame(frame)

    assert inspection.ipv4 is not None
    assert inspection.ipv4.protocol == 6
    assert inspection.transport is not None
    assert inspection.transport.protocol == "TCP"
    assert inspection.transport.source_port == 443
    assert inspection.transport.destination_port == 49152
    assert inspection.transport.header_length == 24
    assert inspection.transport.length == 30
    assert inspection.transport.payload_length == 6
    assert inspection.transport.checksum == ChecksumField(
        value=expected_checksum,
        calculated=expected_checksum,
        state=ChecksumState.VALID,
        offset=50,
    )
    assert inspection.checksum_state is ChecksumState.VALID


@pytest.mark.parametrize(
    ("tags", "expected_outer", "expected_tags"),
    [
        (
            ((0x8100, 5, True, 123),),
            0x8100,
            (VlanTag(tpid=0x8100, pcp=5, dei=True, vid=123),),
        ),
        (
            ((0x88A8, 3, False, 200), (0x8100, 1, True, 4094)),
            0x88A8,
            (
                VlanTag(tpid=0x88A8, pcp=3, dei=False, vid=200),
                VlanTag(tpid=0x8100, pcp=1, dei=True, vid=4094),
            ),
        ),
    ],
)
def test_single_and_double_vlan_tags_are_exposed(tags, expected_outer, expected_tags):
    frame, _ = _fixture_udp_frame()
    tagged = _ethernet_frame(frame[14:], tags=tags)

    inspection = inspect_ethernet_frame(tagged)

    assert inspection.outer_ethertype == expected_outer
    assert inspection.payload_ethertype == 0x0800
    assert inspection.vlan_tags == expected_tags
    assert inspection.checksum_state is ChecksumState.VALID


def test_ipv4_options_are_included_in_header_checksum_verification():
    fixture = FIXTURES["ipv4_options"]
    source = IPv4Address(fixture["source_ip"])
    destination = IPv4Address(fixture["destination_ip"])
    packet = _ipv4_packet(
        bytes.fromhex(fixture["payload"]),
        source=source,
        destination=destination,
        protocol=253,
        options=bytes.fromhex(fixture["options"]),
    )

    inspection = inspect_ethernet_frame(_ethernet_frame(packet))

    assert inspection.ipv4 is not None
    assert inspection.ipv4.header_length == 24
    assert inspection.ipv4.payload_length == 7
    assert inspection.ipv4.checksum.state is ChecksumState.VALID
    assert inspection.transport is None
    assert inspection.checksum_state is ChecksumState.NOT_APPLICABLE


def test_invalid_ipv4_header_checksum_is_reported_without_rejecting_structure():
    frame, _ = _fixture_udp_frame()
    damaged = frame[:24] + bytes((frame[24] ^ 1,)) + frame[25:]

    inspection = inspect_ethernet_frame(damaged)

    assert inspection.ipv4 is not None
    assert inspection.ipv4.checksum.state is ChecksumState.INVALID
    assert inspection.transport is not None
    assert inspection.transport.checksum.state is ChecksumState.VALID


@pytest.mark.parametrize("protocol", ["UDP", "TCP"])
def test_invalid_transport_checksum_is_reported(protocol):
    frame, _ = _fixture_udp_frame() if protocol == "UDP" else _fixture_tcp_frame()
    damaged = frame[:-1] + bytes((frame[-1] ^ 1,))

    inspection = inspect_ethernet_frame(damaged)

    assert inspection.transport is not None
    assert inspection.transport.protocol == protocol
    assert inspection.transport.checksum.state is ChecksumState.INVALID
    assert inspection.checksum_state is ChecksumState.INVALID


def test_udp_zero_checksum_is_disabled():
    udp, _ = _udp_segment(b"disabled", checksum_enabled=False)

    inspection = inspect_ethernet_frame(_ethernet_frame(_ipv4_packet(udp)))

    assert inspection.transport is not None
    assert inspection.transport.checksum.value == 0
    assert inspection.transport.checksum.calculated is not None
    assert inspection.transport.checksum.state is ChecksumState.DISABLED
    assert inspection.checksum_state is ChecksumState.DISABLED


def test_udp_calculated_zero_is_exposed_as_transmitted_ffff():
    def raw_udp_checksum(payload: bytes) -> int:
        length = 8 + len(payload)
        segment = (
            (12345).to_bytes(2, "big")
            + (54321).to_bytes(2, "big")
            + length.to_bytes(2, "big")
            + b"\x00\x00"
            + payload
        )
        return _oracle_checksum(
            _pseudo_header(SOURCE_IP, DESTINATION_IP, 17, length) + segment
        )

    payload = next(
        value.to_bytes(2, "big")
        for value in range(0x1_0000)
        if raw_udp_checksum(value.to_bytes(2, "big")) == 0
    )
    udp, transmitted = _udp_segment(payload)

    assert raw_udp_checksum(payload) == 0
    inspection = inspect_ethernet_frame(_ethernet_frame(_ipv4_packet(udp)))

    assert transmitted == 0xFFFF
    assert inspection.transport is not None
    assert inspection.transport.checksum.value == 0xFFFF
    assert inspection.transport.checksum.calculated == 0xFFFF
    assert inspection.transport.checksum.state is ChecksumState.VALID


@pytest.mark.parametrize("protocol", [17, 6])
def test_first_fragments_parse_base_transport_header_as_incomplete(protocol):
    if protocol == 17:
        complete, _ = _udp_segment(b"fragment payload")
        first_fragment = complete[:8]
        expected_name = "UDP"
    else:
        complete, _ = _tcp_segment(b"fragment payload")
        first_fragment = complete[:24]
        expected_name = "TCP"
    packet = _ipv4_packet(first_fragment, protocol=protocol, flags=1)

    inspection = inspect_ethernet_frame(_ethernet_frame(packet))

    assert inspection.ipv4 is not None
    assert inspection.ipv4.more_fragments is True
    assert inspection.transport is not None
    assert inspection.transport.protocol == expected_name
    assert inspection.transport.source_port is not None
    assert inspection.transport.destination_port is not None
    assert inspection.transport.checksum.state is ChecksumState.INCOMPLETE
    assert inspection.transport.checksum.calculated is None
    assert inspection.checksum_state is ChecksumState.INCOMPLETE


def test_static_udp_fragment_fixture_is_derived_from_owned_packet_helpers():
    complete, _ = _udp_segment(bytes.fromhex(FIXTURES["udp_first_fragment"]["payload"]))
    expected = _ethernet_frame(_ipv4_packet(complete[:8], flags=1))

    assert bytes.fromhex(FIXTURES["udp_first_fragment"]["frame_hex"]) == expected


def test_first_tcp_fragment_allows_declared_options_beyond_capture():
    complete, _ = _tcp_segment(b"", options=b"\x01\x01\x00\x00" * 2)
    packet = _ipv4_packet(complete[:24], protocol=6, flags=1)

    inspection = inspect_ethernet_frame(_ethernet_frame(packet))

    assert inspection.transport is not None
    assert inspection.transport.protocol == "TCP"
    assert inspection.transport.source_port == 443
    assert inspection.transport.destination_port == 49152
    assert inspection.transport.header_length == 28
    assert inspection.transport.length is None
    assert inspection.transport.payload_length is None
    assert inspection.transport.checksum.state is ChecksumState.INCOMPLETE


def test_first_udp_fragment_omits_lengths_when_declared_payload_is_incomplete():
    complete, _ = _udp_segment(b"fragment payload")
    packet = _ipv4_packet(complete[:16], flags=1)

    inspection = inspect_ethernet_frame(_ethernet_frame(packet))

    assert inspection.transport is not None
    assert inspection.transport.protocol == "UDP"
    assert inspection.transport.header_length == 8
    assert inspection.transport.length is None
    assert inspection.transport.payload_length is None
    assert inspection.transport.checksum.state is ChecksumState.INCOMPLETE


def test_first_udp_fragment_rejects_capture_beyond_declared_length():
    udp = b"\x00\x01\x00\x02\x00\x08\x12\x34" + b"x" * 8
    frame = _ethernet_frame(_ipv4_packet(udp, flags=1))

    _assert_format_error(
        frame,
        "UDP first fragment is not shorter than declared length",
        38,
    )


def test_first_udp_fragment_rejects_capture_equal_to_declared_length():
    udp = b"\x00\x01\x00\x02\x00\x08\x12\x34"
    frame = _ethernet_frame(_ipv4_packet(udp, flags=1))

    _assert_format_error(
        frame,
        "UDP first fragment is not shorter than declared length",
        38,
    )


@pytest.mark.parametrize(("protocol", "expected_name"), [(17, "UDP"), (6, "TCP")])
def test_non_first_fragments_do_not_parse_transport_ports(protocol, expected_name):
    packet = _ipv4_packet(
        b"\xaa\xbb\xcc",
        protocol=protocol,
        flags=0,
        fragment_offset_bytes=8,
    )

    inspection = inspect_ethernet_frame(_ethernet_frame(packet))

    assert inspection.ipv4 is not None
    assert inspection.ipv4.fragment_offset == 8
    assert inspection.transport is not None
    assert inspection.transport.protocol == expected_name
    assert inspection.transport.source_port is None
    assert inspection.transport.destination_port is None
    assert get_type_hints(TransportInfo)["offset"] == int | None
    assert inspection.transport.offset is None
    assert inspection.transport.header_length is None
    assert inspection.transport.checksum == ChecksumField(
        value=None,
        calculated=None,
        state=ChecksumState.INCOMPLETE,
        offset=None,
    )
    assert inspection.checksum_state is ChecksumState.INCOMPLETE


def test_non_ipv4_ethertype_is_well_formed_and_not_applicable():
    inspection = inspect_ethernet_frame(_ethernet_frame(b"\x00" * 28, ethertype=0x0806))

    assert inspection.payload_ethertype == 0x0806
    assert inspection.ipv4 is None
    assert inspection.transport is None
    assert inspection.checksum_state is ChecksumState.NOT_APPLICABLE


def test_unsupported_ipv4_protocol_is_well_formed_and_not_applicable():
    packet = _ipv4_packet(b"unsupported", protocol=132)

    inspection = inspect_ethernet_frame(_ethernet_frame(packet))

    assert inspection.ipv4 is not None
    assert inspection.ipv4.protocol == 132
    assert inspection.transport is None
    assert inspection.checksum_state is ChecksumState.NOT_APPLICABLE


@pytest.mark.parametrize("invalid", [True, False, "frame", 1, [0, 1]])
def test_non_bytes_like_inputs_are_rejected(invalid):
    with pytest.raises(TypeError, match="data must be a bytes-like object"):
        inspect_ethernet_frame(invalid)


@pytest.mark.parametrize("length", [0, 1, 12, 13])
def test_truncated_ethernet_header_reports_first_missing_byte(length):
    _assert_format_error(
        b"\x00" * length,
        "truncated Ethernet header",
        length,
    )


@pytest.mark.parametrize("tag_bytes", [0, 1, 2, 3])
def test_truncated_vlan_tag_reports_first_missing_byte(tag_bytes):
    frame = DESTINATION_MAC + SOURCE_MAC + b"\x81\x00" + b"\x00" * tag_bytes
    _assert_format_error(frame, "truncated VLAN tag", len(frame))


def test_third_vlan_tag_is_a_structural_error_at_its_tpid():
    frame = _ethernet_frame(
        b"payload",
        tags=(
            (0x88A8, 0, False, 1),
            (0x8100, 0, False, 2),
            (0x8100, 0, False, 3),
        ),
    )

    _assert_format_error(frame, "too many VLAN tags", 20)


@pytest.mark.parametrize(
    ("tags", "expected_offset"),
    [
        ((), 12),
        (((0x8100, 0, False, 1),), 16),
        (((0x88A8, 0, False, 1), (0x8100, 0, False, 2)), 20),
    ],
)
def test_final_type_length_below_ethernet_ii_range_is_rejected(tags, expected_offset):
    frame = _ethernet_frame(b"payload", ethertype=0x05FF, tags=tags)

    _assert_format_error(
        frame,
        "Ethernet II type below 0x0600",
        expected_offset,
    )


def test_ethernet_ii_type_boundary_is_accepted():
    inspection = inspect_ethernet_frame(_ethernet_frame(b"payload", ethertype=0x0600))

    assert inspection.payload_ethertype == 0x0600
    assert inspection.checksum_state is ChecksumState.NOT_APPLICABLE


@pytest.mark.parametrize("captured", [0, 1, 19])
def test_truncated_ipv4_base_header_reports_first_missing_byte(captured):
    frame = (
        _ethernet_frame(b"\x45" + b"\x00" * (captured - 1))
        if captured
        else _ethernet_frame(b"")
    )
    _assert_format_error(frame, "truncated IPv4 header", 14 + captured)


def test_invalid_ipv4_version_reports_version_field_offset():
    packet = bytearray(_ipv4_packet(b"", protocol=253))
    packet[0] = (6 << 4) | 5
    _assert_format_error(
        _ethernet_frame(packet),
        "invalid IPv4 version",
        14,
    )


def test_invalid_ipv4_ihl_reports_version_field_offset():
    packet = bytearray(_ipv4_packet(b"", protocol=253))
    packet[0] = (4 << 4) | 4
    _assert_format_error(_ethernet_frame(packet), "invalid IPv4 IHL", 14)


def test_truncated_ipv4_options_report_first_missing_byte():
    packet = bytearray(_ipv4_packet(b"", protocol=253))
    packet[0] = (4 << 4) | 6
    _assert_format_error(
        _ethernet_frame(packet),
        "truncated IPv4 header",
        34,
    )


def test_ipv4_total_length_smaller_than_header_reports_length_field():
    packet = _ipv4_packet(b"", protocol=253, total_length=19)
    _assert_format_error(
        _ethernet_frame(packet),
        "invalid IPv4 total length",
        16,
    )


def test_ipv4_total_length_beyond_capture_reports_first_missing_byte():
    packet = _ipv4_packet(b"captured", protocol=253, total_length=29)
    frame = _ethernet_frame(packet)
    _assert_format_error(frame, "truncated IPv4 packet", len(frame))


@pytest.mark.parametrize("captured", [0, 1, 7])
def test_truncated_udp_base_header_reports_first_missing_byte(captured):
    packet = _ipv4_packet(b"\x00" * captured)
    frame = _ethernet_frame(packet)
    _assert_format_error(frame, "truncated UDP header", len(frame))


@pytest.mark.parametrize("udp_length", [0, 7])
def test_udp_length_below_header_reports_length_field(udp_length):
    udp = b"\x00\x01\x00\x02" + udp_length.to_bytes(2, "big") + b"\x00\x00"
    _assert_format_error(
        _ethernet_frame(_ipv4_packet(udp)),
        "invalid UDP length",
        38,
    )


@pytest.mark.parametrize(
    "udp",
    [
        b"\x00\x01\x00\x02\x00\x09\x00\x00",
        b"\x00\x01\x00\x02\x00\x08\x00\x00x",
    ],
)
def test_unfragmented_udp_length_must_equal_ipv4_payload_length(udp):
    _assert_format_error(
        _ethernet_frame(_ipv4_packet(udp)),
        "UDP length does not match IPv4 payload length",
        38,
    )


@pytest.mark.parametrize("captured", [0, 1, 19])
def test_truncated_tcp_base_header_reports_first_missing_byte(captured):
    packet = _ipv4_packet(b"\x00" * captured, protocol=6)
    frame = _ethernet_frame(packet)
    _assert_format_error(frame, "truncated TCP header", len(frame))


def test_tcp_data_offset_below_five_reports_data_offset_field():
    tcp = bytearray(20)
    tcp[12] = 4 << 4
    _assert_format_error(
        _ethernet_frame(_ipv4_packet(tcp, protocol=6)),
        "invalid TCP data offset",
        46,
    )


def test_tcp_data_offset_beyond_payload_reports_data_offset_field():
    tcp = bytearray(20)
    tcp[12] = 6 << 4
    _assert_format_error(
        _ethernet_frame(_ipv4_packet(tcp, protocol=6)),
        "TCP header exceeds IPv4 payload",
        46,
    )


def test_ipv4_reserved_fragment_flag_is_rejected():
    packet = _ipv4_packet(b"\x00" * 8, protocol=253, flags=4)

    _assert_format_error(
        _ethernet_frame(packet),
        "IPv4 reserved fragment flag is set",
        20,
    )


@pytest.mark.parametrize(
    ("flags", "fragment_offset_bytes"),
    [(3, 0), (2, 8)],
)
def test_ipv4_dont_fragment_cannot_describe_a_fragment(flags, fragment_offset_bytes):
    packet = _ipv4_packet(
        b"\x00" * 8,
        protocol=253,
        flags=flags,
        fragment_offset_bytes=fragment_offset_bytes,
    )

    _assert_format_error(
        _ethernet_frame(packet),
        "IPv4 DF flag conflicts with fragmentation",
        20,
    )


def test_non_final_ipv4_fragment_payload_must_be_divisible_by_eight():
    packet = _ipv4_packet(b"\x00" * 9, protocol=253, flags=1)

    _assert_format_error(
        _ethernet_frame(packet),
        "non-final IPv4 fragment payload is not divisible by 8",
        16,
    )


@pytest.mark.parametrize(
    ("protocol", "captured"),
    [(17, 0), (6, 0), (6, 16)],
)
def test_truncated_first_fragment_base_headers_are_structural_errors(
    protocol, captured
):
    minimum = 8 if protocol == 17 else 20
    actual_captured = min(captured, minimum - 1)
    packet = _ipv4_packet(
        b"\x00" * actual_captured,
        protocol=protocol,
        flags=1,
    )
    frame = _ethernet_frame(packet)
    reason = "truncated UDP header" if protocol == 17 else "truncated TCP header"
    _assert_format_error(frame, reason, len(frame))


def test_ethernet_padding_after_ipv4_total_length_does_not_change_result():
    frame, _ = _fixture_udp_frame()

    assert inspect_ethernet_frame(frame + b"\x00" * 32) == inspect_ethernet_frame(frame)


@pytest.mark.parametrize("protocol", ["UDP", "TCP"])
def test_protected_payload_change_flips_state_without_changing_addressing(protocol):
    frame, _ = _fixture_udp_frame() if protocol == "UDP" else _fixture_tcp_frame()
    changed = frame[:-1] + bytes((frame[-1] ^ 0x80,))

    original = inspect_ethernet_frame(frame)
    mutated = inspect_ethernet_frame(changed)

    assert original.ipv4 is not None
    assert mutated.ipv4 is not None
    assert original.ipv4.source == mutated.ipv4.source
    assert original.ipv4.destination == mutated.ipv4.destination
    assert original.transport is not None
    assert mutated.transport is not None
    assert original.transport.source_port == mutated.transport.source_port
    assert original.transport.destination_port == mutated.transport.destination_port
    assert original.checksum_state is ChecksumState.VALID
    assert mutated.checksum_state is ChecksumState.INVALID


def test_bytes_like_input_is_not_mutated_and_records_are_immutable_slots():
    frame, _ = _fixture_udp_frame()
    mutable_frame = bytearray(frame)
    before = mutable_frame[:]

    inspection = inspect_ethernet_frame(memoryview(mutable_frame))

    assert mutable_frame == before
    with pytest.raises(FrozenInstanceError):
        inspection.outer_ethertype = 0
    with pytest.raises((AttributeError, TypeError)):
        inspection.extra = 1


def test_stable_packet_api_is_reexported_from_package():
    import fpga_packet_checksum_offload as package

    expected = {
        "ChecksumField": ChecksumField,
        "ChecksumState": ChecksumState,
        "FrameInspection": FrameInspection,
        "IPv4Info": IPv4Info,
        "PacketFormatError": PacketFormatError,
        "TransportInfo": TransportInfo,
        "VlanTag": VlanTag,
        "inspect_ethernet_frame": inspect_ethernet_frame,
    }
    for name, value in expected.items():
        assert getattr(package, name) is value


def test_checksum_state_is_a_string_enum():
    assert {state.value for state in ChecksumState} == {
        "valid",
        "invalid",
        "disabled",
        "incomplete",
        "not_applicable",
    }
    assert ChecksumState.VALID == "valid"
