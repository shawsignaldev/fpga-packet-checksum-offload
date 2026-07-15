from fpga_packet_checksum_offload.checksum import checksum16, offload, validate

def test_checksum16_is_ones_complement():
    assert checksum16([0x0000]) == 0xFFFF

def test_offload_reports_value_and_validity():
    expected = checksum16([0x1234, 0x5678])
    assert validate([0x1234, 0x5678], expected) is True
    assert offload([0x1234, 0x5678], expected).valid is True
