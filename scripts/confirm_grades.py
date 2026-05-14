"""Stage 2b: merge semantic-match judgments into the grade stream.

Reads tentative-grades.jsonl (from grade.py) and judgments.jsonl (from
the LLM-judge step in stage 01) and produces confirmed-grades.jsonl
where every `needs_semantic_review` row has been resolved to either
`match_semantic` or `mismatch`. Calibration is reclassified to reflect
the resolved match.

Fails loudly if any `needs_semantic_review` row lacks a corresponding
judgment, or if a judgment value is not one of the allowed values.
Silent acceptance is how an eval pipeline produces confidently wrong
aggregates.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scripts.grade import CalibrationKind, MatchKind


VALID_JUDGMENTS = {"match", "mismatch"}


def confirm(
    tentative_path: Path,
    judgments_path: Path,
    output_path: Path,
) -> None:
    tentative = _read_jsonl(tentative_path)
    judgments = _read_jsonl(judgments_path) if judgments_path.exists() else []

    # Index judgments by (transcript_id, field, system).
    by_key: dict[tuple[str, str, str], str] = {}
    for j in judgments:
        decision = j.get("semantic_match")
        if decision not in VALID_JUDGMENTS:
            raise ValueError(
                f"invalid semantic_match value '{decision}' for "
                f"({j.get('transcript_id')}, {j.get('field')}, {j.get('system')}); "
                f"must be one of {sorted(VALID_JUDGMENTS)}"
            )
        key = (j["transcript_id"], j["field"], j["system"])
        by_key[key] = decision

    confirmed: list[dict[str, Any]] = []
    for row in tentative:
        if row["match"] != MatchKind.NEEDS_SEMANTIC_REVIEW.value:
            confirmed.append(row)
            continue

        key = (row["transcript_id"], row["field"], row["system"])
        decision = by_key.get(key)
        if decision is None:
            raise ValueError(
                f"missing judgment for {key}. Every row tagged "
                f"needs_semantic_review must have a corresponding entry in "
                f"judgments.jsonl."
            )

        if decision == "match":
            row["match"] = MatchKind.MATCH_SEMANTIC.value
        else:
            row["match"] = MatchKind.MISMATCH.value

        row["calibration_kind"] = _recompute_calibration(row)
        confirmed.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for row in confirmed:
            f.write(json.dumps(row) + "\n")


def _recompute_calibration(row: dict[str, Any]) -> str:
    """Reclassify calibration after the semantic-match decision is in.

    Mirrors scripts.grade._classify_calibration but for the post-judgment state.
    """
    sys_conf = row["sys_confidence"]
    gold_conf = row["gold_confidence"]
    match = row["match"]

    if gold_conf == "none":
        return CalibrationKind.NOT_APPLICABLE.value
    if sys_conf == "none":
        if row["gold_value"] is None:
            return CalibrationKind.APPROPRIATE_ABSTENTION.value
        return CalibrationKind.MISSED_EXTRACTION.value

    correct = match in (
        MatchKind.MATCH_EXACT.value,
        MatchKind.MATCH_NULL.value,
        MatchKind.MATCH_SEMANTIC.value,
    )
    if sys_conf == "high":
        return (
            CalibrationKind.WELL_CALIBRATED_HIGH.value
            if correct
            else CalibrationKind.OVERCONFIDENT.value
        )
    if sys_conf == "medium":
        return CalibrationKind.WELL_CALIBRATED_MEDIUM.value
    if sys_conf == "low":
        return CalibrationKind.WELL_CALIBRATED_LOW.value
    return CalibrationKind.PENDING.value


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge LLM-judge judgments into the grade stream."
    )
    parser.add_argument("--tentative", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=False, default=None,
                        help="Path to judgments.jsonl. If omitted, the script assumes "
                             "no needs_semantic_review rows exist (fast path).")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    judgments_path = args.judgments if args.judgments else args.tentative.parent / "judgments.jsonl"

    try:
        confirm(args.tentative, judgments_path, args.output)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Confirmed grades written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
