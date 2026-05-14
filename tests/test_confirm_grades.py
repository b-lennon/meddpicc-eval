"""Tests for the semantic-match plumbing (confirm_grades.py + llm_judge.py).

confirm_grades.py merges Claude's (or an API caller's) per-row semantic
judgments into the grade file. The semantic match step is the *only*
place the LLM enters the pipeline; this test suite locks down the
contract between the deterministic grader and the LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.confirm_grades import confirm
from scripts.grade import CalibrationKind, MatchKind
from scripts.llm_judge import format_semantic_match_prompt


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _segment():
    return {"deal_size_band": "over_1m", "stage": "negotiation", "call_type": "deep_dive"}


def _tentative_row(**overrides):
    base = {
        "transcript_id": "t1",
        "field": "economic_buyer",
        "system": "system_x",
        "match": "needs_semantic_review",
        "gold_value": "Jane Smith, CFO",
        "sys_value": "Jane Smith",
        "gold_confidence": "high",
        "sys_confidence": "high",
        "gold_evidence_quote": None,
        "sys_evidence_quote": None,
        "edge_case_tag": None,
        "segment": _segment(),
        "calibration_kind": "pending",
        "evidence_faithfulness": "pending",
        "contract_warning": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# confirm_grades.py
# ---------------------------------------------------------------------------

class TestConfirmGrades:
    def test_merges_match_judgment(self, tmp_path):
        tentative_path = tmp_path / "tentative-grades.jsonl"
        judgments_path = tmp_path / "judgments.jsonl"
        output_path = tmp_path / "confirmed-grades.jsonl"

        _write_jsonl(tentative_path, [_tentative_row()])
        _write_jsonl(judgments_path, [{
            "transcript_id": "t1",
            "field": "economic_buyer",
            "system": "system_x",
            "semantic_match": "match",
        }])

        confirm(tentative_path, judgments_path, output_path)
        rows = _read_jsonl(output_path)

        assert len(rows) == 1
        assert rows[0]["match"] == "match_semantic"
        # Calibration also gets resolved: high confidence + correct match.
        assert rows[0]["calibration_kind"] == "well_calibrated_high"

    def test_merges_mismatch_judgment(self, tmp_path):
        tentative_path = tmp_path / "tentative.jsonl"
        judgments_path = tmp_path / "judgments.jsonl"
        output_path = tmp_path / "confirmed.jsonl"

        _write_jsonl(tentative_path, [_tentative_row()])
        _write_jsonl(judgments_path, [{
            "transcript_id": "t1",
            "field": "economic_buyer",
            "system": "system_x",
            "semantic_match": "mismatch",
        }])

        confirm(tentative_path, judgments_path, output_path)
        rows = _read_jsonl(output_path)

        assert rows[0]["match"] == "mismatch"
        # High confidence + wrong match -> overconfident.
        assert rows[0]["calibration_kind"] == "overconfident"

    def test_fails_loudly_on_missing_judgment(self, tmp_path):
        tentative_path = tmp_path / "tentative.jsonl"
        judgments_path = tmp_path / "judgments.jsonl"
        output_path = tmp_path / "confirmed.jsonl"

        _write_jsonl(tentative_path, [_tentative_row()])
        _write_jsonl(judgments_path, [])  # no judgment for the row

        with pytest.raises(ValueError, match="missing judgment"):
            confirm(tentative_path, judgments_path, output_path)

    def test_passes_through_non_review_rows_unchanged(self, tmp_path):
        tentative_path = tmp_path / "tentative.jsonl"
        judgments_path = tmp_path / "judgments.jsonl"
        output_path = tmp_path / "confirmed.jsonl"

        # An exact match should pass through without needing a judgment.
        row = _tentative_row(
            match="match_exact",
            gold_value="Jane Smith",
            sys_value="Jane Smith",
            calibration_kind="well_calibrated_high",
        )
        _write_jsonl(tentative_path, [row])
        _write_jsonl(judgments_path, [])

        confirm(tentative_path, judgments_path, output_path)
        rows = _read_jsonl(output_path)
        assert rows[0]["match"] == "match_exact"
        assert rows[0]["calibration_kind"] == "well_calibrated_high"

    def test_rejects_invalid_semantic_match_value(self, tmp_path):
        tentative_path = tmp_path / "tentative.jsonl"
        judgments_path = tmp_path / "judgments.jsonl"
        output_path = tmp_path / "confirmed.jsonl"

        _write_jsonl(tentative_path, [_tentative_row()])
        _write_jsonl(judgments_path, [{
            "transcript_id": "t1",
            "field": "economic_buyer",
            "system": "system_x",
            "semantic_match": "maybe",
        }])

        with pytest.raises(ValueError, match="invalid semantic_match"):
            confirm(tentative_path, judgments_path, output_path)


# ---------------------------------------------------------------------------
# llm_judge.py
# ---------------------------------------------------------------------------

class TestSemanticMatchPrompt:
    def test_prompt_contains_field_and_values(self):
        prompt = format_semantic_match_prompt(
            field="economic_buyer",
            gold_value="Jane Smith, CFO",
            sys_value="Jane Smith",
            edge_case_tag="named_but_absent_cfo",
        )
        assert "economic_buyer" in prompt
        assert "Jane Smith, CFO" in prompt
        assert "Jane Smith" in prompt

    def test_prompt_includes_decision_criteria(self):
        prompt = format_semantic_match_prompt(
            field="economic_buyer",
            gold_value="X",
            sys_value="Y",
            edge_case_tag=None,
        )
        # Output expected to be one of: match, mismatch.
        assert "match" in prompt
        assert "mismatch" in prompt

    def test_prompt_includes_edge_case_when_present(self):
        prompt = format_semantic_match_prompt(
            field="economic_buyer",
            gold_value="X",
            sys_value="Y",
            edge_case_tag="champion_not_eb",
        )
        assert "champion_not_eb" in prompt

    def test_prompt_omits_edge_case_when_absent(self):
        prompt = format_semantic_match_prompt(
            field="metrics",
            gold_value="Reduce close time from 8 to 3 days",
            sys_value="Cut closing time to 3 days from 8",
            edge_case_tag=None,
        )
        # The phrase 'edge case' should not appear when there's no tag.
        assert "edge_case" not in prompt.lower() or "edge case" not in prompt.lower()
