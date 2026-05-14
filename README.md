# meddpicc-eval

**A practical eval harness for deciding whether to trust — or switch — your AI model for MEDDPICC extraction.**

When an AI writes to Salesforce, the only question that matters is: *is it right often enough on the things that cost money when it's wrong?* This skill answers that question in 20 minutes, with a one-page scorecard, a deploy/hold verdict, and an audit log naming the specific failure modes hurting you most.

---

## The decision this skill makes practical

Three scenarios this skill is built for:

1. **"Our model provider deprecated the version we built on. Should we migrate?"**
2. **"We rewrote the prompt. Is the new version actually better, or just different?"**
3. **"Reps don't trust our extractions anymore. Where is the system actually failing?"**

Each needs an answer in hours, not weeks.

## Why it's different from a generic accuracy report

Generic eval tools optimize for aggregate accuracy. That's the wrong objective function for a system writing to forecast.

A new model that's **3 points worse on Economic Buyer but 5 points better on Metrics** looks like an improvement on aggregate — and is a deploy decision that costs $1.2M deals. Economic Buyer errors mis-forecast quarters; Metrics errors are slide noise. Treating them as equal weight is how trust collapses.

This skill weights fields by what they cost when wrong. The 3%/5% scenario above produces a **hold** verdict, with the regression named (`champion_not_eb`, concentrated in deals over $1M) so the decision can be acted on, not just absorbed.

## What you give it. What you get back.

**Inputs (two things you already have or can produce):**

- **An answer key** — sales calls where your team has written down the correct MEDDPICC values. The skill grades against this.
- **Your AI's answers** — what your extraction system actually wrote. To compare two systems, hand over both.

The skill never reads transcripts, calls an API, or touches your CRM. Answer key in, AI's answers in, decision out.

**Outputs (three artifacts):**

1. **Scorecard** — one page. Plain English. Reads like a Friday QBR slide.
2. **Verdict** — `ship` / `hold` / `ship_segment`. Machine-readable so CI pipelines can gate deploys on it.
3. **Audit log** — every wrong call, tagged by failure mode (`champion_not_eb`, `named_but_absent_cfo`, `status_quo_competitor`, etc.) and deal segment. Turns "accuracy dropped" into "here are the specific calls and the specific kinds of mistakes."

## What "practical" means here

| | Research-paper approach | This skill |
|---|---|---|
| **Decision speed** | Two weeks of analysis | 20 minutes |
| **Output format** | Precision-recall curves | One-page scorecard + verdict |
| **Threshold logic** | Aggregate accuracy | Field-weighted by business cost |
| **Failure surfacing** | "EB regressed by 3%" | "EB regressed on `champion_not_eb`, concentrated in deals over $1M" |
| **Audience** | ML engineer | Sales VP, RevOps lead, CI pipeline |

## Try the included demo

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

Runs in under a second. The scorecard opens with a **hold** verdict, names Economic Buyer as the failing field, and surfaces `champion_not_eb` in the audit log — the failure mode the design was built to catch.

## Built to outlive any one extractor

The skill is **extractor-agnostic** — it works with any model, any prompt version, any vendor that produces the documented JSON shape. Swap models, rewrite prompts, change vendors. The skill stays.

It's also **field-agnostic**. The default ships seven MEDDPICC fields. To grade Paper Process or any other variant, add one entry to `thresholds.yaml`. No code change.

## What this skill deliberately does NOT do

Scope discipline is the design:

- **Doesn't extract.** That's the system being evaluated, not the evaluator.
- **Doesn't recommend architecture.** It produces a decision; humans act on it.
- **Doesn't replace labelers.** The rubric guides them; the skill consumes their work.

This is what keeps the skill general. An evaluator that also extracts, recommends, and labels is a research project. An evaluator that only evaluates is a tool.

## Production readiness

The seed golden set (12 rows) demonstrates every edge-case tag and runs the 5-minute test cleanly. For a leadership-facing migration decision, the production commitment is **200 calls**, stratified by deal size band, labeled by **two AEs with enablement adjudication** on disagreements. The labeling rubric is in `assets/rubric.md`.

## File contract (for the engineer integrating it)

```
inputs/
  golden-set.jsonl              One row per (transcript_id, field)
  extractions/{system}/         One JSON per transcript, per system
  thresholds.yaml               Field weights and acceptance thresholds (optional override)

output/
  scorecard.md                  Human-readable
  verdict.json                  Machine-readable (ship / hold / ship_segment)
  audit-log.jsonl               Per-failure diagnosis with edge_case_tag + segment
```

Schemas live in `schemas/`. Run `pytest tests/ -v` to see the 103-test coverage; everything runs in under a second with no external dependencies.

---

**Bottom line:** if you're trying to decide whether your AI is good enough to trust with deals that matter, this skill gives you the answer in a form a VP can act on, a CI pipeline can gate on, and a rep can audit. That's the difference between an eval harness and a decision tool.
