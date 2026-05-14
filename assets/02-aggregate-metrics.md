# Stage 2 — Aggregate metrics

**What this stage does:** Rolls confirmed-grades up to per-system, per-field metrics. Produces breakdowns by segment (deal size, stage, call type) and by edge_case_tag.

## Inputs

- `output/confirmed-grades.jsonl`
- `thresholds.yaml` (declares the field set)

## Output

`output/metrics.json` with per-system, per-field aggregates including segment and edge-case breakdowns.

## How to run

```bash
python scripts/aggregate.py \
  --grades output/confirmed-grades.jsonl \
  --thresholds <thresholds.yaml> \
  --output output/metrics.json
```

## What it computes

For each `(system, field)`:
- `precision = TP / (TP + FP)` where TP = `match_exact` + `match_semantic`, FP = `false_positive` + `mismatch`.
- `recall = TP / (TP + FN)` where FN = `false_negative` + `mismatch`.
- `f1` = harmonic mean.
- `abstention_rate` = `sys_value is null` count / total.
- `precision_high` = precision restricted to rows where `sys_confidence == "high"` AND `gold_confidence != "none"`. This is the confidence-calibration gate threshold.yaml's `min_precision_high` checks against.
- `accuracy` = (TP + TN) / total. Used in the segment and edge-case scorecard sections because precision is degenerate on null-gold subsets.

Then groups by segment (`deal_size_band`, `stage`, `call_type`) and by `edge_case_tag` to produce sub-aggregates. Empty groups are omitted.

## What `gold_confidence == "none"` means

Labeler-uncertain rows are excluded from `precision_high` (we don't grade calibration against an uncertain gold). They are still included in recall (the system should still try to extract).
