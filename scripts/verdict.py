"""Stage 5: apply migration rules to produce the verdict.

The verdict is one of three values:
  - ship: all fields pass thresholds AND no field regresses beyond tolerance.
  - hold: any load-bearing field (weight >= 2.0) regresses beyond tolerance,
          OR (single-system mode) any load-bearing field fails its threshold.
  - ship_segment: not ship, not hold. Typically a non-load-bearing regression
                  or a non-load-bearing threshold fail.

Two-system mode requires the regressions dict; single-system mode passes
an empty regressions dict and the verdict is decided on thresholds only.

The reason string cites the specific field(s) that drove the verdict so a
reader can understand why without re-running the analysis.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.apply_thresholds import FieldStatus, RegressionStatus


@dataclass
class Verdict:
    verdict: str  # "ship" | "hold" | "ship_segment"
    reason: str
    failing_fields: list[str] = field(default_factory=list)
    passing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LOAD_BEARING_THRESHOLD = 2.0


def decide(
    threshold_results: dict[str, dict[str, dict[str, Any]]],
    regressions: dict[str, dict[str, dict[str, Any]]],
    thresholds: dict[str, Any],
) -> Verdict:
    """Apply the migration rules.

    threshold_results: {system_name: {field: ThresholdCheck-as-dict}}
    regressions: {candidate_system: {field: RegressionCheck-as-dict}} or {}.
    """
    field_weights = {
        f: cfg["weight"] for f, cfg in thresholds["fields"].items()
    }
    load_bearing = {f for f, w in field_weights.items() if w >= LOAD_BEARING_THRESHOLD}

    # Single-system mode: decide on thresholds alone.
    if not regressions:
        # Use the first (and presumably only) system.
        system_name = next(iter(threshold_results))
        return _decide_single_system(
            threshold_results[system_name], load_bearing
        )

    # Two-system mode: candidate is the system we have regressions for.
    candidate = next(iter(regressions))
    return _decide_two_systems(
        threshold_results[candidate], regressions[candidate], load_bearing
    )


def _decide_single_system(
    threshold_results: dict[str, dict[str, Any]],
    load_bearing: set[str],
) -> Verdict:
    failing = [f for f, t in threshold_results.items() if t["overall"] == FieldStatus.FAIL.value]
    passing = [f for f, t in threshold_results.items() if t["overall"] == FieldStatus.PASS.value]

    failing_load_bearing = [f for f in failing if f in load_bearing]

    if not failing:
        return Verdict(
            verdict="ship",
            reason="All declared fields pass their thresholds.",
            failing_fields=[],
            passing_fields=passing,
        )

    if failing_load_bearing:
        return Verdict(
            verdict="hold",
            reason=(
                f"Load-bearing field(s) {failing_load_bearing} fail their thresholds. "
                f"Hold until addressed."
            ),
            failing_fields=failing,
            passing_fields=passing,
        )

    return Verdict(
        verdict="ship_segment",
        reason=(
            f"Non-load-bearing field(s) {failing} fail thresholds. "
            f"Consider segment-conditional ship; review the scorecard segment "
            f"breakdown before proceeding."
        ),
        failing_fields=failing,
        passing_fields=passing,
    )


def _decide_two_systems(
    threshold_results: dict[str, dict[str, Any]],
    regressions: dict[str, dict[str, Any]],
    load_bearing: set[str],
) -> Verdict:
    regressed = [
        f for f, r in regressions.items() if r["status"] == RegressionStatus.REGRESSION.value
    ]
    regressed_load_bearing = [f for f in regressed if f in load_bearing]

    threshold_failing = [
        f for f, t in threshold_results.items() if t["overall"] == FieldStatus.FAIL.value
    ]
    threshold_failing_load_bearing = [f for f in threshold_failing if f in load_bearing]

    # Hold takes precedence: any load-bearing regression is fatal.
    if regressed_load_bearing:
        return Verdict(
            verdict="hold",
            reason=(
                f"Load-bearing field(s) {regressed_load_bearing} regress beyond tolerance "
                f"in the candidate system. Do not migrate."
            ),
            failing_fields=regressed_load_bearing,
            passing_fields=[
                f for f in threshold_results
                if f not in regressed and f not in threshold_failing
            ],
        )

    # If a load-bearing field absolutely fails (and no regression caught it above),
    # still hold — the candidate is below the floor on a critical field.
    if threshold_failing_load_bearing:
        return Verdict(
            verdict="hold",
            reason=(
                f"Load-bearing field(s) {threshold_failing_load_bearing} are below "
                f"threshold floor in the candidate system. Do not migrate."
            ),
            failing_fields=threshold_failing_load_bearing,
            passing_fields=[
                f for f in threshold_results
                if f not in threshold_failing
            ],
        )

    # No load-bearing failures. Ship iff no regressions and no threshold fails.
    if not regressed and not threshold_failing:
        return Verdict(
            verdict="ship",
            reason="All fields pass thresholds and no field regresses beyond tolerance.",
            failing_fields=[],
            passing_fields=list(threshold_results.keys()),
        )

    # Otherwise it's a non-load-bearing miss → ship_segment.
    flagged = sorted(set(regressed) | set(threshold_failing))
    return Verdict(
        verdict="ship_segment",
        reason=(
            f"Non-load-bearing field(s) {flagged} have regressions or threshold "
            f"failures. Ship to segments where the candidate still passes; review "
            f"the segment breakdown before proceeding."
        ),
        failing_fields=flagged,
        passing_fields=[f for f in threshold_results if f not in flagged],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> int:
    parser = argparse.ArgumentParser(description="Apply migration verdict rules.")
    parser.add_argument("--threshold-results", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    threshold_doc = json.loads(args.threshold_results.read_text())
    thresholds = yaml.safe_load(args.thresholds.read_text())

    v = decide(
        threshold_results=threshold_doc["threshold_results"],
        regressions=threshold_doc.get("regressions", {}),
        thresholds=thresholds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(v.to_dict(), indent=2))
    print(f"Verdict: {v.verdict}. Reason: {v.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
