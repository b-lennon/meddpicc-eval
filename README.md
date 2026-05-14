# meddpicc-eval

Extractor-agnostic eval harness for MEDDPICC extraction systems. Given a labeled golden set and one or more sets of extraction outputs, produces a Friday-decision-grade scorecard, a CI-gateable verdict (`hold` / `ship` / `ship_segment`), and an audit log of failures tagged by edge case.

## Why this shape

Three load-bearing design choices:

1. **Extractor-agnostic input contract.** Works with any extractor — any model, any prompt version — that produces the documented JSON shape. The cost is upstream contract work for the user; the benefit is the skill outlives any specific extractor.

2. **Business-cost-weighted thresholds, not aggregate accuracy.** Economic Buyer has `weight: 3.0` and `regression_tolerance: 0.00`. Metrics has `weight: 1.0` and `regression_tolerance: 0.03`. EB errors cost $1.2M deals; Metrics errors cost QBR slides. The thresholds reflect that. The textbook case where a candidate model is 3 pp worse on EB and 5 pp better on Metrics produces `hold`, not `ship` — the load-bearing regression is the decision, not the aggregate.

3. **Edge-case tagging in the golden set.** Without tags, a regression on `champion_not_eb` is invisible in aggregate precision. With tags, the audit log says exactly what failure mode the candidate model is worse on. Tags turn the scorecard from a number into a diagnosis.

## How it works

This is a model evaluation tool, purpose-built for MEDDPICC extraction. You give it your answer key and your AI's answers, and it tells you whether your AI is good enough to trust — or, if you're comparing two systems, whether switching is safe.

### What you put in

Two things, both of which your team already has or can produce:

1. **An answer key** — a set of sales calls where your team has written down the correct MEDDPICC values. Who was the Economic Buyer? Was there a clear champion? Did the prospect quantify a goal? The skill calls this the *golden set*. Think of it as the grading rubric.

2. **Your AI's answers** — what your extraction system actually said the MEDDPICC values were on those same calls. To compare two systems (current model vs. a replacement; v1 prompt vs. v2), hand over both.

The skill never reads call transcripts, never calls an API, never touches your CRM. Just the answer key and what the AI said.

### What you get back

Three things:

1. **A scorecard** — one page, plain English. How often is the AI right? On which failure modes? On which deal sizes? Read it the way a sales VP reads a Friday QBR slide.

2. **A verdict** — one of three decisions a CI pipeline can act on automatically:
   - **`ship`** — the new system is at least as good as the current one. Deploy it.
   - **`hold`** — the new system regresses on something that costs real money. Don't deploy.
   - **`ship_segment`** — safe for some deal types, not others. Partial deploy.

3. **An audit log** — every call the AI got wrong, tagged by *failure mode*. Instead of "EB accuracy dropped three points," you see the specific calls and the specific kind of mistake — e.g., the AI confused the champion for the Economic Buyer. That's the failure mode that cost $1.2M last quarter; the audit log surfaces it by name.

### How it decides

You tell the skill once, in a config file, how much each MEDDPICC field is worth to your business. Economic Buyer is load-bearing — a wrong EB on a $1.2M deal mis-forecasts the quarter, so zero tolerance for regression. Metrics is recoverable — a slightly wrong number on a QBR slide is annoying, not deal-killing, so a few points of slack is fine.

The skill grades the AI's answers against the answer key, splits the results by failure mode and deal size, and checks them against your thresholds.

The headline case the skill is designed for: a new model that's 3 points worse on Economic Buyer and 5 points better on Metrics. Aggregate accuracy improved. This skill says **`hold`** — because the regression hit the field that costs the most when it's wrong. That asymmetry is the whole point.

### Try the included demo

To see the above end-to-end on data that ships with the skill (no inputs to prepare):

```bash
cd library/skills/meddpicc-eval
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/run_eval.py \
  --golden tests/fixtures/three_five_scenario/golden-set.jsonl \
  --extractions tests/fixtures/three_five_scenario/extractions \
  --thresholds thresholds.yaml \
  --judgments tests/fixtures/three_five_scenario/judgments.jsonl \
  --output-dir output/
open output/scorecard.md
```

The demo runs in under a second. The scorecard opens with a `hold` verdict, names `economic_buyer` as the failing field, and surfaces the `champion_not_eb` regression in the audit log — exactly the scenario described in [Why this shape](#why-this-shape) above. For running it on your own data, the exact file shapes are in [Input contract](#input-contract) below.

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
