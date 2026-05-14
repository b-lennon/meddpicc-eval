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
# Diagnosability — edge-case tag and segment regressions must surface
# ---------------------------------------------------------------------------

def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestDiagnosability:
    def test_champion_not_eb_regression_appears_in_audit_log(self, e2e_output):
        """The whole point of edge-case tagging: a champion_not_eb failure must
        appear in the audit log AS champion_not_eb, not just as 'some EB miss'."""
        output_dir, _ = e2e_output
        audit = _read_jsonl(output_dir / "audit-log.jsonl")
        candidate_eb_failures = [
            r for r in audit
            if r["system"] == "candidate_model_v3"
            and r["field"] == "economic_buyer"
        ]
        tags_in_failures = {r.get("edge_case_tag") for r in candidate_eb_failures if r.get("edge_case_tag")}
        assert "champion_not_eb" in tags_in_failures, (
            f"Candidate must surface champion_not_eb failures in the audit log. "
            f"Got tags: {tags_in_failures}"
        )

    def test_every_audit_row_carries_segment_and_tag(self, e2e_output):
        """Diagnostics require both segment and edge_case_tag to be present
        on every audit row (tag may be null; segment must not be)."""
        output_dir, _ = e2e_output
        audit = _read_jsonl(output_dir / "audit-log.jsonl")
        assert audit, "audit log should be non-empty for this fixture"
        for row in audit:
            assert "segment" in row and row["segment"] is not None
            assert "edge_case_tag" in row  # may be null but key must exist

    def test_over_1m_eb_regression_in_scorecard(self, e2e_output):
        """The scorecard's EB segment breakdown must show the over_1m
        concentration of the regression."""
        output_dir, _ = e2e_output
        scorecard = (output_dir / "scorecard.md").read_text()
        assert "over_1m" in scorecard, "Segment breakdown must include over_1m"
        # Confirm the segment block exists for economic_buyer.
        assert "Segment breakdown" in scorecard
        assert "economic_buyer" in scorecard

    def test_scorecard_contains_edge_case_breakdown_for_eb(self, e2e_output):
        """Edge-case breakdown for EB must be present in the scorecard so the
        reader can see the failure-mode story."""
        output_dir, _ = e2e_output
        scorecard = (output_dir / "scorecard.md").read_text()
        assert "Edge-case breakdown" in scorecard and "economic_buyer" in scorecard

    def test_candidate_has_more_eb_failures_than_current(self, e2e_output):
        """The candidate must contribute more EB rows to the audit log than
        the current system — that's the regression made inspectable."""
        output_dir, _ = e2e_output
        audit = _read_jsonl(output_dir / "audit-log.jsonl")
        cur_eb = sum(1 for r in audit if r["system"] == "current_model_v2" and r["field"] == "economic_buyer")
        cand_eb = sum(1 for r in audit if r["system"] == "candidate_model_v3" and r["field"] == "economic_buyer")
        assert cand_eb > cur_eb, (
            f"Candidate should have more EB failures than current. "
            f"Got cur={cur_eb}, cand={cand_eb}"
        )


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
