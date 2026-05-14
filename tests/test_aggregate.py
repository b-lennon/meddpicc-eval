"""Tests for the aggregation layer (scripts/aggregate.py).

Aggregation consumes confirmed-grades.jsonl and rolls per-row outcomes
up to per-system, per-field metrics. The downstream threshold and
verdict steps depend on these numbers being arithmetically right.
"""
from __future__ import annotations

import math

import pytest

from scripts.aggregate import (
    FieldMetrics,
    aggregate_field,
    aggregate_field_by_edge_case,
    aggregate_field_by_segment,
)


def _row(
    match,
    field="economic_buyer",
    system="sys_a",
    sys_confidence="high",
    gold_confidence="high",
    edge_case_tag=None,
    segment=None,
    transcript_id="t1",
):
    if segment is None:
        segment = {"deal_size_band": "over_1m", "stage": "negotiation", "call_type": "deep_dive"}
    return {
        "transcript_id": transcript_id,
        "field": field,
        "system": system,
        "match": match,
        "gold_value": "X" if match in ("match_exact", "match_semantic", "false_negative", "mismatch") else None,
        "sys_value": "X" if match in ("match_exact", "match_semantic", "false_positive", "mismatch") else None,
        "gold_confidence": gold_confidence,
        "sys_confidence": sys_confidence,
        "edge_case_tag": edge_case_tag,
        "segment": segment,
        "calibration_kind": "well_calibrated_high",
        "evidence_faithfulness": "unverifiable",
        "contract_warning": None,
    }


# ---------------------------------------------------------------------------
# Per-field precision / recall / F1 / abstention (Task 10)
# ---------------------------------------------------------------------------

class TestFieldMetrics:
    def test_all_correct_yields_perfect_precision_and_recall(self):
        grades = [_row("match_exact") for _ in range(10)]
        m = aggregate_field("economic_buyer", grades)
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.abstention_rate == 0.0
        assert m.precision_high == 1.0
        assert m.n_total == 10

    def test_false_positives_drop_precision(self):
        grades = [_row("match_exact") for _ in range(8)] + [_row("false_positive") for _ in range(2)]
        m = aggregate_field("economic_buyer", grades)
        assert m.precision == pytest.approx(8 / 10)  # 8 TP / (8 TP + 2 FP)
        assert m.recall == 1.0  # No false negatives or mismatches

    def test_false_negatives_drop_recall(self):
        grades = [_row("match_exact") for _ in range(8)] + [_row("false_negative") for _ in range(2)]
        m = aggregate_field("economic_buyer", grades)
        assert m.precision == 1.0  # All non-null sys values were correct
        assert m.recall == pytest.approx(8 / 10)  # 8 TP / (8 TP + 2 FN)

    def test_mismatch_counts_as_both_fp_and_fn(self):
        grades = [_row("match_exact") for _ in range(8)] + [_row("mismatch") for _ in range(2)]
        m = aggregate_field("economic_buyer", grades)
        # 8 TP, 2 FP, 2 FN
        assert m.precision == pytest.approx(8 / 10)
        assert m.recall == pytest.approx(8 / 10)

    def test_match_null_does_not_affect_precision_or_recall(self):
        # Abstention agreed by both: neither a positive prediction nor a true positive.
        grades = [_row("match_exact") for _ in range(5)] + [_row("match_null") for _ in range(5)]
        m = aggregate_field("economic_buyer", grades)
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.abstention_rate == 0.5

    def test_abstention_rate_counts_null_sys_values(self):
        grades = [_row("match_exact") for _ in range(7)] + [_row("match_null") for _ in range(3)]
        m = aggregate_field("economic_buyer", grades)
        assert m.abstention_rate == pytest.approx(3 / 10)

    def test_precision_high_restricts_to_high_confidence(self):
        # High-confidence predictions: 4 correct, 1 wrong.
        # Medium-confidence: 2 correct, 0 wrong. Should not affect precision_high.
        grades = (
            [_row("match_exact", sys_confidence="high") for _ in range(4)]
            + [_row("false_positive", sys_confidence="high")]
            + [_row("match_exact", sys_confidence="medium") for _ in range(2)]
        )
        m = aggregate_field("economic_buyer", grades)
        assert m.precision_high == pytest.approx(4 / 5)

    def test_gold_confidence_none_excluded_from_calibration_metrics(self):
        # Labeler-uncertain rows must not pollute precision_high.
        grades = (
            [_row("match_exact", sys_confidence="high") for _ in range(3)]
            + [_row("match_exact", sys_confidence="high", gold_confidence="none") for _ in range(7)]
        )
        m = aggregate_field("economic_buyer", grades)
        assert m.precision_high == pytest.approx(3 / 3)

    def test_f1_harmonic_mean(self):
        grades = (
            [_row("match_exact") for _ in range(6)]
            + [_row("false_positive") for _ in range(2)]
            + [_row("false_negative") for _ in range(2)]
        )
        m = aggregate_field("economic_buyer", grades)
        # precision = 6 / 8 = 0.75; recall = 6 / 8 = 0.75; f1 = 0.75
        assert m.f1 == pytest.approx(0.75)

    def test_no_predictions_no_divide_by_zero(self):
        grades = [_row("match_null") for _ in range(3)]  # All abstentions
        m = aggregate_field("economic_buyer", grades)
        # Precision is undefined; we report 0.0 with a flag rather than NaN.
        assert m.precision == 0.0 or math.isnan(m.precision)
        assert m.abstention_rate == 1.0

    def test_filters_to_named_field(self):
        grades = [
            _row("match_exact", field="economic_buyer") for _ in range(5)
        ] + [_row("false_positive", field="metrics") for _ in range(5)]
        m_eb = aggregate_field("economic_buyer", grades)
        m_metrics = aggregate_field("metrics", grades)
        assert m_eb.precision == 1.0
        assert m_metrics.precision == 0.0
        assert m_eb.n_total == 5
        assert m_metrics.n_total == 5


# ---------------------------------------------------------------------------
# Segment breakdown (Task 11)
# ---------------------------------------------------------------------------

def _seg(deal, stage="discovery", call="discovery"):
    return {"deal_size_band": deal, "stage": stage, "call_type": call}


class TestSegmentBreakdown:
    def test_groups_by_deal_size_band(self):
        grades = (
            [_row("match_exact", segment=_seg("over_1m")) for _ in range(3)]
            + [_row("false_positive", segment=_seg("over_1m"))]
            + [_row("match_exact", segment=_seg("under_250k")) for _ in range(5)]
        )
        by_seg = aggregate_field_by_segment("economic_buyer", grades)
        assert by_seg["deal_size_band"]["over_1m"].precision == pytest.approx(3 / 4)
        assert by_seg["deal_size_band"]["under_250k"].precision == 1.0

    def test_omits_empty_segments(self):
        grades = [_row("match_exact", segment=_seg("over_1m")) for _ in range(3)]
        by_seg = aggregate_field_by_segment("economic_buyer", grades)
        # Only over_1m was present.
        assert "over_1m" in by_seg["deal_size_band"]
        assert "under_250k" not in by_seg["deal_size_band"]

    def test_breaks_down_along_all_three_dimensions(self):
        grades = [_row("match_exact", segment=_seg("over_1m", "negotiation", "deep_dive")) for _ in range(2)]
        by_seg = aggregate_field_by_segment("economic_buyer", grades)
        assert "over_1m" in by_seg["deal_size_band"]
        assert "negotiation" in by_seg["stage"]
        assert "deep_dive" in by_seg["call_type"]


# ---------------------------------------------------------------------------
# Edge-case breakdown (Task 12)
# ---------------------------------------------------------------------------

class TestEdgeCaseBreakdown:
    def test_groups_by_edge_case_tag(self):
        grades = (
            [_row("match_exact", edge_case_tag="champion_not_eb") for _ in range(5)]
            + [_row("false_positive", edge_case_tag="champion_not_eb") for _ in range(5)]
            + [_row("match_exact", edge_case_tag="clear_eb_stated") for _ in range(8)]
        )
        by_tag = aggregate_field_by_edge_case("economic_buyer", grades)
        assert by_tag["champion_not_eb"].precision == pytest.approx(5 / 10)
        assert by_tag["clear_eb_stated"].precision == 1.0

    def test_excludes_untagged_rows(self):
        grades = (
            [_row("match_exact", edge_case_tag="champion_not_eb") for _ in range(3)]
            + [_row("match_exact", edge_case_tag=None) for _ in range(5)]
        )
        by_tag = aggregate_field_by_edge_case("economic_buyer", grades)
        assert "champion_not_eb" in by_tag
        assert None not in by_tag
        assert by_tag["champion_not_eb"].n_total == 3

    def test_diagnosability_one_tag_one_field(self):
        # The diagnostic claim: if `champion_not_eb` precision regresses, the breakdown
        # surfaces it as a distinct number. This proves the tagging is not decorative.
        grades = (
            [_row("match_exact", edge_case_tag="champion_not_eb") for _ in range(2)]
            + [_row("mismatch", edge_case_tag="champion_not_eb") for _ in range(8)]
        )
        by_tag = aggregate_field_by_edge_case("economic_buyer", grades)
        # 2 TP, 8 FP and 8 FN; precision = 2/10 = 0.20
        assert by_tag["champion_not_eb"].precision == pytest.approx(0.2)