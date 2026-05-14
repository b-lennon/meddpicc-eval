# Stage 3 — Apply thresholds

**What this stage does:** Turns aggregate metrics into per-field PASS/FAIL flags, and (in two-system mode) per-field regression flags.

## Inputs

- `output/metrics.json`
- `thresholds.yaml`

## Output

`output/threshold-results.json` with two sections:
- `threshold_results`: `{system: {field: {overall: pass|fail, failed_checks: [...]}}}` — per-field pass/fail with the specific checks that failed (`precision_high` / `recall` / `abstention_rate`).
- `regressions` (only in two-system mode): `{candidate: {field: {status: no_regression|improvement|regression, delta_precision_high: ..., ...}}}`.

## How to run

```bash
python scripts/apply_thresholds.py \
  --metrics output/metrics.json \
  --thresholds <thresholds.yaml> \
  --output output/threshold-results.json
```

## How the checks work

Per field, three checks:
- `precision_high >= min_precision_high` → otherwise FAIL with `precision_high` in `failed_checks`.
- `recall >= min_recall` → otherwise FAIL with `recall` in `failed_checks`.
- `abstention_rate <= max_abstention_rate` → otherwise FAIL with `abstention_rate` in `failed_checks`.

Any failure → field is FAIL overall.

Regression check (two-system mode): `delta_precision_high = candidate.precision_high - current.precision_high`.
- `delta > 0` → IMPROVEMENT.
- `delta == 0` → NO_REGRESSION.
- `-delta <= regression_tolerance` → NO_REGRESSION.
- `-delta > regression_tolerance` → REGRESSION.
