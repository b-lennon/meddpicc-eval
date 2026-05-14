"""Stage 6: render the three output artifacts.

Three artifacts, one stage:
  - scorecard.md      Human-readable. Sales-leader-readable. The Friday document.
  - audit-log.jsonl   One row per (transcript, field, system) failure. Diagnostic.
  - verdict.json      Machine-readable. CI-gateable.

The scorecard is the one piece of prose the skill produces. Every other
output is data. Tone of the prose is deliberate: short sentences, no
hedging, no jargon beyond MEDDPICC. A sales VP must be able to read it
without footnotes.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

import yaml

from scripts.apply_thresholds import FieldStatus, RegressionStatus


# Match types treated as "correct" (a true positive from the system's standpoint).
_CORRECT_MATCHES = {"match_exact", "match_semantic", "match_null"}


def emit_all(
    output_dir: Path,
    grades: list[dict[str, Any]],
    metrics: dict[str, Any],
    threshold_results: dict[str, Any],
    verdict_raw: dict[str, Any],
    thresholds: dict[str, Any] | None = None,
    run_date: str | None = None,
    golden_set_size: int | None = None,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    systems = metrics["systems"]
    fields = metrics["fields"]

    # ---- scorecard.md ----
    scorecard = _render_scorecard(
        verdict_raw=verdict_raw,
        metrics=metrics,
        threshold_results=threshold_results,
        thresholds=thresholds or {},
        run_date=run_date or str(date.today()),
        golden_set_size=golden_set_size if golden_set_size is not None else _infer_golden_size(grades),
    )
    (output_dir / "scorecard.md").write_text(scorecard)

    # ---- audit-log.jsonl ----
    audit_rows = [
        _audit_row(g) for g in grades
        if g["match"] not in _CORRECT_MATCHES
    ]
    with (output_dir / "audit-log.jsonl").open("w") as f:
        for row in audit_rows:
            f.write(json.dumps(row) + "\n")

    # ---- verdict.json ----
    verdict_doc = _build_verdict_json(
        verdict_raw=verdict_raw,
        threshold_results=threshold_results,
        systems=systems,
        run_date=run_date or str(date.today()),
        golden_set_size=golden_set_size if golden_set_size is not None else _infer_golden_size(grades),
    )
    (output_dir / "verdict.json").write_text(json.dumps(verdict_doc, indent=2))


# ---------------------------------------------------------------------------
# scorecard.md
# ---------------------------------------------------------------------------

def _render_scorecard(
    verdict_raw: dict[str, Any],
    metrics: dict[str, Any],
    threshold_results: dict[str, Any],
    thresholds: dict[str, Any],
    run_date: str,
    golden_set_size: int,
) -> str:
    systems = metrics["systems"]
    fields = metrics["fields"]
    is_comparison = len(systems) == 2

    lines: list[str] = []
    lines.append("# MEDDPICC Extraction Scorecard")
    lines.append("")
    lines.append(f"**Systems evaluated:** {', '.join(systems)}")
    lines.append(f"**Golden set:** {golden_set_size} rows")
    lines.append(f"**Run date:** {run_date}")
    lines.append("")

    # Verdict block
    verdict_text = verdict_raw["verdict"].upper()
    lines.append(f"## Migration verdict: {verdict_text}")
    lines.append("")
    lines.append(verdict_raw["reason"])
    lines.append("")

    # Per-field table
    lines.append("## Per-field results")
    lines.append("")
    if is_comparison:
        current, candidate = systems
        regressions = threshold_results.get("regressions", {}).get(candidate, {})
        lines.append("| Field | Current | Candidate | Δ | Threshold | Status |")
        lines.append("|---|---|---|---|---|---|")
        for f in fields:
            cur = metrics["per_system"][current]["by_field"][f]["precision_high"]
            cand = metrics["per_system"][candidate]["by_field"][f]["precision_high"]
            delta = cand - cur
            min_ph = (
                thresholds.get("fields", {}).get(f, {}).get("min_precision_high")
            )
            min_ph_str = f"{min_ph:.2f}" if min_ph is not None else "—"
            cand_status = threshold_results["threshold_results"][candidate][f]["overall"].upper()
            reg = regressions.get(f, {})
            reg_marker = ""
            if reg.get("status") == RegressionStatus.REGRESSION.value:
                reg_marker = " ⚠"
            lines.append(
                f"| {f} | {cur:.2f} | {cand:.2f} | {delta:+.2f}{reg_marker} | {min_ph_str} | {cand_status} |"
            )
    else:
        system = systems[0]
        lines.append("| Field | Precision@high | Recall | Abstention | Threshold | Status |")
        lines.append("|---|---|---|---|---|---|")
        for f in fields:
            m = metrics["per_system"][system]["by_field"][f]
            min_ph = thresholds.get("fields", {}).get(f, {}).get("min_precision_high")
            min_ph_str = f"{min_ph:.2f}" if min_ph is not None else "—"
            status = threshold_results["threshold_results"][system][f]["overall"].upper()
            lines.append(
                f"| {f} | {m['precision_high']:.2f} | {m['recall']:.2f} | "
                f"{m['abstention_rate']:.2f} | {min_ph_str} | {status} |"
            )
    lines.append("")

    # Segment breakdown — only for load-bearing fields, to keep the document scannable.
    load_bearing_fields = [
        f for f, cfg in thresholds.get("fields", {}).items()
        if cfg.get("weight", 0) >= 2.0
    ]
    for f in load_bearing_fields:
        if f not in fields:
            continue
        lines.extend(_render_segment_block(f, systems, metrics, is_comparison))

    # Edge-case breakdown — same audience as segment.
    for f in load_bearing_fields:
        if f not in fields:
            continue
        lines.extend(_render_edge_case_block(f, systems, metrics, is_comparison))

    return "\n".join(lines) + "\n"


def _render_segment_block(
    field_name: str,
    systems: list[str],
    metrics: dict[str, Any],
    is_comparison: bool,
) -> list[str]:
    by_seg_per_system: dict[str, dict[str, dict[str, Any]]] = {
        s: metrics["per_system"][s]["by_field_by_segment"].get(field_name, {})
        for s in systems
    }
    # Use deal_size_band as the headline segment dimension; the spec example does the same.
    dim = "deal_size_band"
    all_segments: list[str] = []
    seen: set[str] = set()
    for s in systems:
        for seg_val in by_seg_per_system[s].get(dim, {}):
            if seg_val not in seen:
                all_segments.append(seg_val)
                seen.add(seg_val)
    if not all_segments:
        return []

    out = [f"## Segment breakdown — {field_name} (accuracy by deal size)", ""]
    if is_comparison:
        cur, cand = systems
        out.append("| Segment | Current | Candidate | Δ |")
        out.append("|---|---|---|---|")
        for seg in all_segments:
            cur_a = by_seg_per_system[cur].get(dim, {}).get(seg, {}).get("accuracy", 0.0)
            cand_a = by_seg_per_system[cand].get(dim, {}).get(seg, {}).get("accuracy", 0.0)
            delta = cand_a - cur_a
            out.append(f"| {seg} | {cur_a:.2f} | {cand_a:.2f} | {delta:+.2f} |")
    else:
        s = systems[0]
        out.append("| Segment | Accuracy | Recall |")
        out.append("|---|---|---|")
        for seg in all_segments:
            m = by_seg_per_system[s].get(dim, {}).get(seg, {})
            out.append(f"| {seg} | {m.get('accuracy', 0.0):.2f} | {m.get('recall', 0.0):.2f} |")
    out.append("")
    return out


def _render_edge_case_block(
    field_name: str,
    systems: list[str],
    metrics: dict[str, Any],
    is_comparison: bool,
) -> list[str]:
    by_tag_per_system: dict[str, dict[str, dict[str, Any]]] = {
        s: metrics["per_system"][s]["by_field_by_edge_case"].get(field_name, {})
        for s in systems
    }
    all_tags: list[str] = []
    seen: set[str] = set()
    for s in systems:
        for tag in by_tag_per_system[s]:
            if tag not in seen:
                all_tags.append(tag)
                seen.add(tag)
    if not all_tags:
        return []

    out = [f"## Edge-case breakdown — {field_name} (accuracy)", ""]
    if is_comparison:
        cur, cand = systems
        out.append("| Edge case | Current | Candidate | Δ |")
        out.append("|---|---|---|---|")
        # Sort by candidate delta ascending so the biggest regressions appear first.
        sortable = [
            (
                tag,
                by_tag_per_system[cur].get(tag, {}).get("accuracy", 0.0),
                by_tag_per_system[cand].get(tag, {}).get("accuracy", 0.0),
            )
            for tag in all_tags
        ]
        sortable.sort(key=lambda r: r[2] - r[1])
        for tag, cur_a, cand_a in sortable:
            delta = cand_a - cur_a
            out.append(f"| {tag} | {cur_a:.2f} | {cand_a:.2f} | {delta:+.2f} |")
    else:
        s = systems[0]
        out.append("| Edge case | Accuracy | n |")
        out.append("|---|---|---|")
        for tag in all_tags:
            m = by_tag_per_system[s].get(tag, {})
            out.append(f"| {tag} | {m.get('accuracy', 0.0):.2f} | {m.get('n_total', 0)} |")
    out.append("")
    return out


# ---------------------------------------------------------------------------
# audit-log.jsonl
# ---------------------------------------------------------------------------

def _audit_row(grade: dict[str, Any]) -> dict[str, Any]:
    """Project a grade row down to the diagnostic columns the audit log carries."""
    return {
        "transcript_id": grade["transcript_id"],
        "field": grade["field"],
        "system": grade["system"],
        "match": grade["match"],
        "gold_value": grade["gold_value"],
        "sys_value": grade["sys_value"],
        "sys_confidence": grade["sys_confidence"],
        "edge_case_tag": grade.get("edge_case_tag"),
        "segment": grade["segment"],
        "calibration_kind": grade.get("calibration_kind"),
        "evidence_faithfulness": grade.get("evidence_faithfulness"),
        "contract_warning": grade.get("contract_warning"),
    }


# ---------------------------------------------------------------------------
# verdict.json
# ---------------------------------------------------------------------------

def _build_verdict_json(
    verdict_raw: dict[str, Any],
    threshold_results: dict[str, Any],
    systems: list[str],
    run_date: str,
    golden_set_size: int,
) -> dict[str, Any]:
    return {
        "verdict": verdict_raw["verdict"],
        "reason": verdict_raw["reason"],
        "failing_fields": verdict_raw.get("failing_fields", []),
        "passing_fields": verdict_raw.get("passing_fields", []),
        "segment_recommendations": None,
        "run_metadata": {
            "run_date": run_date,
            "systems_evaluated": systems,
            "golden_set_size": golden_set_size,
        },
    }


def _infer_golden_size(grades: list[dict[str, Any]]) -> int:
    """Heuristic when caller doesn't supply: count distinct (transcript_id, field) pairs."""
    return len({(g["transcript_id"], g["field"]) for g in grades})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> int:
    parser = argparse.ArgumentParser(description="Render scorecard.md, audit-log.jsonl, verdict.json.")
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--threshold-results", type=Path, required=True)
    parser.add_argument("--verdict", type=Path, required=True, help="Path to verdict-raw.json from verdict.py")
    parser.add_argument("--thresholds", type=Path, required=False, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--golden-set-size", type=int, required=False, default=None)
    parser.add_argument("--run-date", type=str, required=False, default=None)
    args = parser.parse_args()

    grades = [json.loads(l) for l in args.grades.read_text().splitlines() if l.strip()]
    metrics = json.loads(args.metrics.read_text())
    threshold_results = json.loads(args.threshold_results.read_text())
    verdict_raw = json.loads(args.verdict.read_text())
    thresholds = yaml.safe_load(args.thresholds.read_text()) if args.thresholds else {}

    emit_all(
        output_dir=args.output_dir,
        grades=grades,
        metrics=metrics,
        threshold_results=threshold_results,
        verdict_raw=verdict_raw,
        thresholds=thresholds,
        run_date=args.run_date,
        golden_set_size=args.golden_set_size,
    )
    print(f"Wrote scorecard.md, audit-log.jsonl, verdict.json to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
