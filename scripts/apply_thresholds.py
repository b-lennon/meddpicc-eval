"""Stage 4: apply thresholds and compare paired systems.

Two pure functions:
  - apply_thresholds(metrics, thresholds): per-field PASS/FAIL with the
    list of which specific checks failed (precision_high, recall, abstention_rate).
  - compare_systems(current, candidate, thresholds): per-field
    NO_REGRESSION / IMPROVEMENT / REGRESSION, with the delta in precision_high
    (the metric regression_tolerance applies to).

Neither function knows anything about the migration verdict — that's
Stage 5. They just produce the inputs the verdict step needs.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class FieldStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class RegressionStatus(str, Enum):
    NO_REGRESSION = "no_regression"
    IMPROVEMENT = "improvement"
    REGRESSION = "regression"


@dataclass
class ThresholdCheck:
    field: str
    overall: FieldStatus
    failed_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["overall"] = self.overall.value
        return d


@dataclass
class RegressionCheck:
    field: str
    status: RegressionStatus
    current_precision_high: float
    candidate_precision_high: float
    delta_precision_high: float
    tolerance: float

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


# ---------------------------------------------------------------------------
# Threshold application
# ---------------------------------------------------------------------------

def apply_thresholds(
    metrics: dict[str, dict[str, Any]],
    thresholds: dict[str, Any],
) -> dict[str, ThresholdCheck]:
    """Per-field PASS/FAIL for one system's aggregated metrics."""
    out: dict[str, ThresholdCheck] = {}
    for field_name, field_thresholds in thresholds["fields"].items():
        m = metrics.get(field_name)
        if m is None:
            out[field_name] = ThresholdCheck(
                field=field_name,
                overall=FieldStatus.FAIL,
                failed_checks=["missing_metrics"],
            )
            continue

        failed: list[str] = []
        if m["precision_high"] < field_thresholds["min_precision_high"]:
            failed.append("precision_high")
        if m["recall"] < field_thresholds["min_recall"]:
            failed.append("recall")
        if m["abstention_rate"] > field_thresholds["max_abstention_rate"]:
            failed.append("abstention_rate")

        out[field_name] = ThresholdCheck(
            field=field_name,
            overall=FieldStatus.PASS if not failed else FieldStatus.FAIL,
            failed_checks=failed,
        )
    return out


# ---------------------------------------------------------------------------
# System comparison
# ---------------------------------------------------------------------------

def compare_systems(
    current: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    thresholds: dict[str, Any],
) -> dict[str, RegressionCheck]:
    """Per-field regression status between two systems."""
    out: dict[str, RegressionCheck] = {}
    for field_name, field_thresholds in thresholds["fields"].items():
        cur = current.get(field_name, {}).get("precision_high", 0.0)
        cand = candidate.get(field_name, {}).get("precision_high", 0.0)
        delta = cand - cur
        tolerance = field_thresholds["regression_tolerance"]

        if delta > 0:
            status = RegressionStatus.IMPROVEMENT
        elif delta == 0:
            status = RegressionStatus.NO_REGRESSION
        elif -delta <= tolerance:
            status = RegressionStatus.NO_REGRESSION
        else:
            status = RegressionStatus.REGRESSION

        out[field_name] = RegressionCheck(
            field=field_name,
            status=status,
            current_precision_high=cur,
            candidate_precision_high=cand,
            delta_precision_high=delta,
            tolerance=tolerance,
        )
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply thresholds and compute regressions across systems."
    )
    parser.add_argument("--metrics", type=Path, required=True,
                        help="Path to aggregate output (metrics.json).")
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    metrics_doc = json.loads(args.metrics.read_text())
    thresholds = yaml.safe_load(args.thresholds.read_text())

    per_system_metrics = {
        sys: metrics_doc["per_system"][sys]["by_field"]
        for sys in metrics_doc["systems"]
    }

    threshold_results = {
        sys: {f: t.to_dict() for f, t in apply_thresholds(per_system_metrics[sys], thresholds).items()}
        for sys in metrics_doc["systems"]
    }

    regressions: dict[str, dict[str, Any]] = {}
    if len(metrics_doc["systems"]) == 2:
        current, candidate = metrics_doc["systems"]
        regressions[candidate] = {
            f: r.to_dict()
            for f, r in compare_systems(
                per_system_metrics[current], per_system_metrics[candidate], thresholds
            ).items()
        }

    out = {"threshold_results": threshold_results, "regressions": regressions}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2))
    print(f"Threshold + regression results written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
