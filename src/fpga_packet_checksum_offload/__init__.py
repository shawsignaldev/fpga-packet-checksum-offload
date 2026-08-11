"""Reference software and verification models for packet checksum offload."""

__version__ = "1.0.0"

from fpga_packet_checksum_offload.arithmetic import (
    ChecksumAccumulator,
    checksum_bytes,
    fold_sum,
    ipv4_pseudo_header_seed,
    replace_word_checksum,
    verify_bytes,
)
from fpga_packet_checksum_offload.campaign import (
    CampaignCase,
    CampaignResult,
    run_campaign,
)
from fpga_packet_checksum_offload.checksum import (
    ChecksumResult,
    checksum16,
    offload,
    validate,
)
from fpga_packet_checksum_offload.cycle_model import (
    ChecksumCycleModel,
    CycleObservation,
    StreamBeat,
    StreamResult,
    StreamStatus,
)
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
from fpga_packet_checksum_offload.reporting import (
    SCHEMA_VERSION,
    BatchOutcome,
    inspect_records,
    inspection_to_dict,
    render_batch_json,
    render_batch_markdown,
    render_inspection_json,
    render_inspection_markdown,
    write_text_atomic,
)
from fpga_packet_checksum_offload.trace_io import (
    FrameRecord,
    TraceLimits,
    read_frame_batch,
)

__all__ = [
    "SCHEMA_VERSION",
    "BatchOutcome",
    "CampaignCase",
    "CampaignResult",
    "ChecksumAccumulator",
    "ChecksumCycleModel",
    "ChecksumField",
    "ChecksumResult",
    "ChecksumState",
    "CycleObservation",
    "FrameInspection",
    "FrameRecord",
    "IPv4Info",
    "PacketFormatError",
    "StreamBeat",
    "StreamResult",
    "StreamStatus",
    "TraceLimits",
    "TransportInfo",
    "VlanTag",
    "__version__",
    "checksum16",
    "checksum_bytes",
    "fold_sum",
    "inspect_ethernet_frame",
    "inspect_records",
    "inspection_to_dict",
    "ipv4_pseudo_header_seed",
    "offload",
    "read_frame_batch",
    "render_batch_json",
    "render_batch_markdown",
    "render_inspection_json",
    "render_inspection_markdown",
    "replace_word_checksum",
    "run_campaign",
    "validate",
    "verify_bytes",
    "write_text_atomic",
]
