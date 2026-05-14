# MEDDPICC Labeling Rubric

Production guide for AEs and enablement when building a labeled golden set. The seed golden set in `assets/seed-golden-set.jsonl` follows this rubric.

## Per-field guidance

### Economic Buyer

The person with budget authority and final sign-off.

- `gold_value` is **non-null** when the call surfaces a specific individual with budget authority for the deal size in question. Capture them as `"<Name>, <Title>"` when both are present.
- `gold_value` is **null** when no such person is identifiable from the call. This includes:
  - Champion is engaged but does not control budget (`champion_not_eb`).
  - A senior title is mentioned but no authority signal is given (`title_alone_insufficient`).
  - No EB is extractable at all (`no_eb_extractable`).
- For multi-party sign-off (`multi_party_signoff`), list both names separated by `; `.
- For a named-but-absent EB (CFO referenced but not on call), capture the name — they are the EB even if not in the room.

### Metrics

Quantified business outcomes the prospect stated.

- Extract only what the prospect quantified. Rep-asserted numbers the prospect did not affirm are `gold_value: null` with tag `rep_claimed_metric`.
- Preserve the quantification: `"Reduce close time from 8 days to 3"` is correct; `"Faster close"` is not (it's not a metric).

### Decision Criteria

The factors the buyer uses to evaluate solutions. Free-form prose acceptable.

- Capture each criterion in one short phrase. Multiple criteria → join with `; `.
- Implicit criteria (rep inferred from context) → `gold_value: null` unless the prospect named them.

### Decision Process

The buyer's internal steps to a decision.

- `ordered_decision_process`: prospect describes a clear sequence (e.g., "security review → procurement → CFO sign-off"). Capture in order.
- `implied_decision_process`: process implied but not explicitly stated → `gold_value: null` or low confidence.

### Identify Pain

The prospect's critical problem the solution addresses.

- `prospect_affirmed_pain`: prospect stated and confirmed the pain. Extract.
- `rep_asserted_pain_unaffirmed`: rep stated the pain; prospect didn't affirm. `gold_value: null`.

### Champion

The internal advocate. **Always distinct from Economic Buyer.** Conflating them is the most common extraction failure.

- `gold_value` is the champion's name+title.
- The same person should not be labeled as both Champion and EB. If they are both, choose the role that best fits the call's evidence.

### Competition

Alternatives including status quo.

- `status_quo_competitor`: prospect describes "doing nothing" / current manual workflow → extract as such. Most under-recorded competitor.
- `vendor_competitor_named`: prospect named a specific competitor. Extract the vendor name.
- Multiple competitors → list separated by `; `.

## Confidence levels

| Level | Meaning |
|---|---|
| `high` | The call clearly supports this label. A second reader would agree without discussion. |
| `medium` | The call supports this label but inference was required. A second reader might phrase it differently. |
| `low` | The label is the labeler's best read, but the call could reasonably support a different interpretation. |
| `none` | The labeler is uncertain. Rows with `gold_confidence: "none"` are excluded from precision calibration but included in recall. |

## Evidence quotes

`gold_evidence_quote` is the verbatim transcript span that supports the label. **Required when `gold_value` is non-null. Must be `null` when `gold_value` is null.**

Use the shortest span that supports the label. Prefer prospect speech over rep speech (a rep's claim doesn't make it true).

## Edge case tags

`edge_case_tag` names the failure mode being tested. The seed golden set uses the tags below; production sets may add more.

| Tag | Field | Tests |
|---|---|---|
| `champion_not_eb` | economic_buyer | System must not promote champion to EB |
| `named_but_absent_cfo` | economic_buyer | EB named but not on call — extract |
| `multi_party_signoff` | economic_buyer | Joint authority — list both |
| `title_alone_insufficient` | economic_buyer | Senior title alone does not imply EB authority |
| `no_eb_extractable` | economic_buyer | Genuinely no EB on this call |
| `clear_eb_stated` | economic_buyer | The easy case |
| `rep_asserted_pain_unaffirmed` | identify_pain | Rep-asserted, prospect didn't affirm |
| `prospect_affirmed_pain` | identify_pain | The easy case |
| `status_quo_competitor` | competition | "Doing nothing" / manual workflow |
| `vendor_competitor_named` | competition | Specific vendor named |
| `rep_claimed_metric` | metrics | Rep cites a number the prospect didn't confirm |
| `prospect_quantified_outcome` | metrics | Prospect stated a quantified goal |
| `ordered_decision_process` | decision_process | Explicit step sequence |
| `implied_decision_process` | decision_process | Process implied but not stated |

To add a new tag, document the failure mode it tests in a comment alongside the first row that uses it.

## Adjudication

When two labelers disagree:
1. Compare evidence quotes. The labeler with the more grounded quote usually wins.
2. If still disagreeing, enablement breaks the tie. Document the resolution in the commit message.
3. If neither labeler can ground the label in a transcript span, set `gold_confidence: "none"` and move on.

Disagreement rate on EB > 10% suggests the rubric isn't tight enough for your sales motion — bring it back to enablement for refinement.

## Adding a new field (e.g., Paper Process)

The skill is field-agnostic. To grade Paper Process:

1. Add a `paper_process:` entry to `thresholds.yaml` with the appropriate `weight`, `min_precision_high`, etc.
2. Add a per-field guidance section to this rubric.
3. Ensure every extraction file in `extractions/<system>/<transcript>.json` carries a `paper_process` key.
4. Label `(transcript_id, paper_process)` rows in your golden set.

No code change required.

## Production target

The seed golden set is 12 rows for the 5-minute test recipe. The production commitment is:
- **200 calls** stratified by deal_size_band (40% under_250k, 35% 250k_to_1m, 25% over_1m)
- Labeled by **two AEs** independently
- **Enablement adjudication** on disagreements
- Re-labeled annually or whenever the underlying sales motion materially changes
