import ast
import inspect
from random import Random

import pytest

from fpga_packet_checksum_offload.cycle_model import (
    ChecksumCycleModel,
    CycleObservation,
    StreamBeat,
    StreamResult,
    StreamStatus,
)


def _checksum_oracle(data: bytes, seed: int = 0) -> tuple[int, int]:
    total = seed
    for offset in range(0, len(data), 2):
        second = data[offset + 1] if offset + 1 < len(data) else 0
        total += (data[offset] << 8) | second
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF, total


def _beat_data(chunk: bytes) -> int:
    return sum(byte << (lane * 8) for lane, byte in enumerate(chunk))


def _packet_beats(data: bytes, seed: int = 0) -> list[StreamBeat]:
    assert data
    beats = []
    for offset in range(0, len(data), 8):
        chunk = data[offset : offset + 8]
        beats.append(
            StreamBeat(
                data=_beat_data(chunk),
                keep=(1 << len(chunk)) - 1,
                first=offset == 0,
                last=offset + len(chunk) == len(data),
                seed=seed if offset == 0 else 0xA55A,
            )
        )
    return beats


def _accept(model: ChecksumCycleModel, beat: StreamBeat) -> CycleObservation:
    observation = model.step(beat, response_ready=False)
    assert observation.request_ready is True
    assert observation.request_accepted is True
    return observation


def _pending_result(model: ChecksumCycleModel) -> StreamResult:
    observation = model.step(None, response_ready=False)
    assert observation.response_valid is True
    assert observation.result is not None
    return observation.result


@pytest.mark.parametrize(
    ("payload", "seed"),
    [
        (bytes.fromhex("12345678"), 0),
        (bytes.fromhex("123456"), 0),
        (bytes.fromhex("ffff0001ffff"), 0),
        (bytes.fromhex("000100020003"), 0xBEEF),
        (bytes(range(1, 22)), 0x1234),
    ],
)
def test_successful_packets_match_independent_network_byte_order_oracle(payload, seed):
    model = ChecksumCycleModel()

    for beat in _packet_beats(payload, seed):
        _accept(model, beat)

    checksum, folded_sum = _checksum_oracle(payload, seed)
    assert _pending_result(model) == StreamResult(
        status=StreamStatus.SUCCESS,
        checksum=checksum,
        folded_sum=folded_sum,
        byte_length=len(payload),
    )


@pytest.mark.parametrize(
    ("valid_bytes", "poison_bytes"),
    [
        (bytes.fromhex("12345678"), bytes.fromhex("aabbccdd")),
        (bytes.fromhex("123456"), bytes.fromhex("a1b2c3d4e5")),
    ],
)
def test_final_beat_ignores_nonzero_lanes_above_keep(valid_bytes, poison_bytes):
    model = ChecksumCycleModel()
    observation = model.step(
        StreamBeat(
            data=_beat_data(valid_bytes + poison_bytes),
            keep=(1 << len(valid_bytes)) - 1,
            first=True,
            last=True,
        ),
        response_ready=False,
    )
    checksum, folded_sum = _checksum_oracle(valid_bytes)

    assert observation.result == StreamResult(
        StreamStatus.SUCCESS,
        checksum,
        folded_sum,
        len(valid_bytes),
    )


def test_randomized_packets_match_oracle_across_fixed_seeds_and_backpressure():
    random = Random(0x64C1C1E)
    comparison_count = 0

    for seed in range(64):
        payload = random.randbytes(random.randint(1, 257))
        model = ChecksumCycleModel()

        final_observation = None
        for beat in _packet_beats(payload, seed):
            for _ in range(random.randrange(3)):
                idle = model.step(None, response_ready=bool(random.randrange(2)))
                assert idle.request_accepted is False
            final_observation = _accept(model, beat)

        expected_checksum, expected_sum = _checksum_oracle(payload, seed)
        assert final_observation is not None
        assert final_observation.result == StreamResult(
            StreamStatus.SUCCESS,
            expected_checksum,
            expected_sum,
            len(payload),
        )
        stalled_cycles = random.randrange(6)
        for _ in range(stalled_cycles):
            stalled = model.step(None, response_ready=False)
            assert stalled.response_valid is True
            assert stalled.result == StreamResult(
                StreamStatus.SUCCESS,
                expected_checksum,
                expected_sum,
                len(payload),
            )

        completed = model.step(None, response_ready=True)
        assert completed.response_valid is False
        assert completed.result is None
        comparison_count += 1

    assert comparison_count == 64


@pytest.mark.parametrize(
    ("first_beat", "second_beat", "status", "length"),
    [
        (
            StreamBeat(0, 0xFF, False, True),
            None,
            StreamStatus.MISSING_FIRST,
            0,
        ),
        (
            StreamBeat(0x0706050403020100, 0xFF, True, False),
            StreamBeat(0, 0xFF, True, True),
            StreamStatus.UNEXPECTED_FIRST,
            8,
        ),
        (
            StreamBeat(0, 0x7F, True, False),
            None,
            StreamStatus.INVALID_KEEP,
            0,
        ),
        (
            StreamBeat(0, 0x55, True, True),
            None,
            StreamStatus.INVALID_KEEP,
            0,
        ),
        (
            StreamBeat(0, 0, True, True),
            None,
            StreamStatus.EMPTY_FINAL,
            0,
        ),
    ],
)
def test_protocol_errors_terminate_without_counting_the_offending_beat(
    first_beat, second_beat, status, length
):
    model = ChecksumCycleModel()
    _accept(model, first_beat)
    if second_beat is not None:
        _accept(model, second_beat)

    assert _pending_result(model) == StreamResult(status, 0, 0, length)

    model.step(None, response_ready=True)
    _accept(model, StreamBeat(0x34, 0x01, True, True))
    assert _pending_result(model).status is StreamStatus.SUCCESS


def test_length_width_accepts_exact_maximum_of_fifteen_bytes():
    payload = bytes(range(15))
    model = ChecksumCycleModel(length_width=4)

    observation = None
    for beat in _packet_beats(payload):
        observation = _accept(model, beat)

    checksum, folded_sum = _checksum_oracle(payload)
    assert observation is not None
    assert observation.result == StreamResult(
        StreamStatus.SUCCESS,
        checksum,
        folded_sum,
        15,
    )


def test_length_width_rejects_sixteen_bytes_and_reports_prior_eight():
    model = ChecksumCycleModel(length_width=4)
    _accept(model, StreamBeat(0x0706050403020100, 0xFF, True, False))
    _accept(model, StreamBeat(0x0F0E0D0C0B0A0908, 0xFF, False, True))

    assert _pending_result(model) == StreamResult(
        StreamStatus.LENGTH_OVERFLOW,
        0,
        0,
        8,
    )


def test_output_and_packet_state_are_invariant_under_response_stalls():
    payload = bytes(range(1, 30))
    follow_up = bytes.fromhex("deadbeef01")
    stalled_model = ChecksumCycleModel()
    control_model = ChecksumCycleModel()

    for beat in _packet_beats(payload, seed=0x1111):
        _accept(stalled_model, beat)
        _accept(control_model, beat)

    expected = _pending_result(control_model)
    for _ in range(9):
        observation = stalled_model.step(None, response_ready=False)
        assert observation.response_valid is True
        assert observation.result == expected

    stalled_model.step(None, response_ready=True)
    control_model.step(None, response_ready=True)
    for beat in _packet_beats(follow_up, seed=0x2222):
        _accept(stalled_model, beat)
        _accept(control_model, beat)

    assert _pending_result(stalled_model) == _pending_result(control_model)


def test_response_can_retire_as_a_single_beat_result_replaces_it():
    model = ChecksumCycleModel()
    first_payload = bytes.fromhex("1234")
    second_payload = bytes.fromhex("56789a")

    _accept(model, _packet_beats(first_payload)[0])
    first_result = _checksum_oracle(first_payload)
    assert _pending_result(model) == StreamResult(
        StreamStatus.SUCCESS,
        first_result[0],
        first_result[1],
        len(first_payload),
    )
    replacement_edge = model.step(
        _packet_beats(second_payload, seed=7)[0],
        response_ready=True,
    )

    second_result = _checksum_oracle(second_payload, 7)
    assert replacement_edge.request_ready is True
    assert replacement_edge.request_accepted is True
    assert replacement_edge.response_valid is True
    assert replacement_edge.result == StreamResult(
        StreamStatus.SUCCESS,
        second_result[0],
        second_result[1],
        len(second_payload),
    )

    assert _pending_result(model) == StreamResult(
        StreamStatus.SUCCESS,
        second_result[0],
        second_result[1],
        len(second_payload),
    )


def test_reset_clears_active_packet_and_pending_response():
    model = ChecksumCycleModel()
    _accept(model, StreamBeat(0x0706050403020100, 0xFF, True, False))

    reset_edge = model.step(None, response_ready=False, reset_n=False)
    assert reset_edge.response_valid is False
    _accept(model, StreamBeat(0, 0x01, False, True))
    assert _pending_result(model).status is StreamStatus.MISSING_FIRST

    model.step(None, response_ready=True)
    _accept(model, StreamBeat(0x12, 0x01, True, True))
    assert _pending_result(model).status is StreamStatus.SUCCESS
    model.step(None, response_ready=False, reset_n=False)
    after_reset = model.step(None, response_ready=False)
    assert after_reset.response_valid is False
    assert after_reset.result is None


def test_request_is_not_accepted_while_response_is_stalled():
    model = ChecksumCycleModel()
    _accept(model, StreamBeat(0x12, 0x01, True, True))
    blocked = model.step(
        StreamBeat(0x34, 0x01, True, True),
        response_ready=False,
    )

    assert blocked.request_ready is False
    assert blocked.request_accepted is False
    model.step(None, response_ready=True)
    assert model.step(None, response_ready=False).response_valid is False


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("data", True, TypeError),
        ("data", -1, ValueError),
        ("data", 1 << 64, ValueError),
        ("keep", False, TypeError),
        ("keep", -1, ValueError),
        ("keep", 256, ValueError),
        ("first", 1, TypeError),
        ("last", 0, TypeError),
        ("seed", True, TypeError),
        ("seed", -1, ValueError),
        ("seed", 1 << 16, ValueError),
    ],
)
def test_stream_beat_strictly_validates_fields(field, value, exception):
    arguments = {"data": 0, "keep": 1, "first": True, "last": True, "seed": 0}
    arguments[field] = value

    with pytest.raises(exception):
        StreamBeat(**arguments)


@pytest.mark.parametrize(
    ("arguments", "exception"),
    [
        ({"status": 0, "checksum": 0, "folded_sum": 0, "byte_length": 0}, TypeError),
        (
            {
                "status": StreamStatus.SUCCESS,
                "checksum": True,
                "folded_sum": 0,
                "byte_length": 0,
            },
            TypeError,
        ),
        (
            {
                "status": StreamStatus.SUCCESS,
                "checksum": 0x1_0000,
                "folded_sum": 0,
                "byte_length": 0,
            },
            ValueError,
        ),
        (
            {
                "status": StreamStatus.SUCCESS,
                "checksum": 0,
                "folded_sum": -1,
                "byte_length": 0,
            },
            ValueError,
        ),
        (
            {
                "status": StreamStatus.SUCCESS,
                "checksum": 0,
                "folded_sum": 0,
                "byte_length": False,
            },
            TypeError,
        ),
        (
            {
                "status": StreamStatus.SUCCESS,
                "checksum": 0,
                "folded_sum": 0,
                "byte_length": -1,
            },
            ValueError,
        ),
    ],
)
def test_stream_result_strictly_validates_fields(arguments, exception):
    with pytest.raises(exception):
        StreamResult(**arguments)


def test_records_are_immutable_and_slotted():
    beat = StreamBeat(0, 1, True, True)
    result = StreamResult(StreamStatus.SUCCESS, 0xFFFF, 0, 1)
    observation = CycleObservation(True, True, False, None)

    with pytest.raises((AttributeError, TypeError)):
        beat.data = 1
    with pytest.raises((AttributeError, TypeError)):
        result.checksum = 0
    with pytest.raises((AttributeError, TypeError)):
        observation.request_ready = False
    assert not hasattr(beat, "__dict__")
    assert not hasattr(result, "__dict__")
    assert not hasattr(observation, "__dict__")


@pytest.mark.parametrize("length_width", [1, 16, 64])
def test_positive_integer_length_widths_are_supported(length_width):
    model = ChecksumCycleModel(length_width=length_width)
    model.reset()
    assert model.step(None, response_ready=False).request_ready is True


@pytest.mark.parametrize("length_width", [True, 1.5, "16"])
def test_length_width_rejects_non_integer_types(length_width):
    with pytest.raises(TypeError):
        ChecksumCycleModel(length_width=length_width)


@pytest.mark.parametrize("length_width", [0, -1])
def test_length_width_rejects_non_positive_bounds(length_width):
    with pytest.raises(ValueError):
        ChecksumCycleModel(length_width=length_width)


@pytest.mark.parametrize(
    ("beat", "response_ready", "reset_n"),
    [
        (object(), False, True),
        (None, 0, True),
        (None, False, 1),
    ],
)
def test_step_strictly_validates_inputs(beat, response_ready, reset_n):
    with pytest.raises(TypeError):
        ChecksumCycleModel().step(
            beat,
            response_ready=response_ready,
            reset_n=reset_n,
        )


def test_cycle_model_does_not_depend_on_production_arithmetic_module():
    from fpga_packet_checksum_offload import cycle_model

    tree = ast.parse(inspect.getsource(cycle_model))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )

    assert all("arithmetic" not in module for module in imported_modules)


def test_exported_cycle_api_documents_stream_and_edge_contract():
    public_api = (
        StreamStatus,
        StreamBeat,
        StreamResult,
        CycleObservation,
        ChecksumCycleModel,
        ChecksumCycleModel.reset,
        ChecksumCycleModel.step,
    )
    documentation = " ".join(
        " ".join((inspect.getdoc(item) or "").split()) for item in public_api
    ).lower()

    assert all(inspect.getdoc(item) for item in public_api)
    for required_phrase in (
        "pre-edge",
        "post-edge",
        "reset",
        "ascending lane",
        "network byte order",
        "keep",
        "first beat",
        "error",
        "stall",
    ):
        assert required_phrase in documentation


def test_stable_cycle_api_is_reexported_from_package():
    import fpga_packet_checksum_offload as package

    assert package.ChecksumCycleModel is ChecksumCycleModel
    assert package.CycleObservation is CycleObservation
    assert package.StreamBeat is StreamBeat
    assert package.StreamResult is StreamResult
    assert package.StreamStatus is StreamStatus
