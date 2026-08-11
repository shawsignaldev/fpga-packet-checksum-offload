import os
import subprocess
import sys
from importlib import util
from pathlib import Path

import pytest

from fpga_packet_checksum_offload.campaign import (
    CampaignCase,
    CampaignResult,
    run_campaign,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
VECTOR_SNAPSHOT = REPOSITORY_ROOT / "tests" / "vectors" / "checksum_vectors.txt"
VECTOR_INCLUDE = REPOSITORY_ROOT / "tests" / "vectors" / "checksum_vectors.svh"
VECTOR_GENERATOR = REPOSITORY_ROOT / "tools" / "generate_rtl_vectors.py"

EXPECTED_BEHAVIORS = {
    "carry-folding",
    "empty-final",
    "even-length",
    "invalid-nonfinal-keep",
    "length-overflow",
    "missing-first",
    "multibeat-pairing",
    "odd-length",
    "reset-recovery",
    "seeded-sum",
    "sparse-final-keep",
    "stalled-output-stability",
    "unexpected-first",
    "zero-bubble-replacement",
}


def _load_vector_generator():
    spec = util.spec_from_file_location("checksum_vector_generator", VECTOR_GENERATOR)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_campaign_passes_and_covers_every_required_behavior_deterministically():
    first = run_campaign()
    second = run_campaign()

    assert first == second
    assert first.total == len(first.cases)
    assert first.passed == first.total
    assert first.failed_case_names == ()
    assert set(first.covered_behavior_names) == EXPECTED_BEHAVIORS
    assert first.summary == second.summary
    assert first.summary.startswith("checksum-cycle-campaign-v1 ")
    assert f"{first.passed}/{first.total} passed" in first.summary
    assert first.summary.isascii()


def test_campaign_records_are_immutable_slotted_and_reexported():
    import fpga_packet_checksum_offload as package

    result = run_campaign()
    case = result.cases[0]
    assert isinstance(case, CampaignCase)
    assert isinstance(result, CampaignResult)
    assert package.CampaignCase is CampaignCase
    assert package.CampaignResult is CampaignResult
    assert package.run_campaign is run_campaign

    with pytest.raises((AttributeError, TypeError)):
        case.passed = False
    with pytest.raises((AttributeError, TypeError)):
        result.total = 0
    assert not hasattr(case, "__dict__")
    assert not hasattr(result, "__dict__")


def test_optimized_campaign_detects_a_deliberately_corrupted_expectation():
    script = f"""
import sys
sys.path.insert(0, {str(SOURCE_ROOT)!r})
from fpga_packet_checksum_offload import campaign
from fpga_packet_checksum_offload.cycle_model import StreamResult

original_expected = campaign._expected

def corrupted_expected(data, seed=0):
    result = original_expected(data, seed)
    return StreamResult(
        result.status,
        result.checksum ^ 1,
        result.folded_sum,
        result.byte_length,
    )

campaign._expected = corrupted_expected
result = campaign.run_campaign()
if result.passed == result.total:
    raise SystemExit("optimized campaign silently passed corrupted expectation")
print(",".join(result.failed_case_names))
"""
    completed = subprocess.run(
        [sys.executable, "-O", "-c", script],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    assert "even_length" in completed.stdout


def test_vector_generation_is_byte_deterministic_and_matches_snapshot(tmp_path):
    first_output = tmp_path / "first.txt"
    second_output = tmp_path / "second.txt"
    first_include = tmp_path / "first.svh"
    second_include = tmp_path / "second.svh"

    for output, include in (
        (first_output, first_include),
        (second_output, second_include),
    ):
        completed = subprocess.run(
            [
                sys.executable,
                str(VECTOR_GENERATOR),
                "--output",
                str(output),
                "--sv-output",
                str(include),
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    expected = VECTOR_SNAPSHOT.read_bytes()
    assert first_output.read_bytes() == expected
    assert second_output.read_bytes() == expected
    expected_include = VECTOR_INCLUDE.read_bytes()
    assert first_include.read_bytes() == expected_include
    assert second_include.read_bytes() == expected_include
    assert b"CHECKSUM_VECTOR_INCLUDE_FORMAT 1" in expected_include
    assert b"task automatic run_checksum_vectors_v1" in expected_include
    assert b"VECTOR_FORMAT checksum16_stream_cycle 1\n" in expected
    assert b"FIELDS " in expected
    assert b"CASE " in expected
    assert b"CYCLE " in expected
    assert b"SUCCESS" in expected
    assert b"LENGTH_OVERFLOW" in expected
    assert b"CASE poisoned_partial_even LENGTH_WIDTH 16\n" in expected
    assert b"CASE poisoned_partial_odd LENGTH_WIDTH 16\n" in expected
    assert b"ddccbbaa78563412 0f" in expected
    assert b"e5d4c3b2a1563412 07" in expected
    assert b"CASE length_boundary_success LENGTH_WIDTH 4\n" in expected
    assert b"CASE length_boundary_overflow LENGTH_WIDTH 4\n" in expected
    assert b"SUCCESS" in expected and b" 15\n" in expected
    assert b"LENGTH_OVERFLOW 0000 0000 8\n" in expected
    expected.decode("ascii")
    expected_include.decode("ascii")


def test_sv_include_is_parsed_from_canonical_text_and_changes_with_any_field():
    generator = _load_vector_generator()
    canonical = generator.render_vectors()
    rendered = generator.render_sv_include(canonical)

    assert rendered.encode("ascii") == VECTOR_INCLUDE.read_bytes()
    for old, new in (
        ("0000000078563412", "0000000078563413"),
        (" 0f 1 1 0000 ", " 07 1 1 0000 "),
        (" SUCCESS 9753 68ac 4", " SUCCESS 9752 68ac 4"),
        (" response_fields_post_edge", " response_fields_post_edgf"),
    ):
        mutated = canonical.replace(old, new, 1)
        assert mutated != canonical
        assert generator.render_sv_include(mutated) != rendered


def test_vector_check_mode_reports_missing_without_creating_file(tmp_path):
    output = tmp_path / "missing.txt"
    include = tmp_path / "missing.svh"
    completed = subprocess.run(
        [
            sys.executable,
            str(VECTOR_GENERATOR),
            "--output",
            str(output),
            "--sv-output",
            str(include),
            "--check",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "missing" in completed.stderr.lower()
    assert not output.exists()
    assert not include.exists()


@pytest.mark.parametrize("stale_artifact", ("text", "include"))
def test_vector_check_mode_reports_stale_without_rewriting_files(
    tmp_path, stale_artifact
):
    output = tmp_path / "stale.txt"
    include = tmp_path / "stale.svh"
    generated = subprocess.run(
        [
            sys.executable,
            str(VECTOR_GENERATOR),
            "--output",
            str(output),
            "--sv-output",
            str(include),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stderr
    target = output if stale_artifact == "text" else include
    target.write_bytes(b"stale\n")
    os.utime(output, ns=(1_000_000_000, 1_000_000_000))
    os.utime(include, ns=(1_000_000_000, 1_000_000_000))
    before_output = (output.read_bytes(), output.stat().st_mtime_ns)
    before_include = (include.read_bytes(), include.stat().st_mtime_ns)

    completed = subprocess.run(
        [
            sys.executable,
            str(VECTOR_GENERATOR),
            "--check",
            "--output",
            str(output),
            "--sv-output",
            str(include),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "stale" in completed.stderr.lower()
    assert (output.read_bytes(), output.stat().st_mtime_ns) == before_output
    assert (include.read_bytes(), include.stat().st_mtime_ns) == before_include


def test_vector_check_mode_accepts_current_file_without_rewriting_it(tmp_path):
    output = tmp_path / "current.txt"
    include = tmp_path / "current.svh"
    generated = subprocess.run(
        [
            sys.executable,
            str(VECTOR_GENERATOR),
            "--output",
            str(output),
            "--sv-output",
            str(include),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stderr
    os.utime(output, ns=(1_000_000_000, 1_000_000_000))
    os.utime(include, ns=(1_000_000_000, 1_000_000_000))
    before_output = (output.read_bytes(), output.stat().st_mtime_ns)
    before_include = (include.read_bytes(), include.stat().st_mtime_ns)

    checked = subprocess.run(
        [
            sys.executable,
            str(VECTOR_GENERATOR),
            "--output",
            str(output),
            "--sv-output",
            str(include),
            "--check",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert checked.returncode == 0, checked.stderr
    assert checked.stderr == ""
    assert (output.read_bytes(), output.stat().st_mtime_ns) == before_output
    assert (include.read_bytes(), include.stat().st_mtime_ns) == before_include


def test_vector_check_mode_ties_include_to_on_disk_text_fields(tmp_path):
    output = tmp_path / "field-mutated.txt"
    include = tmp_path / "field-mutated.svh"
    generated = subprocess.run(
        [
            sys.executable,
            str(VECTOR_GENERATOR),
            "--output",
            str(output),
            "--sv-output",
            str(include),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stderr
    output.write_text(
        output.read_text(encoding="ascii").replace(
            " SUCCESS 9753 ", " SUCCESS 9752 ", 1
        ),
        encoding="ascii",
        newline="\n",
    )
    before = (output.read_bytes(), include.read_bytes())

    checked = subprocess.run(
        [
            sys.executable,
            str(VECTOR_GENERATOR),
            "--check",
            "--output",
            str(output),
            "--sv-output",
            str(include),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert checked.returncode != 0
    assert "vector file stale" in checked.stderr
    assert "SV include file stale" in checked.stderr
    assert (output.read_bytes(), include.read_bytes()) == before
