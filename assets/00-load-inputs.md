# Stage 0 — Load and validate inputs

**What this stage does:** Confirms the user's golden set, extraction outputs, and thresholds file all match the documented contract. Bails loudly on any violation.

## Inputs

- `golden-set.jsonl` — labeled rows, one per `(transcript_id, field)`
- `extractions/{system_name}/{transcript_id}.json` — extraction outputs per system per transcript
- `thresholds.yaml` — field declarations + per-field thresholds + migration rules

## Output

Nothing on disk. Either succeeds silently, or prints a list of contract violations to stderr and exits non-zero.

## How to run

```bash
python scripts/validate_inputs.py \
  --golden <golden-set.jsonl> \
  --extractions <extractions-dir> \
  --thresholds <thresholds.yaml>
```

## What to do if it fails

**Do not proceed.** Print the errors verbatim and stop. The validator catches contract violations the eval pipeline would otherwise paper over with wrong scorecards:

- Missing required fields in a golden row
- Invalid confidence enum values (must be `high`/`medium`/`low`/`none`)
- `gold_evidence_quote` non-null when `gold_value` is null (and vice versa)
- Extraction files missing one of the fields declared in `thresholds.yaml`
- Extraction files with field keys NOT declared in `thresholds.yaml`
- Extraction `transcript_id`s that don't appear in the golden set (orphan extractions)
- Non-JSON lines in the JSONL
- Empty input files

Warnings (not errors): contract weirdness like `value: null` with non-`none` confidence — recorded as a warning on the grade row so it surfaces in the audit log later.
