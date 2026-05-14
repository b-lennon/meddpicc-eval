"""Deterministic builder for the 3%/5% regression-test fixture.

Generates 100 transcripts × 7 fields of golden labels plus two paired
extraction sets engineered to produce: EB precision@high 0.94→0.91
(-0.03), Metrics 0.85→0.90 (+0.05), with the EB regression concentrated
in `over_1m` deals and `champion_not_eb`-tagged rows.

Determinism: every random choice goes through `RNG = random.Random(SEED)`.
Re-running this script produces bit-identical output. The seed is the
date the fixture was first generated; bump it only if the underlying
spec changes.

Run from the skill root:

    .venv/bin/python tests/fixtures/three_five_scenario/build_fixture.py

Outputs are committed alongside the script so tests don't need to
regenerate before running.
"""
from __future__ import annotations

import json
import random
import shutil
from collections import Counter
from pathlib import Path
from typing import Any


SEED = 20260514
OUT_DIR = Path(__file__).resolve().parent
GOLDEN_PATH = OUT_DIR / "golden-set.jsonl"
EXTRACTIONS_DIR = OUT_DIR / "extractions"
CURRENT_DIR = EXTRACTIONS_DIR / "current_model_v2"
CANDIDATE_DIR = EXTRACTIONS_DIR / "candidate_model_v3"


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------

DEAL_SIZE_DISTRIBUTION = (
    [("under_250k", 40), ("250k_to_1m", 35), ("over_1m", 25)]
)
STAGE_WEIGHTS = {"discovery": 35, "technical_validation": 35, "negotiation": 20, "other": 10}
CALL_TYPE_WEIGHTS = {"discovery": 30, "demo": 25, "deep_dive": 25, "procurement": 15, "other": 5}

FIELD_LABEL_TARGETS = {
    "economic_buyer": 100,
    "metrics": 80,
    "decision_criteria": 75,
    "decision_process": 60,
    "identify_pain": 90,
    "champion": 85,
    "competition": 70,
}

EB_TAG_DISTRIBUTION = [
    ("clear_eb_stated", 35),         # gold = non-null
    ("no_eb_extractable", 20),       # gold = null
    ("champion_not_eb", 15),         # gold = null (the $1.2M failure mode)
    ("title_alone_insufficient", 12),# gold = null
    ("named_but_absent_cfo", 10),    # gold = non-null
    ("multi_party_signoff", 8),      # gold = non-null
]
EB_NULL_TAGS = {"no_eb_extractable", "champion_not_eb", "title_alone_insufficient"}

# Realistic placeholder values, sampled by field. None is a valid choice (abstention).
VALUE_POOLS: dict[str, list[str | None]] = {
    "economic_buyer": [
        "Sarah Chen, CFO", "Michael Torres, VP Finance", "Priya Singh, CFO",
        "David Park, COO", "Alex Reyes, VP Operations",
    ],
    "metrics": [
        "Reduce close time from 8 to 3 days",
        "Cut reconciliation effort by 40%",
        "Improve forecast accuracy by 15 percentage points",
        "Save 12 hours per close cycle",
    ],
    "decision_criteria": [
        "Total cost of ownership and integration effort",
        "SOC2 compliance and uptime SLA",
        "API completeness for downstream systems",
        "Time-to-value within one quarter",
    ],
    "decision_process": [
        "Security review then procurement then CFO sign-off",
        "POC then technical evaluation then business case to steering committee",
        "Pilot with one team then expand based on metrics",
    ],
    "identify_pain": [
        "Manual reconciliation taking 12 hours per close",
        "Forecast accuracy off by 25%+ each quarter",
        "Sales reps spending 6 hours/week on data entry",
        "Reporting delays of 3-5 days for board materials",
    ],
    "champion": [
        "Jordan Lee, Director of Sales Ops",
        "Casey Rivera, Senior Manager Revenue Operations",
        "Sam Patel, VP Sales",
    ],
    "competition": [
        "Doing nothing / status quo workflow",
        "Salesforce Einstein",
        "Clari",
        "In-house spreadsheet tooling",
    ],
}

NULL_TAG_BY_FIELD = {
    "metrics": ("rep_claimed_metric", "prospect_quantified_outcome"),
    "identify_pain": ("rep_asserted_pain_unaffirmed", "prospect_affirmed_pain"),
    "competition": (None, "vendor_competitor_named"),  # null tag for "not discussed"
    "decision_criteria": (None, None),
    "decision_process": ("implied_decision_process", "ordered_decision_process"),
    "champion": (None, None),
}


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------

def build() -> None:
    rng = random.Random(SEED)

    transcripts = _build_transcripts()
    golden = _build_golden_set(rng, transcripts)
    perfect = _build_perfect_extractions(golden, transcripts)

    current = _degrade_to_current(rng, perfect, transcripts, golden)
    candidate = _perturb_to_candidate(rng, current, golden, transcripts)

    _write_outputs(golden, current, candidate)
    _print_stats(golden, current, candidate)


# ---- Transcripts and golden set ----

def _build_transcripts() -> list[dict[str, Any]]:
    """T001-T100 with segments fixed by the spec's distribution."""
    rng = random.Random(SEED)  # separate RNG so segment assignment is stable
    transcripts: list[dict[str, Any]] = []

    deal_sizes: list[str] = []
    for band, count in DEAL_SIZE_DISTRIBUTION:
        deal_sizes.extend([band] * count)
    rng.shuffle(deal_sizes)

    stages = list(STAGE_WEIGHTS.keys())
    stage_weights = list(STAGE_WEIGHTS.values())
    call_types = list(CALL_TYPE_WEIGHTS.keys())
    call_weights = list(CALL_TYPE_WEIGHTS.values())

    for i, band in enumerate(deal_sizes, start=1):
        transcripts.append({
            "transcript_id": f"T{i:03d}",
            "segment": {
                "deal_size_band": band,
                "stage": rng.choices(stages, weights=stage_weights)[0],
                "call_type": rng.choices(call_types, weights=call_weights)[0],
            },
        })
    return transcripts


def _build_golden_set(rng: random.Random, transcripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # Stratify EB tags across deal_size_band so every segment sees the full
    # mix of failure modes (otherwise the random alignment can put all
    # null-gold tags into a single segment and degenerate the breakdown).
    eb_tag_by_tid = _stratify_eb_tags(rng, transcripts)
    for t in transcripts:
        tag = eb_tag_by_tid[t["transcript_id"]]
        gold_null = tag in EB_NULL_TAGS
        rows.append(_make_row(rng, t, "economic_buyer", tag, gold_null))

    # Other fields: sample the labeled subset, then label each row.
    for field_name, target in FIELD_LABEL_TARGETS.items():
        if field_name == "economic_buyer":
            continue
        labeled_indices = rng.sample(range(100), target)
        null_tag, nonnull_tag = NULL_TAG_BY_FIELD.get(field_name, (None, None))
        for idx in labeled_indices:
            t = transcripts[idx]
            # 25% null on these fields (rep-asserted-but-not-affirmed pattern, etc.)
            gold_null = rng.random() < 0.25
            tag = (null_tag if gold_null else nonnull_tag)
            rows.append(_make_row(rng, t, field_name, tag, gold_null))

    return rows


def _stratify_eb_tags(
    rng: random.Random, transcripts: list[dict[str, Any]]
) -> dict[str, str]:
    """Allocate the 100 EB tags proportionally across deal_size_band.

    Within each segment, tags are shuffled by the supplied RNG. The
    result is deterministic and ensures every segment carries the full
    spectrum of failure modes — so the segment breakdown isn't degenerate.
    """
    transcripts_by_seg: dict[str, list[dict[str, Any]]] = {}
    for t in transcripts:
        transcripts_by_seg.setdefault(t["segment"]["deal_size_band"], []).append(t)

    total = sum(c for _, c in EB_TAG_DISTRIBUTION)
    assert total == 100

    out: dict[str, str] = {}
    remaining = {tag: count for tag, count in EB_TAG_DISTRIBUTION}

    for seg, seg_transcripts in transcripts_by_seg.items():
        n_seg = len(seg_transcripts)
        # Build the tag bag for this segment proportionally.
        bag: list[str] = []
        for tag, total_count in EB_TAG_DISTRIBUTION:
            target = round(total_count * n_seg / total)
            bag.extend([tag] * min(target, remaining[tag]))
        # Fill any remainder with whatever tags are still left, in original order.
        while len(bag) < n_seg:
            for tag in [t for t, _ in EB_TAG_DISTRIBUTION]:
                left = remaining[tag] - bag.count(tag)
                if left > 0:
                    bag.append(tag)
                    if len(bag) == n_seg:
                        break
        bag = bag[:n_seg]
        rng.shuffle(bag)
        for t, tag in zip(seg_transcripts, bag):
            out[t["transcript_id"]] = tag
            remaining[tag] -= 1
    return out


def _make_row(
    rng: random.Random,
    transcript: dict[str, Any],
    field_name: str,
    tag: str | None,
    gold_null: bool,
) -> dict[str, Any]:
    if gold_null:
        return {
            "transcript_id": transcript["transcript_id"],
            "field": field_name,
            "gold_value": None,
            "gold_confidence": rng.choices(["high", "medium", "low", "none"], weights=[55, 30, 10, 5])[0],
            "gold_evidence_quote": None,
            "edge_case_tag": tag,
            "segment": dict(transcript["segment"]),
        }

    value = rng.choice([v for v in VALUE_POOLS[field_name] if v is not None])
    return {
        "transcript_id": transcript["transcript_id"],
        "field": field_name,
        "gold_value": value,
        "gold_confidence": rng.choices(["high", "medium", "low", "none"], weights=[70, 22, 5, 3])[0],
        "gold_evidence_quote": f"Prospect stated: {value!r}.",
        "edge_case_tag": tag,
        "segment": dict(transcript["segment"]),
    }


# ---- Extractions ----

def _build_perfect_extractions(
    golden: list[dict[str, Any]], transcripts: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return {transcript_id: {field: extraction_dict}} where extraction matches gold."""
    out: dict[str, dict[str, dict[str, Any]]] = {t["transcript_id"]: {} for t in transcripts}
    declared = list(FIELD_LABEL_TARGETS.keys())

    # Start by populating every field on every transcript as a null abstention.
    for tid in out:
        for f in declared:
            out[tid][f] = {
                "value": None,
                "confidence": "none",
                "evidence_quote": None,
                "abstention_reason": "Not discussed on this call.",
            }

    # Then override with the gold value wherever the gold row exists (a "perfect" extractor).
    for row in golden:
        tid = row["transcript_id"]
        f = row["field"]
        if row["gold_value"] is None:
            out[tid][f] = {
                "value": None,
                "confidence": "none",
                "evidence_quote": None,
                "abstention_reason": "Insufficient signal in this call.",
            }
        else:
            out[tid][f] = {
                "value": row["gold_value"],
                "confidence": "high",
                "evidence_quote": row["gold_evidence_quote"],
                "abstention_reason": None,
            }
    return out


# Perturbation budgets — counted to hit the spec's per-field targets.
CURRENT_FLIPS = {
    "economic_buyer":    {"mismatch": 3, "false_negative": 4, "false_positive": 2},
    "metrics":           {"mismatch": 7, "false_negative": 4, "false_positive": 1},
    "decision_criteria": {"mismatch": 3, "false_negative": 4, "false_positive": 1},
    "decision_process":  {"mismatch": 3, "false_negative": 4, "false_positive": 1},
    "identify_pain":     {"mismatch": 4, "false_negative": 4, "false_positive": 2},
    "champion":          {"mismatch": 3, "false_negative": 4, "false_positive": 2},
    "competition":       {"mismatch": 5, "false_negative": 4, "false_positive": 2},
}


def _degrade_to_current(
    rng: random.Random,
    perfect: dict[str, dict[str, dict[str, Any]]],
    transcripts: list[dict[str, Any]],
    golden: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    current = _deepcopy(perfect)
    golden_by_pair = {(g["transcript_id"], g["field"]): g for g in golden}

    for field_name, budget in CURRENT_FLIPS.items():
        labeled_pairs = [pair for pair in golden_by_pair if pair[1] == field_name]
        rng.shuffle(labeled_pairs)

        # Categorize labeled rows by gold null vs non-null.
        nonnull_pairs = [p for p in labeled_pairs if golden_by_pair[p]["gold_value"] is not None]
        null_pairs = [p for p in labeled_pairs if golden_by_pair[p]["gold_value"] is None]

        # Flip mismatches: take from correct non-null rows.
        for _ in range(budget["mismatch"]):
            if not nonnull_pairs:
                break
            tid, f = nonnull_pairs.pop()
            wrong = _pick_wrong_value(rng, field_name, golden_by_pair[(tid, f)]["gold_value"])
            current[tid][f] = {
                "value": wrong, "confidence": "high",
                "evidence_quote": f"Prospect stated: {wrong!r}.", "abstention_reason": None,
            }

        # Flip false negatives: take from correct non-null rows.
        for _ in range(budget["false_negative"]):
            if not nonnull_pairs:
                break
            tid, f = nonnull_pairs.pop()
            current[tid][f] = {
                "value": None, "confidence": "none",
                "evidence_quote": None, "abstention_reason": "Missed by extractor.",
            }

        # Flip false positives: take from correct null abstentions.
        for _ in range(budget["false_positive"]):
            if not null_pairs:
                break
            tid, f = null_pairs.pop()
            wrong = _pick_wrong_value(rng, field_name, None)
            current[tid][f] = {
                "value": wrong, "confidence": "high",
                "evidence_quote": f"Prospect stated: {wrong!r}.", "abstention_reason": None,
            }

    return current


def _perturb_to_candidate(
    rng: random.Random,
    current: dict[str, dict[str, dict[str, Any]]],
    golden: list[dict[str, Any]],
    transcripts: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Start from current and apply the candidate-specific perturbations.

    Targets:
      - EB: 3 additional wrong predictions, concentrated in over_1m segment
        AND champion_not_eb tag (so segment and edge-case deltas surface).
      - Metrics: fix 5 wrong predictions back to correct.
      - Other fields: small noise (each gets ±1 row flipped or fixed).
    """
    candidate = _deepcopy(current)
    golden_by_pair = {(g["transcript_id"], g["field"]): g for g in golden}
    seg_by_tid = {t["transcript_id"]: t["segment"] for t in transcripts}

    # ---- EB regression ----
    eb_rows = [g for g in golden if g["field"] == "economic_buyer"]

    # Pool 1: champion_not_eb in over_1m (gold null, currently correct null)
    pool_champion = [
        g for g in eb_rows
        if g["edge_case_tag"] == "champion_not_eb"
        and seg_by_tid[g["transcript_id"]]["deal_size_band"] == "over_1m"
        and candidate[g["transcript_id"]]["economic_buyer"]["value"] is None  # still correct
    ]

    # Pool 2: over_1m non-null EBs (clear or named_but_absent or multi)
    pool_over_1m_nonnull = [
        g for g in eb_rows
        if g["gold_value"] is not None
        and seg_by_tid[g["transcript_id"]]["deal_size_band"] == "over_1m"
        and candidate[g["transcript_id"]]["economic_buyer"]["value"] == g["gold_value"]  # still correct
    ]

    rng.shuffle(pool_champion)
    rng.shuffle(pool_over_1m_nonnull)

    # Flip 1 of pool 1 (champion_not_eb in over_1m): set sys to a wrong non-null EB.
    # The audit log surfaces this as the failure-mode regression.
    for g in pool_champion[:1]:
        tid = g["transcript_id"]
        wrong = _pick_wrong_value(rng, "economic_buyer", None)
        candidate[tid]["economic_buyer"] = {
            "value": wrong, "confidence": "high",
            "evidence_quote": f"Prospect stated: {wrong!r}.", "abstention_reason": None,
        }

    # Flip 1 of pool 2 (over_1m non-null): mismatch with a wrong value.
    for g in pool_over_1m_nonnull[:1]:
        tid = g["transcript_id"]
        wrong = _pick_wrong_value(rng, "economic_buyer", g["gold_value"])
        candidate[tid]["economic_buyer"] = {
            "value": wrong, "confidence": "high",
            "evidence_quote": f"Prospect stated: {wrong!r}.", "abstention_reason": None,
        }

    # Plus one champion_not_eb flip OUTSIDE over_1m so the failure mode is
    # diagnosable beyond a single segment — the audit log catches it via the tag.
    pool_champion_other = [
        g for g in eb_rows
        if g["edge_case_tag"] == "champion_not_eb"
        and seg_by_tid[g["transcript_id"]]["deal_size_band"] != "over_1m"
        and candidate[g["transcript_id"]]["economic_buyer"]["value"] is None
    ]
    rng.shuffle(pool_champion_other)
    for g in pool_champion_other[:1]:
        tid = g["transcript_id"]
        wrong = _pick_wrong_value(rng, "economic_buyer", None)
        candidate[tid]["economic_buyer"] = {
            "value": wrong, "confidence": "high",
            "evidence_quote": f"Prospect stated: {wrong!r}.", "abstention_reason": None,
        }

    # ---- Metrics improvement: fix 5 currently-wrong metrics rows. ----
    metrics_wrong = [
        g for g in golden if g["field"] == "metrics"
        and g["gold_value"] is not None
        and candidate[g["transcript_id"]]["metrics"]["value"] != g["gold_value"]
    ]
    rng.shuffle(metrics_wrong)
    for g in metrics_wrong[:5]:
        tid = g["transcript_id"]
        candidate[tid]["metrics"] = {
            "value": g["gold_value"], "confidence": "high",
            "evidence_quote": g["gold_evidence_quote"], "abstention_reason": None,
        }

    return candidate


# ---- Utilities ----

def _pick_wrong_value(rng: random.Random, field_name: str, gold_value: str | None) -> str:
    pool = [v for v in VALUE_POOLS[field_name] if v != gold_value]
    return rng.choice(pool)


def _deepcopy(d):  # noqa: ANN001, ANN201
    return json.loads(json.dumps(d))


def _write_outputs(
    golden: list[dict[str, Any]],
    current: dict[str, dict[str, dict[str, Any]]],
    candidate: dict[str, dict[str, dict[str, Any]]],
) -> None:
    if EXTRACTIONS_DIR.exists():
        shutil.rmtree(EXTRACTIONS_DIR)
    CURRENT_DIR.mkdir(parents=True)
    CANDIDATE_DIR.mkdir(parents=True)

    # Golden set: write rows in a stable order so the file is reproducible.
    golden_sorted = sorted(golden, key=lambda r: (r["transcript_id"], r["field"]))
    with GOLDEN_PATH.open("w") as f:
        for row in golden_sorted:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    for tid, fields in current.items():
        (CURRENT_DIR / f"{tid}.json").write_text(json.dumps(fields, indent=2, sort_keys=True) + "\n")
    for tid, fields in candidate.items():
        (CANDIDATE_DIR / f"{tid}.json").write_text(json.dumps(fields, indent=2, sort_keys=True) + "\n")


def _print_stats(
    golden: list[dict[str, Any]],
    current: dict[str, dict[str, dict[str, Any]]],
    candidate: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Print per-field precision_high and recall for both systems, plus segment
    and edge-case breakdowns for EB."""
    golden_by_pair = {(g["transcript_id"], g["field"]): g for g in golden}

    def metrics_for(system: dict[str, dict[str, dict[str, Any]]], field_name: str,
                    subset: list[tuple[str, str]] | None = None) -> tuple[float, float, int]:
        pairs = subset or [p for p in golden_by_pair if p[1] == field_name]
        tp = fp = fn = 0
        high_tp = high_fp = 0
        for (tid, f) in pairs:
            g = golden_by_pair[(tid, f)]
            sys_extract = system[tid][f]
            sv, sc, gv = sys_extract["value"], sys_extract["confidence"], g["gold_value"]
            is_high = sc == "high"
            if gv is None and sv is None:
                pass
            elif gv is None and sv is not None:
                fp += 1
                if is_high: high_fp += 1
            elif gv is not None and sv is None:
                fn += 1
            elif gv == sv:
                tp += 1
                if is_high: high_tp += 1
            else:
                fp += 1
                fn += 1
                if is_high: high_fp += 1
        prec_high = high_tp / (high_tp + high_fp) if (high_tp + high_fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        return prec_high, recall, tp + fp + fn

    print("\n=== Per-field precision@high / recall ===")
    print(f"{'field':22} {'current_ph':>10} {'cand_ph':>10} {'delta_ph':>10} {'current_r':>10} {'cand_r':>10}")
    for field_name in FIELD_LABEL_TARGETS:
        cur_p, cur_r, _ = metrics_for(current, field_name)
        cand_p, cand_r, _ = metrics_for(candidate, field_name)
        print(f"{field_name:22} {cur_p:>10.3f} {cand_p:>10.3f} {cand_p - cur_p:>+10.3f} {cur_r:>10.3f} {cand_r:>10.3f}")

    print("\n=== EB by deal_size_band ===")
    seg_by_tid = {tid: candidate[tid] for tid in candidate}  # placeholder, will resolve from golden
    by_seg_pairs: dict[str, list[tuple[str, str]]] = {"under_250k": [], "250k_to_1m": [], "over_1m": []}
    for g in golden:
        if g["field"] == "economic_buyer":
            by_seg_pairs[g["segment"]["deal_size_band"]].append((g["transcript_id"], g["field"]))
    print(f"{'segment':14} {'current_ph':>10} {'cand_ph':>10} {'delta_ph':>10}")
    for seg, pairs in by_seg_pairs.items():
        cur_p, _, _ = metrics_for(current, "economic_buyer", pairs)
        cand_p, _, _ = metrics_for(candidate, "economic_buyer", pairs)
        print(f"{seg:14} {cur_p:>10.3f} {cand_p:>10.3f} {cand_p - cur_p:>+10.3f}")

    print("\n=== EB by edge_case_tag ===")
    by_tag_pairs: dict[str, list[tuple[str, str]]] = {}
    for g in golden:
        if g["field"] == "economic_buyer" and g["edge_case_tag"]:
            by_tag_pairs.setdefault(g["edge_case_tag"], []).append((g["transcript_id"], g["field"]))
    print(f"{'tag':30} {'current_ph':>10} {'cand_ph':>10} {'delta_ph':>10}")
    for tag, pairs in by_tag_pairs.items():
        cur_p, _, _ = metrics_for(current, "economic_buyer", pairs)
        cand_p, _, _ = metrics_for(candidate, "economic_buyer", pairs)
        print(f"{tag:30} {cur_p:>10.3f} {cand_p:>10.3f} {cand_p - cur_p:>+10.3f}")


if __name__ == "__main__":
    build()
