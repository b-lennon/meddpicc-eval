"""End-to-end orchestrator that runs all six stages in sequence.

The orchestrator is the deterministic fast path: it skips the LLM-judge
step entirely when no row was flagged `needs_semantic_review`. With
fixtures that use exact-match-only extractions (e.g. the three_five_scenario),
the whole pipeline runs in milliseconds with no API calls.

When `needs_semantic_review` rows are present, the caller must supply a
judgments.jsonl from Claude (or any LLM judge); the orchestrator will
otherwise raise from confirm_grades.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Make `scripts.*` importable when invoked as `python scripts/run_eval.py`.
_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

import yaml

from scripts.aggregate import aggregate_all
from scripts.apply_thresholds import apply_thresholds, compare_systems
from scripts.confirm_grades import confirm
from scripts.emit import emit_all
from scripts.grade import MatchKind, grade_all
from scripts.validate_inputs import validate
from scripts.verdict import decide


def run_full_pipeline(
    golden_set: Path,
    extractions_dir: Path,
    thresholds: Path,
    output_dir: Path,
    judgments: Path | None = None,
    run_date: str | None = None,
) -> dict[str, Any]:
    """Run all six stages. Returns a dict with the verdict + output paths."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1: validate inputs.
    v_result = validate(golden_set, extractions_dir, thresholds)
    if not v_result.ok:
        raise ValueError(f"Input validation failed: {v_result.errors}")

    thresholds_dict = yaml.safe_load(thresholds.read_text())
    declared_fields = list(thresholds_dict["fields"].keys())

    # Stage 2a: deterministic grading.
    grades = grade_all(golden_set, extractions_dir)
    tentative_path = output_dir / "tentative-grades.jsonl"
    with tentative_path.open("w") as f:
        for g in grades:
            f.write(json.dumps(g.to_dict()) + "\n")

    # Stage 2b: confirm semantic-match judgments (if any rows need it).
    needs_review = [g for g in grades if g.match == MatchKind.NEEDS_SEMANTIC_REVIEW]
    confirmed_path = output_dir / "confirmed-grades.jsonl"
    if needs_review:
        if judgments is None or not Path(judgments).exists():
            raise ValueError(
                f"{len(needs_review)} rows need LLM-judge resolution but no "
                f"judgments file was provided. Either pass --judgments PATH or "
                f"use a fixture with exact-match-only extractions."
            )
        confirm(tentative_path, Path(judgments), confirmed_path)
    else:
        # Fast path: no semantic review needed, just rename the file.
        confirmed_path.write_text(tentative_path.read_text())

    confirmed_grades = [
        json.loads(line)
        for line in confirmed_path.read_text().splitlines()
        if line.strip()
    ]

    # Stage 3: aggregate.
    metrics = aggregate_all(confirmed_grades, declared_fields)
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    # Stage 4: apply thresholds.
    per_system_field_metrics = {
        s: metrics["per_system"][s]["by_field"] for s in metrics["systems"]
    }
    threshold_results = {
        s: {f: t.to_dict() for f, t in apply_thresholds(per_system_field_metrics[s], thresholds_dict).items()}
        for s in metrics["systems"]
    }
    regressions: dict[str, dict[str, dict[str, Any]]] = {}
    if len(metrics["systems"]) == 2:
        current, candidate = metrics["systems"]
        regressions[candidate] = {
            f: r.to_dict()
            for f, r in compare_systems(
                per_system_field_metrics[current],
                per_system_field_metrics[candidate],
                thresholds_dict,
            ).items()
        }

    threshold_doc = {"threshold_results": threshold_results, "regressions": regressions}
    (output_dir / "threshold-results.json").write_text(json.dumps(threshold_doc, indent=2))

    # Stage 5: verdict.
    verdict = decide(threshold_results, regressions, thresholds_dict)
    (output_dir / "verdict-raw.json").write_text(json.dumps(verdict.to_dict(), indent=2))

    # Stage 6: emit.
    emit_all(
        output_dir=output_dir,
        grades=confirmed_grades,
        metrics=metrics,
        threshold_results=threshold_doc,
        verdict_raw=verdict.to_dict(),
        thresholds=thresholds_dict,
        run_date=run_date or str(date.today()),
        golden_set_size=len({(g["transcript_id"], g["field"]) for g in confirmed_grades}),
    )

    return {
        "verdict": verdict.verdict,
        "output_dir": str(output_dir),
        "scorecard": str(output_dir / "scorecard.md"),
        "verdict_json": str(output_dir / "verdict.json"),
        "audit_log": str(output_dir / "audit-log.jsonl"),
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run the full MEDDPICC eval pipeline.")
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--extractions", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=False, default=None)
    parser.add_argument("--run-date", type=str, required=False, default=None)
    args = parser.parse_args()

    try:
        out = run_full_pipeline(
            golden_set=args.golden,
            extractions_dir=args.extractions,
            thresholds=args.thresholds,
            output_dir=args.output_dir,
            judgments=args.judgments,
            run_date=args.run_date,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Verdict: {out['verdict'].upper()}")
    print(f"Scorecard: {out['scorecard']}")
    print(f"Verdict JSON: {out['verdict_json']}")
    print(f"Audit log: {out['audit_log']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
