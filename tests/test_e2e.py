"""End-to-end test against the deterministic 3%/5% fixture.

This is the test the rest of the suite is designed around:
  - load the fixture
  - run the full pipeline
  - assert the verdict is `hold`
  - assert the EB and Metrics deltas point the right way
  - assert the diagnostic outputs (audit log, scorecard) make the failure
    mode inspectable, not just countable.

If this test ever flips green for `ship`, the verdict logic broke and
the panel demo is dead.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.run_eval import run_full_pipeline


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "three_five_scenario"
SKILL_ROOT = Path(__file__).parent.parent
THRESHOLDS = SKILL_ROOT / "thresholds.yaml"


@pytest.fixture(scope="module")
def e2e_output(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("e2e_output")
    result = run_full_pipeline(
        golden_set=FIXTURE_DIR / "golden-set.jsonl",
        extractions_dir=FIXTURE_DIR / "extractions",
        thresholds=THRESHOLDS,
        output_dir=output_dir,
        judgments=FIXTURE_DIR / "judgments.jsonl",
        run_date="2026-05-14",
    )
    return output_dir, result


# ---------------------------------------------------------------------------
# Verdict: the headline assertion
# ---------------------------------------------------------------------------

class TestThreeFiveVerdict:
    def test_verdict_is_hold(self, e2e_output):
        output_dir, _ = e2e_output
        verdict = json.loads((output_dir / "verdict.json").read_text())
        assert verdict["verdict"] == "hold", (
            f"3%/5% scenario must produce hold. Got: {verdict['verdict']}. "
            f"Reason given: {verdict['reason']}"
        )

    def test_failing_fields_include_economic_buyer(self, e2e_output):
        output_dir, _ = e2e_output
        verdict = json.loads((output_dir / "verdict.json").read_text())
        assert "economic_buyer" in verdict["failing_fields"]

    def test_metrics_did_not_cause_the_hold(self, e2e_output):
        """Metrics improved by ~5pp — it must not appear as a failing field."""
        output_dir, _ = e2e_output
        verdict = json.loads((output_dir / "verdict.json").read_text())
        assert "metrics" not in verdict["failing_fields"]

    def test_reason_cites_load_bearing_or_economic_buyer(self, e2e_output):
        output_dir, _ = e2e_output
        verdict = json.loads((output_dir / "verdict.json").read_text())
        reason = verdict["reason"].lower()
        assert "load-bearing" in reason or "economic_buyer" in reason, (
            f"Verdict reason must cite WHY: {verdict['reason']}"
        )

    def test_verdict_json_validates_against_schema(self, e2e_output):
        output_dir, _ = e2e_output
        verdict = json.loads((output_dir / "verdict.json").read_text())
        schema = json.loads(
            (SKILL_ROOT / "schemas" / "verdict.schema.json").read_text()
        )
        errs = list(Draft202012Validator(schema).iter_errors(verdict))
        assert not errs, [e.message for e in errs]


# ---------------------------------------------------------------------------
# Direction: EB regresses, Metrics improves
# ---------------------------------------------------------------------------

class TestDeltas:
    def test_eb_candidate_below_current(self, e2e_output):
        output_dir, _ = e2e_output
        metrics = json.loads((output_dir / "metrics.json").read_text())
        cur = metrics["per_system"]["current_model_v2"]["by_field"]["economic_buyer"]["precision_high"]
        cand = metrics["per_system"]["candidate_model_v3"]["by_field"]["economic_buyer"]["precision_high"]
        assert cand < cur, f"EB candidate must regress; got cur={cur}, cand={cand}"

    def test_metrics_candidate_above_current(self, e2e_output):
        output_dir, _ = e2e_output
        metrics = json.loads((output_dir / "metrics.json").read_text())
        cur = metrics["per_system"]["current_model_v2"]["by_field"]["metrics"]["precision_high"]
        cand = metrics["per_system"]["candidate_model_v3"]["by_field"]["metrics"]["precision_high"]
        assert cand > cur, f"Metrics candidate must improve; got cur={cur}, cand={cand}"


# ---------------------------------------------------------------------------
# Determinism (a second run produces the same outputs modulo run_date)
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_pipeline_is_deterministic_on_fixture(self, tmp_path):
        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        common = dict(
            golden_set=FIXTURE_DIR / "golden-set.jsonl",
            extractions_dir=FIXTURE_DIR / "extractions",
            thresholds=THRESHOLDS,
            judgments=FIXTURE_DIR / "judgments.jsonl",
            run_date="2026-05-14",
        )
        run_full_pipeline(output_dir=out1, **common)
        run_full_pipeline(output_dir=out2, **common)
        assert (out1 / "verdict.json").read_text() == (out2 / "verdict.json").read_text()
        assert (out1 / "audit-log.jsonl").read_text() == (out2 / "audit-log.jsonl").read_text()
        assert (out1 / "scorecard.md").read_text() == (out2 / "scorecard.md").read_text()
