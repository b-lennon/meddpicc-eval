"""Stage 3: roll per-row grades up to per-system, per-field aggregates.

Aggregation is pure arithmetic over confirmed-grades.jsonl. It produces:
  - Per-field precision / recall / F1 / abstention rate / precision@high
  - Per-(field, segment_dimension, segment_value) breakdowns
  - Per-(field, edge_case_tag) breakdowns

Downstream threshold and verdict steps depend on the numbers here being
correct — that's why this layer is deterministic and pytest-covered.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

CORRECT_MATCHES = {"match_exact", "match_semantic"}
NULL_MATCH = "match_null"
WRONG_NONNULL_PREDICTIONS = {"false_positive", "mismatch"}
MISSED_GOLDS = {"false_negative", "mismatch"}


@dataclass
class FieldMetrics:
    field: str
    n_total: int
    precision: float
    recall: float
    f1: float
    abstention_rate: float
    precision_high: float
    n_high_confidence: int
    accuracy: float = 0.0  # (TP + TN) / n_total; useful for null-gold subsets where precision is degenerate

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Per-field aggregation (Task 10)
# ---------------------------------------------------------------------------

def aggregate_field(field_name: str, grades: list[dict[str, Any]]) -> FieldMetrics:
    relevant = [g for g in grades if g["field"] == field_name]
    n_total = len(relevant)

    # Counts for precision / recall / F1.
    tp = sum(1 for g in relevant if g["match"] in CORRECT_MATCHES)
    fp_nonnull = sum(1 for g in relevant if g["match"] in WRONG_NONNULL_PREDICTIONS)
    fn_missed = sum(1 for g in relevant if g["match"] in MISSED_GOLDS)
    abstentions = sum(1 for g in relevant if g["sys_value"] is None)

    precision = _safe_div(tp, tp + fp_nonnull)
    recall = _safe_div(tp, tp + fn_missed)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    abstention_rate = _safe_div(abstentions, n_total)

    # Calibration: restrict to (sys_confidence=high, gold_confidence != none).
    high_subset = [
        g for g in relevant
        if g["sys_confidence"] == "high" and g["gold_confidence"] != "none"
    ]
    n_high = len(high_subset)
    tp_high = sum(1 for g in high_subset if g["match"] in CORRECT_MATCHES)
    fp_high = sum(1 for g in high_subset if g["match"] in WRONG_NONNULL_PREDICTIONS)
    precision_high = _safe_div(tp_high, tp_high + fp_high)

    correct = sum(1 for g in relevant if g["match"] in CORRECT_MATCHES or g["match"] == NULL_MATCH)
    accuracy = _safe_div(correct, n_total)

    return FieldMetrics(
        field=field_name,
        n_total=n_total,
        precision=precision,
        recall=recall,
        f1=f1,
        abstention_rate=abstention_rate,
        precision_high=precision_high,
        n_high_confidence=n_high,
        accuracy=accuracy,
    )


# ---------------------------------------------------------------------------
# Segment breakdown (Task 11)
# ---------------------------------------------------------------------------

def aggregate_field_by_segment(
    field_name: str, grades: list[dict[str, Any]]
) -> dict[str, dict[str, FieldMetrics]]:
    """Return {dimension: {segment_value: FieldMetrics}} for the field.

    Empty segments are omitted from the output.
    """
    relevant = [g for g in grades if g["field"] == field_name]
    out: dict[str, dict[str, FieldMetrics]] = {
        "deal_size_band": {},
        "stage": {},
        "call_type": {},
    }
    for dim in out:
        groups: dict[str, list[dict]] = defaultdict(list)
        for g in relevant:
            groups[g["segment"][dim]].append(g)
        for value, rows in groups.items():
            out[dim][value] = aggregate_field(field_name, rows)
    return out


# ---------------------------------------------------------------------------
# Edge-case breakdown (Task 12)
# ---------------------------------------------------------------------------

def aggregate_field_by_edge_case(
    field_name: str, grades: list[dict[str, Any]]
) -> dict[str, FieldMetrics]:
    """Return {edge_case_tag: FieldMetrics}. Rows without a tag are excluded."""
    relevant = [g for g in grades if g["field"] == field_name and g.get("edge_case_tag")]
    groups: dict[str, list[dict]] = defaultdict(list)
    for g in relevant:
        groups[g["edge_case_tag"]].append(g)
    return {tag: aggregate_field(field_name, rows) for tag, rows in groups.items()}


# ---------------------------------------------------------------------------
# Top-level rollup: every system × every declared field
# ---------------------------------------------------------------------------

def _sort_systems(systems: list[str]) -> list[str]:
    """Order two-system pairs as (current, candidate) when names cue it.

    Heuristic: any name containing 'current' sorts before any name containing
    'candidate'. Same for legacy/baseline vs proposed/new/migration. Otherwise
    alphabetical. The ordering matters because the verdict layer treats the
    first system as the baseline and the second as the migration candidate.
    """
    def key(name: str) -> tuple[int, str]:
        n = name.lower()
        if "current" in n or "baseline" in n or "legacy" in n:
            return (0, name)
        if "candidate" in n or "new" in n or "proposed" in n or "migration" in n:
            return (2, name)
        return (1, name)
    return sorted(systems, key=key)


def aggregate_all(
    grades: list[dict[str, Any]],
    declared_fields: list[str],
) -> dict[str, Any]:
    """Return a JSON-serializable dict with per-system aggregates."""
    systems = _sort_systems(list({g["system"] for g in grades}))
    out: dict[str, Any] = {
        "systems": systems,
        "fields": declared_fields,
        "per_system": {},
    }
    for sys in systems:
        sys_grades = [g for g in grades if g["system"] == sys]
        out["per_system"][sys] = {
            "by_field": {
                f: aggregate_field(f, sys_grades).to_dict()
                for f in declared_fields
            },
            "by_field_by_segment": {
                f: {
                    dim: {val: m.to_dict() for val, m in vals.items()}
                    for dim, vals in aggregate_field_by_segment(f, sys_grades).items()
                }
                for f in declared_fields
            },
            "by_field_by_edge_case": {
                f: {tag: m.to_dict() for tag, m in aggregate_field_by_edge_case(f, sys_grades).items()}
                for f in declared_fields
            },
        }
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_div(num: float, denom: float) -> float:
    return num / denom if denom else 0.0


def _main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate grades into per-system metrics.")
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    import yaml
    thresholds = yaml.safe_load(args.thresholds.read_text())
    declared_fields = list(thresholds["fields"].keys())

    grades = [json.loads(l) for l in args.grades.read_text().splitlines() if l.strip()]
    metrics = aggregate_all(grades, declared_fields)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, indent=2))
    print(f"Aggregated {len(grades)} grades across {len(metrics['systems'])} system(s); output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
