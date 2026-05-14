"""Tests for the output-emission layer (scripts/emit.py).

Three artifacts:
  - scorecard.md (human-readable, sales-leader-readable)
  - audit-log.jsonl (per-failure diagnostic)
  - verdict.json (machine-readable, CI-gateable)

Tests verify structural properties — section headers, row contents,
schema validity — rather than exact byte-equality (which would be
brittle across formatting tweaks).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.emit import emit_all


SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


def _row(match, **overrides):
    base = {
        "transcript_id": "t1",
        "field": "economic_buyer",
        "system": "current_model_v2",
        "match": match,
        "gold_value": "X" if match in ("match_exact", "match_semantic", "false_negative", "mismatch") else None,
        "sys_value": "X" if match in ("match_exact", "match_semantic", "false_positive", "mismatch") else None,
        "gold_confidence": "high",
        "sys_confidence": "high",
        "gold_evidence_quote": None,
        "sys_evidence_quote": None,
        "edge_case_tag": None,
        "segment": {"deal_size_band": "over_1m", "stage": "negotiation", "call_type": "deep_dive"},
        "calibration_kind": "well_calibrated_high",
        "evidence_faithfulness": "unverifiable",
        "contract_warning": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def minimal_inputs(tmp_path):
    """A minimal set of pipeline outputs for emission testing.

    Two systems (current + candidate), one field (economic_buyer), a
    couple of correct rows and one wrong candidate row.
    """
    grades = [
        _row("match_exact", system="current_model_v2"),
        _row("match_exact", system="current_model_v2"),
        _row("match_exact", system="candidate_model_v3", transcript_id="t2"),
        _row("mismatch", system="candidate_model_v3", transcript_id="t3", edge_case_tag="champion_not_eb"),
    ]

    metrics = {
        "systems": ["current_model_v2", "candidate_model_v3"],
        "fields": ["economic_buyer"],
        "per_system": {
            "current_model_v2": {
                "by_field": {
                    "economic_buyer": {
                        "field": "economic_buyer", "n_total": 2,
                        "precision": 1.0, "recall": 1.0, "f1": 1.0,
                        "abstention_rate": 0.0, "precision_high": 1.0, "n_high_confidence": 2,
                    },
                },
                "by_field_by_segment": {"economic_buyer": {"deal_size_band": {}, "stage": {}, "call_type": {}}},
                "by_field_by_edge_case": {"economic_buyer": {}},
            },
            "candidate_model_v3": {
                "by_field": {
                    "economic_buyer": {
                        "field": "economic_buyer", "n_total": 2,
                        "precision": 0.5, "recall": 0.5, "f1": 0.5,
                        "abstention_rate": 0.0, "precision_high": 0.5, "n_high_confidence": 2,
                    },
                },
                "by_field_by_segment": {"economic_buyer": {"deal_size_band": {}, "stage": {}, "call_type": {}}},
                "by_field_by_edge_case": {
                    "economic_buyer": {
                        "champion_not_eb": {
                            "field": "economic_buyer", "n_total": 1,
                            "precision": 0.0, "recall": 0.0, "f1": 0.0,
                            "abstention_rate": 0.0, "precision_high": 0.0, "n_high_confidence": 1,
                        },
                    },
                },
            },
        },
    }
    threshold_results = {
        "threshold_results": {
            "current_model_v2": {
                "economic_buyer": {"field": "economic_buyer", "overall": "pass", "failed_checks": []},
            },
            "candidate_model_v3": {
                "economic_buyer": {"field": "economic_buyer", "overall": "fail", "failed_checks": ["precision_high"]},
            },
        },
        "regressions": {
            "candidate_model_v3": {
                "economic_buyer": {
                    "field": "economic_buyer", "status": "regression",
                    "current_precision_high": 1.0, "candidate_precision_high": 0.5,
                    "delta_precision_high": -0.5, "tolerance": 0.0,
                },
            },
        },
    }
    verdict_raw = {
        "verdict": "hold",
        "reason": "Load-bearing field(s) ['economic_buyer'] regress beyond tolerance in the candidate system. Do not migrate.",
        "failing_fields": ["economic_buyer"],
        "passing_fields": [],
    }
    return {
        "grades": grades,
        "metrics": metrics,
        "threshold_results": threshold_results,
        "verdict_raw": verdict_raw,
    }


# ---------------------------------------------------------------------------
# scorecard.md (Task 16)
# ---------------------------------------------------------------------------

class TestScorecard:
    def test_scorecard_contains_verdict_block(self, tmp_path, minimal_inputs):
        out = tmp_path / "output"
        emit_all(out, **minimal_inputs)
        scorecard = (out / "scorecard.md").read_text()
        assert "Migration verdict" in scorecard or "## Verdict" in scorecard
        assert "HOLD" in scorecard.upper()

    def test_scorecard_contains_per_field_table(self, tmp_path, minimal_inputs):
        out = tmp_path / "output"
        emit_all(out, **minimal_inputs)
        scorecard = (out / "scorecard.md").read_text()
        # Per-field table headers from the spec example.
        assert "economic_buyer" in scorecard.lower() or "Economic Buyer" in scorecard
        assert "0.50" in scorecard or "0.5" in scorecard  # candidate precision_high

    def test_scorecard_lists_failing_field_in_reason(self, tmp_path, minimal_inputs):
        out = tmp_path / "output"
        emit_all(out, **minimal_inputs)
        scorecard = (out / "scorecard.md").read_text()
        assert "economic_buyer" in scorecard.lower() and ("HOLD" in scorecard.upper() or "hold" in scorecard.lower())


# ---------------------------------------------------------------------------
# audit-log.jsonl (Task 17)
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_audit_log_one_row_per_wrong(self, tmp_path, minimal_inputs):
        out = tmp_path / "output"
        emit_all(out, **minimal_inputs)
        audit_lines = (out / "audit-log.jsonl").read_text().splitlines()
        rows = [json.loads(l) for l in audit_lines if l.strip()]
        # Only one row was wrong: the candidate mismatch.
        assert len(rows) == 1
        assert rows[0]["transcript_id"] == "t3"
        assert rows[0]["match"] == "mismatch"
        assert rows[0]["edge_case_tag"] == "champion_not_eb"

    def test_audit_log_preserves_edge_case_tag(self, tmp_path, minimal_inputs):
        out = tmp_path / "output"
        emit_all(out, **minimal_inputs)
        rows = [json.loads(l) for l in (out / "audit-log.jsonl").read_text().splitlines() if l.strip()]
        assert any(r["edge_case_tag"] == "champion_not_eb" for r in rows), \
            "Edge-case tag must propagate into the audit log for diagnosability."

    def test_audit_log_each_line_is_valid_json(self, tmp_path, minimal_inputs):
        out = tmp_path / "output"
        emit_all(out, **minimal_inputs)
        for line in (out / "audit-log.jsonl").read_text().splitlines():
            if line.strip():
                json.loads(line)  # raises if malformed


# ---------------------------------------------------------------------------
# verdict.json (Task 18)
# ---------------------------------------------------------------------------

class TestVerdictJson:
    def test_verdict_json_validates_against_schema(self, tmp_path, minimal_inputs):
        out = tmp_path / "output"
        emit_all(out, **minimal_inputs)
        verdict = json.loads((out / "verdict.json").read_text())

        schema = json.loads((SCHEMAS_DIR / "verdict.schema.json").read_text())
        errs = list(Draft202012Validator(schema).iter_errors(verdict))
        assert not errs, f"verdict.json failed schema validation: {[e.message for e in errs]}"

    def test_verdict_json_includes_run_metadata(self, tmp_path, minimal_inputs):
        out = tmp_path / "output"
        emit_all(out, **minimal_inputs)
        verdict = json.loads((out / "verdict.json").read_text())
        assert "run_metadata" in verdict
        assert "run_date" in verdict["run_metadata"]
        assert "systems_evaluated" in verdict["run_metadata"]
        assert "golden_set_size" in verdict["run_metadata"]
        assert verdict["run_metadata"]["systems_evaluated"] == ["current_model_v2", "candidate_model_v3"]