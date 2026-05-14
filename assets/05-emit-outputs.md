# Stage 5 — Emit outputs

**What this stage does:** Renders the three deliverable artifacts. Final stage.

## Inputs

- `output/confirmed-grades.jsonl`
- `output/metrics.json`
- `output/threshold-results.json`
- `output/verdict-raw.json`
- `thresholds.yaml`

## Outputs

| File | Audience | Contents |
|---|---|---|
| `output/scorecard.md` | Sales leader / take-home reviewer | Title, run metadata, verdict block with reason, per-field table, segment + edge-case breakdowns for load-bearing fields |
| `output/audit-log.jsonl` | Engineer diagnosing the regression | One row per (transcript, field, system) failure |
| `output/verdict.json` | CI gate, downstream automation | Verdict + reason + failing_fields + run_metadata. Validates against `schemas/verdict.schema.json`. |

## How to run

```bash
python scripts/emit.py \
  --grades output/confirmed-grades.jsonl \
  --metrics output/metrics.json \
  --threshold-results output/threshold-results.json \
  --verdict output/verdict-raw.json \
  --thresholds <thresholds.yaml> \
  --output-dir output/
```

## What "good" looks like

The scorecard should be readable by a non-technical sales leader. No jargon beyond MEDDPICC. The verdict block names the failing field if any. The per-field table shows current/candidate/Δ in two-system mode with a ⚠ marker on regressing fields. Segment and edge-case breakdowns appear for load-bearing fields only (to keep the document scannable).

The audit log must preserve `edge_case_tag` and `segment` on every row — that's what makes failures inspectable rather than countable. If the audit log doesn't surface the tag, the tagging is decorative.

The verdict.json must validate against `schemas/verdict.schema.json`. Run twice on identical inputs: outputs should be bit-identical except for the `run_date` line.

## Report the file paths

After emission, surface the three file paths to the user so they can open the scorecard immediately:

```
scorecard:    output/scorecard.md
audit log:    output/audit-log.jsonl
verdict.json: output/verdict.json
```
