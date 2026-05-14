# meddpicc-eval

Extractor-agnostic eval harness for MEDDPICC extraction systems. Given a labeled golden set and one or more sets of extraction outputs, produces a Friday-decision-grade scorecard, a CI-gateable verdict (`hold` / `ship` / `ship_segment`), and an audit log of failures tagged by edge case.

## Why this shape

Three load-bearing design choices:

1. **Extractor-agnostic input contract.** Works with any extractor — any model, any prompt version — that produces the documented JSON shape. The cost is upstream contract work for the user; the benefit is the skill outlives any specific extractor.

2. **Business-cost-weighted thresholds, not aggregate accuracy.** Economic Buyer has `weight: 3.0` and `regression_tolerance: 0.00`. Metrics has `weight: 1.0` and `regression_tolerance: 0.03`. EB errors cost $1.2M deals; Metrics errors cost QBR slides. The thresholds reflect that. The textbook case where a candidate model is 3 pp worse on EB and 5 pp better on Metrics produces `hold`, not `ship` — the load-bearing regression is the decision, not the aggregate.

3. **Edge-case tagging in the golden set.** Without tags, a regression on `champion_not_eb` is invisible in aggregate precision. With tags, the audit log says exactly what failure mode the candidate model is worse on. Tags turn the scorecard from a number into a diagnosis.

## Quick start

The fastest way to see this work is to run it against the deterministic 3%/5% fixture that ships with the skill. No input preparation, no API keys, no network calls — the full pipeline runs in under a second.

### Option A — Run the demo fixture (60 seconds)

```bash
cd library/skills/meddpicc-eval
python -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python scripts/run_eval.py \
  --golden tests/fixtures/three_five_scenario/golden-set.jsonl \
  --extractions tests/fixtures/three_five_scenario/extractions \
  --thresholds thresholds.yaml \
  --judgments tests/fixtures/three_five_scenario/judgments.jsonl \
  --output-dir output/

cat output/scorecard.md
```

What the scorecard shows:

- **Verdict: HOLD** — `Load-bearing field(s) ['economic_buyer'] regress beyond tolerance in the candidate system.`
- Per-field table: **EB 0.90 → 0.85 (-0.05 ⚠ FAIL)** alongside **Metrics 0.88 → 0.95 (+0.08 PASS)**. The aggregate improved, the load-bearing field regressed, the verdict is `hold`. That's the whole thesis in one table.
- EB segment breakdown: `over_1m: 1.00 → 0.92 (-0.08)` — the regression is concentrated in the expensive segment.
- EB edge-case breakdown: `champion_not_eb: 0.87 → 0.73 (-0.13)` — the $1.2M failure mode, named.

`output/` also contains `verdict.json` (machine-readable, CI-gateable) and `audit-log.jsonl` (one row per failure, with `edge_case_tag` and `segment` intact for diagnosis).

### Option B — Run on your own data

Three inputs go in, three artifacts come out.

**Inputs you produce:**

```
inputs/
├── golden-set.jsonl                   # one row per (transcript_id, field), schema: schemas/golden-set.schema.json
└── extractions/
    ├── current_model/                 # one folder per extractor you want to evaluate
    │   ├── T001.json                  # one file per transcript, schema: schemas/extraction.schema.json
    │   ├── T002.json
    │   └── ...
    └── candidate_model/               # (optional) a second extractor for comparison
        └── ...
```

`thresholds.yaml` already ships with sensible defaults — copy it, edit per your cost model, or use as-is.

**Run:**

```bash
.venv/bin/python scripts/run_eval.py \
  --golden inputs/golden-set.jsonl \
  --extractions inputs/extractions/ \
  --thresholds thresholds.yaml \
  --output-dir output/
```

If any extraction has `value` and `gold_value` both non-null but not exactly equal, the skill flags those rows for semantic-match judgment and writes them to `output/tentative-grades.jsonl`. Resolve them by appending one JSON line per row to `judgments.jsonl` (see [assets/01-grade-extractions.md](assets/01-grade-extractions.md) for the per-field decision criteria), then re-run with `--judgments judgments.jsonl`.

**Outputs you get:**

| File | For |
|---|---|
| `output/scorecard.md` | The Friday-decision document. Readable by a sales leader. |
| `output/verdict.json` | Machine-readable verdict (`hold` / `ship` / `ship_segment`). Gate your CI on it. |
| `output/audit-log.jsonl` | One row per failure, tagged. Diagnose what broke, not just how often. |

The skill validates inputs on entry and **bails loudly on contract violations** — malformed schema, missing fields, orphan extractions, contract weirdness. Silent acceptance of malformed input is how eval harnesses ship confidently wrong scorecards.

## Paper Process and other MEDDPICC variants

The skill is field-agnostic by design. The default `thresholds.yaml` ships the seven fields from the v1 spec (Metrics, Economic Buyer, Decision Criteria, Decision Process, Identify Pain, Champion, Competition). To grade Paper Process — or any other variant field — add one entry to `thresholds.yaml`:

```yaml
fields:
  paper_process:
    weight: 1.5
    min_precision_high: 0.92
    min_recall: 0.75
    max_abstention_rate: 0.50
    regression_tolerance: 0.02
```

…and ensure each extraction file carries a `paper_process` key. **No code change required.**

## Input contract

```
inputs/
  golden-set.jsonl                     One row per (transcript_id, field). Schema: schemas/golden-set.schema.json
  extractions/
    {system_name}/                     One folder per extractor
      {transcript_id}.json             Schema: schemas/extraction.schema.json
  thresholds.yaml                      (Optional) Schema: schemas/thresholds.schema.json
```

## Output contract

```
output/
  scorecard.md                         Human-readable, sales-leader-readable Friday decision
  audit-log.jsonl                      One row per failure, tagged by edge case + segment
  verdict.json                         Machine-readable, CI-gateable. Schema: schemas/verdict.schema.json
```

`verdict.json` values: `verdict ∈ {ship, ship_segment, hold}`, plus `reason`, `failing_fields`, `passing_fields`, `run_metadata`.

## Production readiness

The seed golden set (12 rows) is enough for the 5-minute test recipe and to demonstrate every edge-case tag the skill is designed to surface.

The production commitment for a sales-leader-facing migration decision is **200 calls**, stratified by deal_size_band per the rubric, labeled by **two AEs** with **enablement adjudication** on disagreements. The `assets/rubric.md` document is the guide AEs and enablement use.

## What this skill deliberately does NOT do

From the v1 spec — binding scope:

- **Does not build an extractor.** Grades outputs the user produces upstream. Extractor-agnostic by contract.
- **Does not generate transcripts.** Operates on labels + extraction outputs.
- **Does not make architecture recommendations.** Produces a verdict and an audit log; design changes are a downstream human decision.
- **Does not do cross-call entity resolution.** That belongs in CRM enrichment, upstream of both extraction and evaluation.
- **Does not replace human labeling.** The rubric guides labelers; the skill consumes labels.

## Versioning

- **Current version:** 1.0
- **Threshold philosophy:** business-cost-weighted, not aggregate accuracy. Tuned per organization in `thresholds.yaml`. EB defaults are conservative because EB errors are asymmetrically expensive.
- **Independent versioning:** `thresholds.yaml` can be tuned without touching the grading logic. The grading logic can be improved (better semantic match, additional calibration metrics) without re-labeling the golden set.

## Testing

```bash
.venv/bin/pytest tests/ -v
```

The test suite covers:

- **Schema validation** (10 adversarial cases): malformed golden rows, invalid enums, orphan extractions, contract violations
- **Grading core** (26 tests): match-kind classification, confidence calibration, abstention handling, evidence faithfulness, contract-weirdness warnings
- **Semantic-match plumbing** (9 tests): judgment merge, missing-judgment failure, invalid-decision failure, per-field prompt formatting
- **Aggregation** (17 tests): precision/recall/F1, divide-by-zero guards, segment breakdown, edge-case breakdown
- **Thresholds** (12 tests): per-check failures, regression detection, tolerance handling
- **Verdict** (7 tests): single-system, two-system, the 3%/5% scenario as a unit test
- **Emission** (8 tests): scorecard structure, audit log per-row presence, verdict.json schema validity
- **End-to-end** (13 tests): the 3%/5% fixture produces `hold`, EB regresses, Metrics improves, champion_not_eb surfaces in the audit log, over_1m segment regression shows in the scorecard, the pipeline is deterministic on identical inputs

98 tests total. They run in under a second with no external dependencies.

## Layout

```
meddpicc-eval/
├── SKILL.md              Orchestrator. When-to-use, contract, pipeline.
├── README.md             This document.
├── thresholds.yaml       Default field-weighted thresholds with inline justifications.
├── assets/
│   ├── 00-load-inputs.md through 05-emit-outputs.md  Stage instructions
│   ├── seed-golden-set.jsonl  12 deliberate failure-mode tests
│   └── rubric.md         Labeling guide for production golden sets
├── schemas/              JSON schemas for inputs and outputs
├── scripts/              Python helpers (validate, grade, aggregate, verdict, emit, run_eval)
└── tests/                pytest suite + fixtures including three_five_scenario
```
