"""Tests for the per-row grading core (scripts/grade.py).

Each test isolates one grading dimension: deterministic match-kind here;
calibration, abstention, and evidence faithfulness in their own sections.
"""
from __future__ import annotations

import pytest

from scripts.grade import GradeResult, MatchKind, grade_row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _segment():
    return {"deal_size_band": "over_1m", "stage": "negotiation", "call_type": "deep_dive"}


def _grade(
    gold_value=None,
    sys_value=None,
    gold_confidence="high",
    sys_confidence="high",
    gold_evidence_quote=None,
    sys_evidence_quote=None,
    edge_case_tag=None,
) -> GradeResult:
    return grade_row(
        transcript_id="t1",
        field="economic_buyer",
        system="system_x",
        gold_value=gold_value,
        sys_value=sys_value,
        gold_confidence=gold_confidence,
        sys_confidence=sys_confidence,
        gold_evidence_quote=gold_evidence_quote,
        sys_evidence_quote=sys_evidence_quote,
        edge_case_tag=edge_case_tag,
        segment=_segment(),
    )


# ---------------------------------------------------------------------------
# Match kind (Task 5)
# ---------------------------------------------------------------------------

class TestMatchKind:
    def test_both_null_is_match_null(self):
        g = _grade(gold_value=None, sys_value=None)
        assert g.match == MatchKind.MATCH_NULL

    def test_gold_null_sys_nonnull_is_false_positive(self):
        g = _grade(gold_value=None, sys_value="Jane Smith")
        assert g.match == MatchKind.FALSE_POSITIVE

    def test_gold_nonnull_sys_null_is_false_negative(self):
        g = _grade(gold_value="Jane Smith", sys_value=None)
        assert g.match == MatchKind.FALSE_NEGATIVE

    def test_exact_string_match_is_match_exact(self):
        g = _grade(gold_value="Jane Smith", sys_value="Jane Smith")
        assert g.match == MatchKind.MATCH_EXACT

    def test_string_mismatch_flags_for_semantic_review(self):
        # Different surface form, possibly same entity — needs LLM judge.
        g = _grade(gold_value="Jane Smith, CFO", sys_value="Jane Smith")
        assert g.match == MatchKind.NEEDS_SEMANTIC_REVIEW

    def test_grade_result_preserves_identity_and_segment(self):
        g = _grade(gold_value="X", sys_value="X")
        assert g.transcript_id == "t1"
        assert g.field == "economic_buyer"
        assert g.system == "system_x"
        assert g.segment["deal_size_band"] == "over_1m"


# ---------------------------------------------------------------------------
# Confidence calibration (Task 6)
# ---------------------------------------------------------------------------

from scripts.grade import CalibrationKind


class TestCalibration:
    def test_high_confidence_correct_is_well_calibrated_high(self):
        g = _grade(gold_value="X", sys_value="X", sys_confidence="high")
        assert g.calibration_kind == CalibrationKind.WELL_CALIBRATED_HIGH

    def test_high_confidence_wrong_is_overconfident(self):
        g = _grade(gold_value=None, sys_value="X", sys_confidence="high")
        # match=FALSE_POSITIVE, confidence=high → overconfident
        assert g.calibration_kind == CalibrationKind.OVERCONFIDENT

    def test_high_confidence_false_negative_is_overconfident(self):
        # System says "I'm sure it's null" with high confidence, but gold has a value.
        # By the contract, value=null requires confidence=none — so this row should
        # also surface a contract warning (covered in Task 7).
        g = _grade(gold_value="X", sys_value=None, sys_confidence="high")
        assert g.calibration_kind == CalibrationKind.OVERCONFIDENT

    def test_medium_confidence_yields_well_calibrated_medium(self):
        g = _grade(gold_value="X", sys_value="X", sys_confidence="medium")
        assert g.calibration_kind == CalibrationKind.WELL_CALIBRATED_MEDIUM

    def test_low_confidence_yields_well_calibrated_low(self):
        # Low confidence is "appropriately humble" regardless of outcome.
        g = _grade(gold_value="X", sys_value="X", sys_confidence="low")
        assert g.calibration_kind == CalibrationKind.WELL_CALIBRATED_LOW
        g2 = _grade(gold_value="X", sys_value="Y", sys_confidence="low")
        # MATCH=NEEDS_SEMANTIC_REVIEW → calibration stays PENDING for low+review
        # but for our simpler rule, low+any-classified-match is well_calibrated_low.
        # For NEEDS_SEMANTIC_REVIEW, calibration is PENDING until the judge resolves.
        assert g2.calibration_kind == CalibrationKind.PENDING

    def test_abstention_appropriate_when_gold_is_null(self):
        g = _grade(gold_value=None, sys_value=None, sys_confidence="none")
        assert g.calibration_kind == CalibrationKind.APPROPRIATE_ABSTENTION

    def test_abstention_inappropriate_when_gold_non_null(self):
        # System said "I don't know" but the gold had a value — missed extraction.
        g = _grade(gold_value="X", sys_value=None, sys_confidence="none")
        assert g.calibration_kind == CalibrationKind.MISSED_EXTRACTION

    def test_gold_confidence_none_yields_not_applicable_when_system_is_high(self):
        # Labeler was uncertain → we don't grade calibration in either direction
        # against this gold row. System confidence is ignored for calibration here.
        g = _grade(gold_value="X", sys_value="X", gold_confidence="none", sys_confidence="high")
        assert g.calibration_kind == CalibrationKind.NOT_APPLICABLE

    def test_needs_semantic_review_leaves_calibration_pending(self):
        g = _grade(gold_value="Jane Smith, CFO", sys_value="Jane Smith", sys_confidence="high")
        assert g.match == MatchKind.NEEDS_SEMANTIC_REVIEW
        assert g.calibration_kind == CalibrationKind.PENDING


# ---------------------------------------------------------------------------
# Abstention contract warnings (Task 7)
# ---------------------------------------------------------------------------

class TestContractWarnings:
    def test_value_null_with_high_confidence_emits_warning(self):
        # Contract: value=null requires confidence=none.
        g = _grade(gold_value=None, sys_value=None, sys_confidence="high")
        assert g.contract_warning is not None
        assert "confidence" in g.contract_warning.lower()
        assert "value is null" in g.contract_warning.lower()

    def test_value_nonnull_with_none_confidence_emits_warning(self):
        # Opposite direction: value=non-null with confidence=none also violates the contract.
        g = _grade(gold_value="X", sys_value="Jane Smith", sys_confidence="none")
        assert g.contract_warning is not None
        assert "confidence" in g.contract_warning.lower()
        assert "non-null" in g.contract_warning.lower()

    def test_well_formed_abstention_emits_no_warning(self):
        g = _grade(gold_value=None, sys_value=None, sys_confidence="none")
        assert g.contract_warning is None

    def test_well_formed_extraction_emits_no_warning(self):
        g = _grade(gold_value="X", sys_value="X", sys_confidence="high")
        assert g.contract_warning is None


# ---------------------------------------------------------------------------
# Evidence faithfulness (Task 8)
# ---------------------------------------------------------------------------

from scripts.grade import EvidenceFaithfulness


class TestEvidenceFaithfulness:
    def test_identical_quotes_are_faithful(self):
        g = _grade(
            gold_value="Jane Smith",
            sys_value="Jane Smith",
            gold_evidence_quote="Jane will need to sign off on this.",
            sys_evidence_quote="Jane will need to sign off on this.",
        )
        assert g.evidence_faithfulness == EvidenceFaithfulness.FAITHFUL

    def test_system_substring_of_gold_is_faithful(self):
        g = _grade(
            gold_value="Jane Smith",
            sys_value="Jane Smith",
            gold_evidence_quote="I think Jane will sign off on these contracts.",
            sys_evidence_quote="Jane will sign off",
        )
        assert g.evidence_faithfulness == EvidenceFaithfulness.FAITHFUL

    def test_disjoint_quotes_are_unfaithful(self):
        g = _grade(
            gold_value="Jane Smith",
            sys_value="Jane Smith",
            gold_evidence_quote="Jane will sign off on this purchase.",
            sys_evidence_quote="The procurement team handles contracts.",
        )
        assert g.evidence_faithfulness == EvidenceFaithfulness.UNFAITHFUL

    def test_gold_quote_null_is_unverifiable(self):
        # Gold null, system provided a quote: nothing to compare against.
        g = _grade(
            gold_value=None,
            sys_value="Jane Smith",
            sys_confidence="high",
            gold_evidence_quote=None,
            sys_evidence_quote="Jane will sign off.",
        )
        assert g.evidence_faithfulness == EvidenceFaithfulness.UNVERIFIABLE

    def test_both_quotes_null_is_unverifiable(self):
        g = _grade(
            gold_value=None,
            sys_value=None,
            sys_confidence="none",
            gold_evidence_quote=None,
            sys_evidence_quote=None,
        )
        assert g.evidence_faithfulness == EvidenceFaithfulness.UNVERIFIABLE

    def test_sys_quote_null_with_gold_quote_is_unverifiable(self):
        g = _grade(
            gold_value="Jane Smith",
            sys_value=None,
            sys_confidence="none",
            gold_evidence_quote="Jane will sign off.",
            sys_evidence_quote=None,
        )
        assert g.evidence_faithfulness == EvidenceFaithfulness.UNVERIFIABLE

    def test_punctuation_and_case_normalized(self):
        g = _grade(
            gold_value="Jane Smith",
            sys_value="Jane Smith",
            gold_evidence_quote="Jane will sign off on this!",
            sys_evidence_quote="jane will sign off on this",
        )
        assert g.evidence_faithfulness == EvidenceFaithfulness.FAITHFUL
