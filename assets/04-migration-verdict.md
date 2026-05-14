# Stage 4 — Decide the migration verdict

**What this stage does:** Applies the migration rules from `thresholds.yaml` to produce one of `ship` / `ship_segment` / `hold` with a reason.

## Inputs

- `output/threshold-results.json`
- `output/metrics.json`
- `thresholds.yaml`

## Output

`output/verdict-raw.json` — the verdict object before stage 5 wraps it with run_metadata.

## How to run

```bash
python scripts/verdict.py \
  --threshold-results output/threshold-results.json \
  --metrics output/metrics.json \
  --thresholds <thresholds.yaml> \
  --output output/verdict-raw.json
```

## The rules

A field is **load-bearing** if its `weight >= 2.0` in `thresholds.yaml`.

**Two-system (migration) mode** — rules applied in order, first match wins:
1. Any load-bearing field regresses beyond tolerance → **`hold`** (load-bearing regression).
2. Any load-bearing field is below its threshold floor → **`hold`** (load-bearing threshold fail).
3. No regressions and all thresholds pass → **`ship`**.
4. Otherwise → **`ship_segment`** (non-load-bearing miss).

**Single-system mode** — no regression check:
1. All thresholds pass → **`ship`**.
2. Any load-bearing threshold fail → **`hold`**.
3. Otherwise → **`ship_segment`**.

## Why this rule structure

Aggregate accuracy is the wrong objective when EB errors cost $1.2M and Metrics errors cost a QBR slide. The verdict logic encodes that asymmetry: a 3 pp regression on EB beats a 5 pp improvement on Metrics, every time. If the panel asks "why hold when accuracy improved overall?" — the answer is in this rule structure.

## What `reason` should contain

The verdict's `reason` string must name the specific field(s) that drove the decision so a reader can understand why without re-reading the metrics. The Python implementation does this automatically; if you regenerate the verdict manually, follow the same pattern.
