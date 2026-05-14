---
name: meddpicc-eval
description: Use when grading MEDDPICC extraction outputs against a labeled golden set to make a model-migration, prompt-version, or extractor-swap decision. Produces a per-field scorecard, a machine-readable verdict (hold/ship/ship_segment), and an edge-case-tagged audit log of failures. Extractor-agnostic — works with any model or prompt that produces the documented JSON output shape. Field set is configuration-driven via thresholds.yaml so the skill supports any MEDDPICC variant.
---

# MEDDPICC Eval

## Overview

Extractor-agnostic eval harness for MEDDPICC extraction systems. Takes a labeled golden set and one or more sets of extraction outputs and produces a Friday-decision-grade scorecard, a CI-gateable verdict, and an audit log of failures tagged by edge case.

The skill is field-agnostic: `thresholds.yaml` declares which MEDDPICC fields are graded. Default config ships the seven fields from the v1 spec (Metrics, Economic Buyer, Decision Criteria, Decision Process, Identify Pain, Champion, Competition). To grade Paper Process or any other variant field, add one config entry — no code change.

## When to use

Trigger this skill when the user wants to:
- Decide whether to migrate to a new model, prompt version, or extractor on a MEDDPICC system.
- Compare two extractors (current vs candidate) on a labeled call set.
- Evaluate a single extractor against per-field quality thresholds.
- Diagnose why an extraction system is producing untrusted outputs.

Do not use this skill to:
- **Build an extractor.** This skill grades outputs the user produces upstream.
- **Generate transcripts or synthetic data.** The skill operates on labels + extraction outputs.
- **Make architecture recommendations.** Produces a verdict and an audit log; design changes are a downstream human decision.
- **Replace human labeling.** Consumes labels; the rubric (assets/rubric.md) guides labelers.

## Input contract

```
inputs/
  golden-set.jsonl                     Labeled ground truth (one row per (transcript_id, field))
  extractions/
    {system_name}/                     One folder per extractor being evaluated
      {transcript_id}.json             One file per transcript
  thresholds.yaml                      (Optional) override the default thresholds
```

Schemas: `schemas/golden-set.schema.json`, `schemas/extraction.schema.json`, `schemas/thresholds.schema.json`.

## Output contract

Three artifacts written to `--output-dir`:

| File | Audience | Purpose |
|---|---|---|
| `scorecard.md` | Sales leader, take-home reviewer | Human-readable Friday decision |
| `verdict.json` | CI gate, downstream automation | Machine-readable verdict + metadata |
| `audit-log.jsonl` | The engineer diagnosing the regression | One row per (transcript, field, system) failure, tagged by edge case |

`verdict.json` schema: `schemas/verdict.schema.json`. Verdict values: `ship` / `ship_segment` / `hold`.

## Pipeline — six stages

Each stage has an instruction file in `assets/NN-*.md` plus a Python helper in `scripts/`. The Python helpers do the deterministic math; one stage (01) makes an LLM judgment call for semantic-match decisions.

| Stage | What it does | Script | Instruction |
|---|---|---|---|
| 0 | Validate inputs against schemas; bail on contract violations. | `validate_inputs.py` | `assets/00-load-inputs.md` |
| 1 | Per-row grading: match kind, calibration, abstention, evidence faithfulness. Flag `needs_semantic_review` rows for the LLM judge. | `grade.py` + `confirm_grades.py` | `assets/01-grade-extractions.md` |
| 2 | Aggregate: precision / recall / F1 / abstention rate / precision@high. Segment + edge-case breakdowns. | `aggregate.py` | `assets/02-aggregate-metrics.md` |
| 3 | Apply per-field thresholds (PASS/FAIL) and compare paired systems (REGRESSION/IMPROVEMENT/NO_REGRESSION). | `apply_thresholds.py` | `assets/03-apply-thresholds.md` |
| 4 | Decide the migration verdict: `ship` / `ship_segment` / `hold`. | `verdict.py` | `assets/04-migration-verdict.md` |
| 5 | Render the three output artifacts. | `emit.py` | `assets/05-emit-outputs.md` |

The orchestrator `scripts/run_eval.py` runs all six stages in sequence. On a fixture with exact-match-only extractions (no needs_semantic_review rows) the whole pipeline runs in milliseconds with no API calls.

## Quick-start

5-minute test recipe with the seed:

```bash
cd library/skills/meddpicc-eval
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/run_eval.py \
  --golden assets/seed-golden-set.jsonl \
  --extractions <your_extractions_dir> \
  --thresholds thresholds.yaml \
  --output-dir output/
cat output/scorecard.md
```

Run the regression test:

```bash
.venv/bin/pytest tests/ -v
```

## What's load-bearing in this design

Three choices the panel may probe:

1. **Extractor-agnostic input contract.** Works with any extractor that produces the documented JSON. The cost is upstream contract work for the user; the benefit is the skill outlives any specific extractor.

2. **Business-cost-weighted thresholds.** EB has weight 3.0, zero regression tolerance. Metrics has weight 1.0, 3% tolerance. Aggregate accuracy is the wrong objective when an EB error costs $1.2M and a metrics error costs a QBR slide. The 3%/5% scenario in the take-home is the textbook case where aggregate improves but the load-bearing field regresses — the verdict is `hold`.

3. **Edge-case tagging.** Without tags, a regression on `champion_not_eb` is invisible in aggregate precision. With tags, the audit log surfaces it. Tags turn the scorecard from a number into a diagnosis.

## Verification

After running the skill, the deliverable must:

- `scorecard.md` is readable by a non-technical sales leader (no jargon beyond MEDDPICC).
- `verdict.json` validates against `schemas/verdict.schema.json`.
- `audit-log.jsonl` surfaces each failure with its `edge_case_tag` and `segment` intact.
- Re-running on identical inputs produces bit-identical scorecard.md, verdict.json, audit-log.jsonl (modulo the run_date metadata line). Verified by `tests/test_e2e.py::TestDeterminism`.

The headline regression test — `tests/test_e2e.py::TestThreeFiveVerdict` — asserts that the 3%/5% fixture produces `hold`. If that ever turns green for `ship`, the verdict logic broke.
