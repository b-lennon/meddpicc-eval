"""Tests for the threshold-application layer (scripts/apply_thresholds.py).

Threshold checks turn aggregated metrics into pass/fail flags. System
comparison turns paired aggregates into regression flags. Both are pure
arithmetic — no judgment.
"""
from __future__ import annotations

import pytest

from scripts.apply_thresholds import (
    FieldStatus,
    RegressionStatus,
    apply_thresholds,
    compare_systems,
)


THRESHOLDS = {
    "fields": {
        "economic_buyer": {
            "weight": 3.0,
            "min_precision_high": 0.98,
            "min_recall": 0.85,
            "max_abstention_rate": 0.40,
            "regression_tolerance": 0.00,
        },
        "metrics": {
            "weight": 1.0,
            "min_precision_high": 0.90,
            "min_recall": 0.75,
            "max_abstention_rate": 0.50,
            "regression_tolerance": 0.03,
        },
    },
    "migration_rules": {
        "ship": "all pass",
        "ship_segment": "split",
        "hold": "load-bearing regresses",
    },
}


def _fm(**overrides):
    """Build a FieldMetrics-shaped dict (matches what aggregate emits)."""
    base = {
        "field": "economic_buyer",
        "n_total": 100,
        "precision": 0.95,
        "recall": 0.90,
        "f1": 0.92,
        "abstention_rate": 0.10,
        "precision_high": 0.99,
        "n_high_confidence": 80,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# apply_thresholds — single system → per-field pass/fail
# ---------------------------------------------------------------------------

class TestApplyThresholds:
    def test_all_above_threshold_passes(self):
        metrics = {"economic_buyer": _fm(precision_high=0.99, recall=0.90, abstention_rate=0.10)}
        result = apply_thresholds(metrics, THRESHOLDS)
        assert result["economic_buyer"].overall == FieldStatus.PASS
        assert result["economic_buyer"].failed_checks == []

    def test_precision_high_below_threshold_fails(self):
        metrics = {"economic_buyer": _fm(precision_high=0.93, recall=0.90, abstention_rate=0.10)}
        result = apply_thresholds(metrics, THRESHOLDS)
        assert result["economic_buyer"].overall == FieldStatus.FAIL
        assert "precision_high" in result["economic_buyer"].failed_checks

    def test_recall_below_threshold_fails(self):
        metrics = {"economic_buyer": _fm(precision_high=0.99, recall=0.70, abstention_rate=0.10)}
        result = apply_thresholds(metrics, THRESHOLDS)
        assert result["economic_buyer"].overall == FieldStatus.FAIL
        assert "recall" in result["economic_buyer"].failed_checks

    def test_abstention_rate_above_max_fails(self):
        metrics = {"economic_buyer": _fm(precision_high=0.99, recall=0.90, abstention_rate=0.50)}
        result = apply_thresholds(metrics, THRESHOLDS)
        assert result["economic_buyer"].overall == FieldStatus.FAIL
        assert "abstention_rate" in result["economic_buyer"].failed_checks

    def test_multiple_failures_collected(self):
        metrics = {"economic_buyer": _fm(precision_high=0.50, recall=0.40, abstention_rate=0.80)}
        result = apply_thresholds(metrics, THRESHOLDS)
        assert result["economic_buyer"].overall == FieldStatus.FAIL
        assert set(result["economic_buyer"].failed_checks) == {"precision_high", "recall", "abstention_rate"}

    def test_evaluates_every_declared_field(self):
        metrics = {
            "economic_buyer": _fm(precision_high=0.99, recall=0.90, abstention_rate=0.10),
            "metrics": _fm(precision_high=0.85, recall=0.78, abstention_rate=0.30),  # precision below 0.90
        }
        result = apply_thresholds(metrics, THRESHOLDS)
        assert result["economic_buyer"].overall == FieldStatus.PASS
        assert result["metrics"].overall == FieldStatus.FAIL


# ---------------------------------------------------------------------------
# compare_systems — paired aggregates → regression flags
# ---------------------------------------------------------------------------

class TestCompareSystems:
    def test_no_change_is_no_regression(self):
        current = {"economic_buyer": _fm(precision_high=0.94)}
        candidate = {"economic_buyer": _fm(precision_high=0.94)}
        result = compare_systems(current, candidate, THRESHOLDS)
        assert result["economic_buyer"].status == RegressionStatus.NO_REGRESSION

    def test_improvement(self):
        current = {"economic_buyer": _fm(precision_high=0.94)}
        candidate = {"economic_buyer": _fm(precision_high=0.96)}
        result = compare_systems(current, candidate, THRESHOLDS)
        assert result["economic_buyer"].status == RegressionStatus.IMPROVEMENT

    def test_eb_zero_tolerance_any_drop_is_regression(self):
        # economic_buyer.regression_tolerance = 0.00 → any drop counts.
        current = {"economic_buyer": _fm(precision_high=0.94)}
        candidate = {"economic_buyer": _fm(precision_high=0.93)}
        result = compare_systems(current, candidate, THRESHOLDS)
        assert result["economic_buyer"].status == RegressionStatus.REGRESSION

    def test_metrics_tolerance_allows_small_drop(self):
        # metrics.regression_tolerance = 0.03
        current = {"metrics": _fm(precision_high=0.85)}
        candidate = {"metrics": _fm(precision_high=0.83)}  # -0.02
        result = compare_systems(current, candidate, THRESHOLDS)
        assert result["metrics"].status == RegressionStatus.NO_REGRESSION

    def test_metrics_tolerance_breached(self):
        current = {"metrics": _fm(precision_high=0.85)}
        candidate = {"metrics": _fm(precision_high=0.80)}  # -0.05
        result = compare_systems(current, candidate, THRESHOLDS)
        assert result["metrics"].status == RegressionStatus.REGRESSION

    def test_records_delta(self):
        current = {"economic_buyer": _fm(precision_high=0.94)}
        candidate = {"economic_buyer": _fm(precision_high=0.91)}
        result = compare_systems(current, candidate, THRESHOLDS)
        assert result["economic_buyer"].delta_precision_high == pytest.approx(-0.03)