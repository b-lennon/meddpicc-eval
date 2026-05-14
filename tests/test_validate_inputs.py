"""Tests for the schema validator (scripts/validate_inputs.py).

Happy-path test in this module — adversarial tests live alongside in
parametrized form once the validator can fail loudly on contract violations.
"""
from __future__ import annotations

from scripts.validate_inputs import validate


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
