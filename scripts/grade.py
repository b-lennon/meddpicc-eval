"""Stage 2 (deterministic portion): per-row grading.

For each (transcript_id, field, system) triple, produces a GradeResult
that classifies the match kind, the confidence calibration, the
abstention status, and the evidence-quote faithfulness. Rows where
exact-string comparison is inconclusive are flagged for downstream
LLM-judge review.

This module is pure and deterministic. The LLM-judgment step lives in
scripts/confirm_grades.py; this module never calls a model.
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


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MatchKind(str, Enum):
    """Classification of the gold-vs-system value comparison."""
    MATCH_EXACT = "match_exact"
    MATCH_NULL = "match_null"
    MATCH_SEMANTIC = "match_semantic"          # set downstream after LLM judge
    FALSE_POSITIVE = "false_positive"          # gold=null, sys=non-null
    FALSE_NEGATIVE = "false_negative"          # gold=non-null, sys=null
    MISMATCH = "mismatch"                      # set downstream when LLM says no
    NEEDS_SEMANTIC_REVIEW = "needs_semantic_review"


class CalibrationKind(str, Enum):
    """Confidence calibration classification.

    Defined in detail in Task 6. Placeholder default is PENDING so that
    Task 5's tests can still construct GradeResults without lying about
    calibration.
    """
    PENDING = "pending"
    WELL_CALIBRATED_HIGH = "well_calibrated_high"
    OVERCONFIDENT = "overconfident"
    WELL_CALIBRATED_MEDIUM = "well_calibrated_medium"
    WELL_CALIBRATED_LOW = "well_calibrated_low"
    APPROPRIATE_ABSTENTION = "appropriate_abstention"
    MISSED_EXTRACTION = "missed_extraction"
    NOT_APPLICABLE = "not_applicable"          # gold_confidence == 'none'


class EvidenceFaithfulness(str, Enum):
    """Whether system evidence quote actually supports the system value."""
    PENDING = "pending"
    FAITHFUL = "faithful"
    UNFAITHFUL = "unfaithful"
    UNVERIFIABLE = "unverifiable"              # one or both quotes null


# ---------------------------------------------------------------------------
# Grade result
# ---------------------------------------------------------------------------

@dataclass
class GradeResult:
    transcript_id: str
    field: str
    system: str
    match: MatchKind
    gold_value: str | None
    sys_value: str | None
    gold_confidence: str
    sys_confidence: str
    gold_evidence_quote: str | None
    sys_evidence_quote: str | None
    edge_case_tag: str | None
    segment: dict[str, str]
    calibration_kind: CalibrationKind = CalibrationKind.PENDING
    evidence_faithfulness: EvidenceFaithfulness = EvidenceFaithfulness.PENDING
    contract_warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Enums serialize as their string values for stable JSONL output.
        d["match"] = self.match.value
        d["calibration_kind"] = self.calibration_kind.value
        d["evidence_faithfulness"] = self.evidence_faithfulness.value
        return d


# ---------------------------------------------------------------------------
# Grading: match kind (Task 5)
# ---------------------------------------------------------------------------

_CORRECT_MATCHES = {MatchKind.MATCH_EXACT, MatchKind.MATCH_NULL, MatchKind.MATCH_SEMANTIC}


def grade_row(
    transcript_id: str,
    field: str,
    system: str,
    gold_value: str | None,
    sys_value: str | None,
    gold_confidence: str,
    sys_confidence: str,
    gold_evidence_quote: str | None,
    sys_evidence_quote: str | None,
    edge_case_tag: str | None,
    segment: dict[str, str],
) -> GradeResult:
    """Classify one (gold, system) pair for a single field."""
    match = _classify_match(gold_value, sys_value)
    calibration = _classify_calibration(match, gold_value, gold_confidence, sys_confidence)
    warning = _check_contract(sys_value, sys_confidence)
    faithfulness = _classify_faithfulness(gold_evidence_quote, sys_evidence_quote)
    return GradeResult(
        transcript_id=transcript_id,
        field=field,
        system=system,
        match=match,
        gold_value=gold_value,
        sys_value=sys_value,
        gold_confidence=gold_confidence,
        sys_confidence=sys_confidence,
        gold_evidence_quote=gold_evidence_quote,
        sys_evidence_quote=sys_evidence_quote,
        edge_case_tag=edge_case_tag,
        segment=segment,
        calibration_kind=calibration,
        contract_warning=warning,
        evidence_faithfulness=faithfulness,
    )


def _check_contract(sys_value: str | None, sys_confidence: str) -> str | None:
    """Surface inner contract violations as warnings (not errors).

    The contract: value=null requires confidence=none, and confidence=none
    requires value=null. Schema validation catches this at the input stage
    with a warning; we surface it on the grade row too so the audit log
    flags the specific (transcript, field, system) where it occurred.
    """
    if sys_value is None and sys_confidence != "none":
        return (
            f"Contract violation: value is null but confidence is "
            f"'{sys_confidence}' (expected 'none')."
        )
    if sys_value is not None and sys_confidence == "none":
        return (
            f"Contract violation: confidence is 'none' but value is "
            f"non-null ('{sys_value}')."
        )
    return None


# ---------------------------------------------------------------------------
# Evidence faithfulness (Task 8)
# ---------------------------------------------------------------------------

_FAITHFULNESS_THRESHOLD = 0.7


def _classify_faithfulness(
    gold_quote: str | None, sys_quote: str | None
) -> EvidenceFaithfulness:
    """Compare the system's evidence quote against the gold's.

    We don't have the source transcript, so we can't check the system's
    quote against the call directly. We can check whether it overlaps
    with the gold's quote — the gold is itself a verbatim span from the
    transcript, so substantial overlap is a proxy for faithful citation.

    Faithfulness score = max(|S∩G|/|S|, |S∩G|/|G|). This rewards either
    direction of substring relationship (system quoted a snippet of the
    gold span, OR system quoted a longer span containing the gold).
    """
    if gold_quote is None or sys_quote is None:
        return EvidenceFaithfulness.UNVERIFIABLE

    gold_tokens = _normalize_tokens(gold_quote)
    sys_tokens = _normalize_tokens(sys_quote)
    if not gold_tokens or not sys_tokens:
        return EvidenceFaithfulness.UNVERIFIABLE

    intersection = gold_tokens & sys_tokens
    coverage_gold = len(intersection) / len(gold_tokens)
    coverage_sys = len(intersection) / len(sys_tokens)
    score = max(coverage_gold, coverage_sys)

    return (
        EvidenceFaithfulness.FAITHFUL
        if score >= _FAITHFULNESS_THRESHOLD
        else EvidenceFaithfulness.UNFAITHFUL
    )


def _normalize_tokens(text: str) -> set[str]:
    """Lowercase, strip non-alphanumeric, return token set."""
    cleaned = "".join(
        c.lower() if c.isalnum() else " " for c in text
    )
    return {tok for tok in cleaned.split() if tok}


def _classify_match(gold_value: str | None, sys_value: str | None) -> MatchKind:
    if gold_value is None and sys_value is None:
        return MatchKind.MATCH_NULL
    if gold_value is None and sys_value is not None:
        return MatchKind.FALSE_POSITIVE
    if gold_value is not None and sys_value is None:
        return MatchKind.FALSE_NEGATIVE
    if gold_value == sys_value:
        return MatchKind.MATCH_EXACT
    return MatchKind.NEEDS_SEMANTIC_REVIEW


def _classify_calibration(
    match: MatchKind,
    gold_value: str | None,
    gold_confidence: str,
    sys_confidence: str,
) -> CalibrationKind:
    # Labeler uncertain → row contributes neither to overconfidence nor to
    # well-calibrated counts; abstention semantics still apply.
    if gold_confidence == "none":
        return CalibrationKind.NOT_APPLICABLE

    if sys_confidence == "none":
        if gold_value is None:
            return CalibrationKind.APPROPRIATE_ABSTENTION
        return CalibrationKind.MISSED_EXTRACTION

    # Semantic-review pending → defer calibration classification until the
    # LLM judge resolves the match in confirm_grades.py.
    if match == MatchKind.NEEDS_SEMANTIC_REVIEW:
        return CalibrationKind.PENDING

    if sys_confidence == "high":
        return (
            CalibrationKind.WELL_CALIBRATED_HIGH
            if match in _CORRECT_MATCHES
            else CalibrationKind.OVERCONFIDENT
        )
    if sys_confidence == "medium":
        return CalibrationKind.WELL_CALIBRATED_MEDIUM
    if sys_confidence == "low":
        return CalibrationKind.WELL_CALIBRATED_LOW

    # Shouldn't reach here given the enum constraint on sys_confidence.
    return CalibrationKind.PENDING


# ---------------------------------------------------------------------------
# CLI entry point — grades all (golden, extraction) pairs into a JSONL.
# ---------------------------------------------------------------------------

def grade_all(
    golden_path: Path,
    extractions_dir: Path,
) -> list[GradeResult]:
    """Grade every (transcript_id, field, system) triple found in the inputs."""
    golden_rows = _load_golden_set(golden_path)
    grades: list[GradeResult] = []

    for system_dir in sorted(p for p in extractions_dir.iterdir() if p.is_dir()):
        system = system_dir.name
        for extraction_file in sorted(system_dir.glob("*.json")):
            transcript_id = extraction_file.stem
            extraction = json.loads(extraction_file.read_text())
            for row in golden_rows:
                if row["transcript_id"] != transcript_id:
                    continue
                fname = row["field"]
                if fname not in extraction:
                    # Caught by validate_inputs.py — but be defensive.
                    continue
                fextract = extraction[fname]
                grades.append(
                    grade_row(
                        transcript_id=transcript_id,
                        field=fname,
                        system=system,
                        gold_value=row["gold_value"],
                        sys_value=fextract["value"],
                        gold_confidence=row["gold_confidence"],
                        sys_confidence=fextract["confidence"],
                        gold_evidence_quote=row["gold_evidence_quote"],
                        sys_evidence_quote=fextract["evidence_quote"],
                        edge_case_tag=row["edge_case_tag"],
                        segment=row["segment"],
                    )
                )
    return grades


def _load_golden_set(golden_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in golden_path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _main() -> int:
    parser = argparse.ArgumentParser(description="Grade MEDDPICC extractions row-by-row.")
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--extractions", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=False,
                        help="thresholds.yaml — currently unused at this stage, accepted for pipeline uniformity.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    grades = grade_all(args.golden, args.extractions)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        for g in grades:
            f.write(json.dumps(g.to_dict()) + "\n")

    needs_review = sum(1 for g in grades if g.match == MatchKind.NEEDS_SEMANTIC_REVIEW)
    print(
        f"Graded {len(grades)} rows. "
        f"needs_semantic_review={needs_review}. "
        f"Output: {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
