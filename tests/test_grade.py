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
