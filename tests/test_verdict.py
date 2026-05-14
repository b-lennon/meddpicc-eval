"""Tests for the migration verdict layer (scripts/verdict.py).

The verdict layer applies the migration rules from thresholds.yaml to
produce one of three outputs: ship / ship_segment / hold. This is the
test that proves the verdict logic works the way the one-pager claims it
does — including the 3%/5% scenario explicitly.
"""
from __future__ import annotations

import pytest

from scripts.apply_thresholds import FieldStatus, RegressionStatus
from scripts.verdict import Verdict, decide


THRESHOLDS = {
    "fields": {
        "economic_buyer": {
            "weight": 3.0, "min_precision_high": 0.98, "min_recall": 0.85,
            "max_abstention_rate": 0.40, "regression_tolerance": 0.00,
        },
        "metrics": {
            "weight": 1.0, "min_precision_high": 0.90, "min_recall": 0.75,
            "max_abstention_rate": 0.50, "regression_tolerance": 0.03,
        },
        "champion": {
            "weight": 2.0, "min_precision_high": 0.95, "min_recall": 0.80,
            "max_abstention_rate": 0.45, "regression_tolerance": 0.02,
        },
    },
    "migration_rules": {
        "ship": "all pass + no regression",
        "ship_segment": "non-load-bearing fail or segment split",
        "hold": "load-bearing regression or load-bearing threshold fail",
    },
}


def _tc(field, overall, failed=None):
    return {"field": field, "overall": overall, "failed_checks": failed or []}


def _rc(field, status, delta=0.0):
    return {
        "field": field,
        "status": status,
        "current_precision_high": 0.94,
        "candidate_precision_high": 0.94 + delta,
        "delta_precision_high": delta,
        "tolerance": 0.0,
    }


# ---------------------------------------------------------------------------
# Single-system mode (no regression check)
# ---------------------------------------------------------------------------

class TestSingleSystem:
    def test_all_pass_yields_ship(self):
        threshold_results = {
            "system_a": {
                "economic_buyer": _tc("economic_buyer", "pass"),
                "metrics": _tc("metrics", "pass"),
                "champion": _tc("champion", "pass"),
            },
        }
        v = decide(threshold_results, regressions={}, thresholds=THRESHOLDS)
        assert v.verdict == "ship"
        assert v.failing_fields == []

    def test_load_bearing_threshold_fail_yields_hold(self):
        threshold_results = {
            "system_a": {
                "economic_buyer": _tc("economic_buyer", "fail", ["precision_high"]),
                "metrics": _tc("metrics", "pass"),
                "champion": _tc("champion", "pass"),
            },
        }
        v = decide(threshold_results, regressions={}, thresholds=THRESHOLDS)
        assert v.verdict == "hold"
        assert "economic_buyer" in v.failing_fields
        assert "load-bearing" in v.reason.lower() or "economic_buyer" in v.reason

    def test_non_load_bearing_threshold_fail_yields_ship_segment(self):
        threshold_results = {
            "system_a": {
                "economic_buyer": _tc("economic_buyer", "pass"),
                "metrics": _tc("metrics", "fail", ["precision_high"]),
                "champion": _tc("champion", "pass"),
            },
        }
        v = decide(threshold_results, regressions={}, thresholds=THRESHOLDS)
        assert v.verdict == "ship_segment"


# ---------------------------------------------------------------------------
# Two-system mode (with regression check)
# ---------------------------------------------------------------------------

class TestTwoSystem:
    def test_all_pass_no_regression_is_ship(self):
        threshold_results = {
            "current_model_v2": {
                "economic_buyer": _tc("economic_buyer", "pass"),
                "metrics": _tc("metrics", "pass"),
                "champion": _tc("champion", "pass"),
            },
            "candidate_model_v3": {
                "economic_buyer": _tc("economic_buyer", "pass"),
                "metrics": _tc("metrics", "pass"),
                "champion": _tc("champion", "pass"),
            },
        }
        regressions = {
            "candidate_model_v3": {
                "economic_buyer": _rc("economic_buyer", "no_regression"),
                "metrics": _rc("metrics", "no_regression"),
                "champion": _rc("champion", "no_regression"),
            },
        }
        v = decide(threshold_results, regressions, thresholds=THRESHOLDS)
        assert v.verdict == "ship"

    def test_load_bearing_regression_is_hold(self):
        threshold_results = {
            "current_model_v2": {
                "economic_buyer": _tc("economic_buyer", "pass"),
                "metrics": _tc("metrics", "pass"),
                "champion": _tc("champion", "pass"),
            },
            "candidate_model_v3": {
                "economic_buyer": _tc("economic_buyer", "pass"),  # absolute level still OK
                "metrics": _tc("metrics", "pass"),
                "champion": _tc("champion", "pass"),
            },
        }
        # EB regressed beyond tolerance.
        regressions = {
            "candidate_model_v3": {
                "economic_buyer": _rc("economic_buyer", "regression", delta=-0.03),
                "metrics": _rc("metrics", "no_regression"),
                "champion": _rc("champion", "no_regression"),
            },
        }
        v = decide(threshold_results, regressions, thresholds=THRESHOLDS)
        assert v.verdict == "hold"
        assert "economic_buyer" in v.failing_fields

    def test_non_load_bearing_regression_is_ship_segment(self):
        # Metrics regresses beyond its 0.03 tolerance, but it's weight 1.0 (not load-bearing).
        threshold_results = {
            "current_model_v2": {f: _tc(f, "pass") for f in THRESHOLDS["fields"]},
            "candidate_model_v3": {f: _tc(f, "pass") for f in THRESHOLDS["fields"]},
        }
        regressions = {
            "candidate_model_v3": {
                "economic_buyer": _rc("economic_buyer", "no_regression"),
                "metrics": _rc("metrics", "regression", delta=-0.05),
                "champion": _rc("champion", "no_regression"),
            },
        }
        v = decide(threshold_results, regressions, thresholds=THRESHOLDS)
        assert v.verdict == "ship_segment"

    # -----------------------------------------------------------------------
    # The 3%/5% scenario — the spec's headline test
    # -----------------------------------------------------------------------

    def test_three_five_scenario_yields_hold(self):
        """Candidate is 3% worse on EB (-0.03) and 5% better on Metrics (+0.05).
        Even though aggregate accuracy improves, EB is load-bearing with zero
        tolerance — the verdict must be `hold`."""
        threshold_results = {
            "current_model_v2": {
                "economic_buyer": _tc("economic_buyer", "fail", ["precision_high"]),  # 0.94 < 0.98
                "metrics": _tc("metrics", "fail", ["precision_high"]),  # 0.85 < 0.90
                "champion": _tc("champion", "pass"),
            },
            "candidate_model_v3": {
                "economic_buyer": _tc("economic_buyer", "fail", ["precision_high"]),  # 0.91 < 0.98
                "metrics": _tc("metrics", "pass"),  # 0.90 == 0.90
                "champion": _tc("champion", "pass"),
            },
        }
        regressions = {
            "candidate_model_v3": {
                "economic_buyer": _rc("economic_buyer", "regression", delta=-0.03),  # the regression
                "metrics": _rc("metrics", "improvement", delta=+0.05),                 # the improvement
                "champion": _rc("champion", "no_regression"),
            },
        }
        v = decide(threshold_results, regressions, thresholds=THRESHOLDS)

        # The whole point of the eval: 3% load-bearing regression > 5% non-load-bearing improvement.
        assert v.verdict == "hold"
        assert "economic_buyer" in v.failing_fields
        assert "metrics" not in v.failing_fields  # metrics did NOT cause the hold
        # Reason should cite the load-bearing field, not aggregate accuracy.
        assert "economic_buyer" in v.reason or "load-bearing" in v.reason.lower()