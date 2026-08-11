# Protocol Boundary

## Supported interpretation

The packet inspector accepts Ethernet II frames with an EtherType of at least `0x0600`. It recognizes zero, one, or two `0x8100`/`0x88a8` VLAN tags. For EtherType `0x0800`, it validates IPv4 version, IHL, total length, available bytes, header checksum, options, fragmentation fields, and protocol metadata.

UDP validation checks header length, declared datagram length, available bytes, and the IPv4 pseudo-header. A zero UDP checksum is `disabled`. TCP validation checks the data offset, available segment, and IPv4 pseudo-header. A fragmented UDP or TCP domain is `incomplete` because the current frame cannot establish the complete transport residue. Other IPv4 protocols and non-IPv4 EtherTypes are `not_applicable` for transport verification.

## Checksum states

- `valid`: the complete applicable checksum domain has the expected residue.
- `invalid`: a complete applicable checksum domain does not have the expected residue.
- `disabled`: an IPv4 UDP checksum field is zero.
- `incomplete`: fragmentation prevents complete transport verification.
- `not_applicable`: the parsed frame has no supported transport checksum domain.

CLI exit code `1` is reserved for a completed report containing an invalid applicable checksum or another validation failure. Disabled, incomplete, and not-applicable states remain visible in the report but return exit code `0` when no independent IPv4 checksum is invalid.

## Structural failures

Truncated Ethernet, VLAN, IPv4, UDP, and TCP structures are rejected with the first relevant byte offset. Invalid IPv4 IHL or total length, impossible UDP length, excessive VLAN depth, and Ethernet length-field values are structural errors. These produce CLI exit code `2` and no report.

## Explicit exclusions

The inspector does not implement IPv6, IP fragment reassembly, tunnels, IPv4 extension protocols, transport decryption, checksum insertion, frame mutation, Ethernet FCS, or application parsing. The RTL does not parse these protocols at all; it accumulates a byte domain selected by integration logic. Production use requires separate policy, resource, and timing closure analysis.
