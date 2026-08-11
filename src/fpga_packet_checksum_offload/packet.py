from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from ipaddress import IPv4Address
from typing import Any

from fpga_packet_checksum_offload.arithmetic import (
    checksum_bytes,
    ipv4_pseudo_header_seed,
    verify_bytes,
)

_ETHERNET_HEADER_LENGTH = 14
_ETHERNET_II_MINIMUM_TYPE = 0x0600
_IPV4_ETHERTYPE = 0x0800
_VLAN_TPIDS = frozenset((0x8100, 0x88A8))
_UDP_PROTOCOL = 17
_TCP_PROTOCOL = 6


class PacketFormatError(ValueError):
    """Report a structural packet error at an absolute byte offset."""

    __slots__ = ()

    def __init__(self, reason: str, offset: int) -> None:
        super().__init__(reason, offset)

    @property
    def reason(self) -> str:
        return self.args[0]

    @property
    def offset(self) -> int:
        return self.args[1]

    def __str__(self) -> str:
        return f"{self.reason} at byte offset {self.offset}"


class ChecksumState(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    DISABLED = "disabled"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class VlanTag:
    tpid: int
    pcp: int
    dei: bool
    vid: int


@dataclass(frozen=True, slots=True)
class ChecksumField:
    value: int | None
    calculated: int | None
    state: ChecksumState
    offset: int | None


@dataclass(frozen=True, slots=True)
class IPv4Info:
    offset: int
    source: IPv4Address
    destination: IPv4Address
    dscp: int
    ecn: int
    identification: int
    ttl: int
    protocol: int
    flags: int
    fragment_offset: int
    more_fragments: bool
    header_length: int
    total_length: int
    payload_length: int
    checksum: ChecksumField


@dataclass(frozen=True, slots=True)
class TransportInfo:
    """Transport metadata; length and payload_length are None when incomplete."""

    protocol: str
    offset: int | None
    source_port: int | None
    destination_port: int | None
    header_length: int | None
    length: int | None
    payload_length: int | None
    checksum: ChecksumField


@dataclass(frozen=True, slots=True)
class FrameInspection:
    destination_mac: bytes
    source_mac: bytes
    outer_ethertype: int
    payload_ethertype: int
    vlan_tags: tuple[VlanTag, ...]
    ipv4: IPv4Info | None
    transport: TransportInfo | None
    checksum_state: ChecksumState


def _buffer_bytes(data: Any) -> bytes:
    if isinstance(data, bool):
        raise TypeError("data must be a bytes-like object")
    try:
        return memoryview(data).tobytes()
    except TypeError as error:
        raise TypeError("data must be a bytes-like object") from error


def _read_u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def _checksum_with_zeroed_field(
    data: bytes,
    field_offset: int,
    *,
    seed: int = 0,
) -> int:
    zeroed = data[:field_offset] + b"\x00\x00" + data[field_offset + 2 :]
    return checksum_bytes(zeroed, seed=seed)


def _parse_ethernet(
    frame: bytes,
) -> tuple[bytes, bytes, int, int, tuple[VlanTag, ...], int]:
    if len(frame) < _ETHERNET_HEADER_LENGTH:
        raise PacketFormatError("truncated Ethernet header", len(frame))

    destination_mac = frame[:6]
    source_mac = frame[6:12]
    outer_ethertype = _read_u16(frame, 12)
    payload_ethertype = outer_ethertype
    offset = _ETHERNET_HEADER_LENGTH
    tags: list[VlanTag] = []

    while payload_ethertype in _VLAN_TPIDS:
        if len(tags) == 2:
            raise PacketFormatError("too many VLAN tags", offset - 2)
        if len(frame) < offset + 4:
            raise PacketFormatError("truncated VLAN tag", len(frame))

        tci = _read_u16(frame, offset)
        tags.append(
            VlanTag(
                tpid=payload_ethertype,
                pcp=(tci >> 13) & 0x7,
                dei=bool((tci >> 12) & 0x1),
                vid=tci & 0x0FFF,
            )
        )
        payload_ethertype = _read_u16(frame, offset + 2)
        offset += 4

    if payload_ethertype < _ETHERNET_II_MINIMUM_TYPE:
        raise PacketFormatError(
            "Ethernet II type below 0x0600",
            offset - 2,
        )

    return (
        destination_mac,
        source_mac,
        outer_ethertype,
        payload_ethertype,
        tuple(tags),
        offset,
    )


def _parse_ipv4(frame: bytes, offset: int) -> tuple[IPv4Info, bytes, int]:
    captured_length = len(frame) - offset
    if captured_length < 20:
        raise PacketFormatError("truncated IPv4 header", len(frame))

    version_and_ihl = frame[offset]
    if version_and_ihl >> 4 != 4:
        raise PacketFormatError("invalid IPv4 version", offset)

    ihl = version_and_ihl & 0x0F
    if ihl < 5:
        raise PacketFormatError("invalid IPv4 IHL", offset)

    header_length = ihl * 4
    if captured_length < header_length:
        raise PacketFormatError("truncated IPv4 header", len(frame))

    total_length = _read_u16(frame, offset + 2)
    if total_length < header_length:
        raise PacketFormatError("invalid IPv4 total length", offset + 2)
    if total_length > captured_length:
        raise PacketFormatError("truncated IPv4 packet", len(frame))

    packet = frame[offset : offset + total_length]
    header = packet[:header_length]
    payload = packet[header_length:]
    checksum_value = _read_u16(header, 10)
    calculated_checksum = _checksum_with_zeroed_field(header, 10)
    checksum = ChecksumField(
        value=checksum_value,
        calculated=calculated_checksum,
        state=(ChecksumState.VALID if verify_bytes(header) else ChecksumState.INVALID),
        offset=offset + 10,
    )

    differentiated_services = header[1]
    flags_and_offset = _read_u16(header, 6)
    flags = flags_and_offset >> 13
    fragment_offset = (flags_and_offset & 0x1FFF) * 8
    more_fragments = bool(flags & 0x1)
    if flags & 0x4:
        raise PacketFormatError("IPv4 reserved fragment flag is set", offset + 6)
    if flags & 0x2 and (more_fragments or fragment_offset):
        raise PacketFormatError(
            "IPv4 DF flag conflicts with fragmentation",
            offset + 6,
        )
    if more_fragments and len(payload) % 8:
        raise PacketFormatError(
            "non-final IPv4 fragment payload is not divisible by 8",
            offset + 2,
        )

    info = IPv4Info(
        offset=offset,
        source=IPv4Address(header[12:16]),
        destination=IPv4Address(header[16:20]),
        dscp=differentiated_services >> 2,
        ecn=differentiated_services & 0x3,
        identification=_read_u16(header, 4),
        ttl=header[8],
        protocol=header[9],
        flags=flags,
        fragment_offset=fragment_offset,
        more_fragments=more_fragments,
        header_length=header_length,
        total_length=total_length,
        payload_length=len(payload),
        checksum=checksum,
    )
    return info, payload, offset + header_length


def _incomplete_transport(
    protocol: str,
) -> TransportInfo:
    return TransportInfo(
        protocol=protocol,
        offset=None,
        source_port=None,
        destination_port=None,
        header_length=None,
        length=None,
        payload_length=None,
        checksum=ChecksumField(
            value=None,
            calculated=None,
            state=ChecksumState.INCOMPLETE,
            offset=None,
        ),
    )


def _parse_udp(
    payload: bytes,
    offset: int,
    ipv4: IPv4Info,
) -> TransportInfo:
    if ipv4.fragment_offset:
        return _incomplete_transport("UDP")
    if len(payload) < 8:
        raise PacketFormatError("truncated UDP header", offset + len(payload))

    source_port = _read_u16(payload, 0)
    destination_port = _read_u16(payload, 2)
    length = _read_u16(payload, 4)
    checksum_value = _read_u16(payload, 6)
    if length < 8:
        raise PacketFormatError("invalid UDP length", offset + 4)

    if ipv4.more_fragments:
        if len(payload) >= length:
            raise PacketFormatError(
                "UDP first fragment is not shorter than declared length",
                offset + 4,
            )
        return TransportInfo(
            protocol="UDP",
            offset=offset,
            source_port=source_port,
            destination_port=destination_port,
            header_length=8,
            length=None,
            payload_length=None,
            checksum=ChecksumField(
                value=checksum_value,
                calculated=None,
                state=ChecksumState.INCOMPLETE,
                offset=offset + 6,
            ),
        )

    if length != len(payload):
        raise PacketFormatError(
            "UDP length does not match IPv4 payload length",
            offset + 4,
        )

    seed = ipv4_pseudo_header_seed(
        ipv4.source,
        ipv4.destination,
        _UDP_PROTOCOL,
        length,
    )
    calculated = _checksum_with_zeroed_field(payload, 6, seed=seed)
    if calculated == 0:
        calculated = 0xFFFF

    if checksum_value == 0:
        state = ChecksumState.DISABLED
    elif verify_bytes(payload, seed=seed):
        state = ChecksumState.VALID
    else:
        state = ChecksumState.INVALID

    return TransportInfo(
        protocol="UDP",
        offset=offset,
        source_port=source_port,
        destination_port=destination_port,
        header_length=8,
        length=length,
        payload_length=length - 8,
        checksum=ChecksumField(
            value=checksum_value,
            calculated=calculated,
            state=state,
            offset=offset + 6,
        ),
    )


def _parse_tcp(
    payload: bytes,
    offset: int,
    ipv4: IPv4Info,
) -> TransportInfo:
    if ipv4.fragment_offset:
        return _incomplete_transport("TCP")
    if len(payload) < 20:
        raise PacketFormatError("truncated TCP header", offset + len(payload))

    source_port = _read_u16(payload, 0)
    destination_port = _read_u16(payload, 2)
    header_length = (payload[12] >> 4) * 4
    if header_length < 20:
        raise PacketFormatError("invalid TCP data offset", offset + 12)

    checksum_value = _read_u16(payload, 16)
    if ipv4.more_fragments:
        return TransportInfo(
            protocol="TCP",
            offset=offset,
            source_port=source_port,
            destination_port=destination_port,
            header_length=header_length,
            length=None,
            payload_length=None,
            checksum=ChecksumField(
                value=checksum_value,
                calculated=None,
                state=ChecksumState.INCOMPLETE,
                offset=offset + 16,
            ),
        )

    if header_length > len(payload):
        raise PacketFormatError("TCP header exceeds IPv4 payload", offset + 12)

    seed = ipv4_pseudo_header_seed(
        ipv4.source,
        ipv4.destination,
        _TCP_PROTOCOL,
        len(payload),
    )
    calculated = _checksum_with_zeroed_field(payload, 16, seed=seed)
    state = (
        ChecksumState.VALID
        if verify_bytes(payload, seed=seed)
        else ChecksumState.INVALID
    )
    return TransportInfo(
        protocol="TCP",
        offset=offset,
        source_port=source_port,
        destination_port=destination_port,
        header_length=header_length,
        length=len(payload),
        payload_length=len(payload) - header_length,
        checksum=ChecksumField(
            value=checksum_value,
            calculated=calculated,
            state=state,
            offset=offset + 16,
        ),
    )


def inspect_ethernet_frame(
    data: bytes | bytearray | memoryview,
) -> FrameInspection:
    """Inspect one strict Ethernet II frame and its supported checksum domains."""

    frame = _buffer_bytes(data)
    (
        destination_mac,
        source_mac,
        outer_ethertype,
        payload_ethertype,
        vlan_tags,
        payload_offset,
    ) = _parse_ethernet(frame)

    if payload_ethertype != _IPV4_ETHERTYPE:
        return FrameInspection(
            destination_mac=destination_mac,
            source_mac=source_mac,
            outer_ethertype=outer_ethertype,
            payload_ethertype=payload_ethertype,
            vlan_tags=vlan_tags,
            ipv4=None,
            transport=None,
            checksum_state=ChecksumState.NOT_APPLICABLE,
        )

    ipv4, transport_payload, transport_offset = _parse_ipv4(
        frame,
        payload_offset,
    )
    if ipv4.protocol == _UDP_PROTOCOL:
        transport = _parse_udp(transport_payload, transport_offset, ipv4)
    elif ipv4.protocol == _TCP_PROTOCOL:
        transport = _parse_tcp(transport_payload, transport_offset, ipv4)
    else:
        transport = None

    checksum_state = (
        ChecksumState.NOT_APPLICABLE if transport is None else transport.checksum.state
    )
    return FrameInspection(
        destination_mac=destination_mac,
        source_mac=source_mac,
        outer_ethertype=outer_ethertype,
        payload_ethertype=payload_ethertype,
        vlan_tags=vlan_tags,
        ipv4=ipv4,
        transport=transport,
        checksum_state=checksum_state,
    )


__all__ = [
    "ChecksumField",
    "ChecksumState",
    "FrameInspection",
    "IPv4Info",
    "PacketFormatError",
    "TransportInfo",
    "VlanTag",
    "inspect_ethernet_frame",
]
