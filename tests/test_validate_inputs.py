"""Tests for the schema validator (scripts/validate_inputs.py).

Includes the happy-path test and a parametrized adversarial suite across
ten distinct contract-violation modes. The validator must fail loudly on
every one of them — silent acceptance is how eval harnesses ship
confidently wrong scorecards.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_inputs import validate

FIXTURES = Path(__file__).parent / "fixtures"
MALFORMED = FIXTURES / "malformed"


def test_valid_inputs_pass(valid_golden_set_path, valid_extractions_dir, valid_thresholds_path):
    result = validate(
        golden_path=valid_golden_set_path,
        extractions_dir=valid_extractions_dir,
        thresholds_path=valid_thresholds_path,
    )
    assert result.ok, f"expected ok, got errors: {result.errors}"
    assert result.errors == []
    assert "system_a" in result.systems
    assert "v_001" in result.transcripts


# ---------------------------------------------------------------------------
# Adversarial: every contract violation must produce a recognizable error.
# ---------------------------------------------------------------------------

ADVERSARIAL_CASES = [
    # (fixture_name, expected_error_substring)
    ("missing_required_field", "'field' is a required property"),
    ("invalid_confidence_enum", "'extreme'"),
    ("invalid_segment", "'deal_size_band' is a required property"),
    ("non_json_line", "not valid JSON"),
    ("empty_golden_set", "empty"),
    ("extraction_missing_field", "missing field"),
    ("extraction_extra_field", "not declared"),
    ("extraction_inner_missing_keys", "'confidence' is a required property"),
    ("evidence_quote_not_null_for_null_value", "gold_evidence_quote must be null"),
    ("transcript_in_extraction_not_in_golden", "orphan"),
]


@pytest.mark.parametrize("fixture_name,expected_substring", ADVERSARIAL_CASES)
def test_malformed_inputs_fail_loudly(
    fixture_name, expected_substring, valid_thresholds_path
):
    fixture_dir = MALFORMED / fixture_name
    result = validate(
        golden_path=fixture_dir / "golden-set.jsonl",
        extractions_dir=fixture_dir / "extractions",
        thresholds_path=valid_thresholds_path,
    )
    assert not result.ok, (
        f"fixture '{fixture_name}' should have failed validation but didn't. "
        f"errors={result.errors} warnings={result.warnings}"
    )
    assert any(expected_substring in e for e in result.errors), (
        f"fixture '{fixture_name}': expected substring '{expected_substring}' in "
        f"errors, got: {result.errors}"
    )
