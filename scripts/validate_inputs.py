"""Stage 1: validate eval inputs against the contract.

Loads and validates:
  - thresholds.yaml against schemas/thresholds.schema.json
  - golden-set.jsonl line-by-line against schemas/golden-set.schema.json
  - every extraction file under extractions/{system}/{transcript_id}.json
    against schemas/extraction.schema.json

Validation is strict by design: contract violations fail loudly. Silent
acceptance of malformed input is how eval harnesses produce confidently
wrong scorecards.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    transcripts: list[str] = field(default_factory=list)
    declared_fields: list[str] = field(default_factory=list)


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / name).read_text())


def validate(
    golden_path: Path,
    extractions_dir: Path,
    thresholds_path: Path,
) -> ValidationResult:
    result = ValidationResult(ok=True)

    declared_fields = _validate_thresholds(thresholds_path, result)
    result.declared_fields = sorted(declared_fields)

    golden_rows, transcripts_in_golden = _validate_golden_set(
        golden_path, declared_fields, result
    )
    result.transcripts = sorted(transcripts_in_golden)

    _validate_extractions(
        extractions_dir, declared_fields, transcripts_in_golden, result
    )

    result.ok = not result.errors
    return result


def _validate_thresholds(thresholds_path: Path, result: ValidationResult) -> set[str]:
    if not thresholds_path.exists():
        result.errors.append(f"thresholds.yaml not found at {thresholds_path}")
        return set()

    try:
        config = yaml.safe_load(thresholds_path.read_text())
    except yaml.YAMLError as exc:
        result.errors.append(f"thresholds.yaml is not valid YAML: {exc}")
        return set()

    if not isinstance(config, dict):
        result.errors.append("thresholds.yaml must be a mapping at top level.")
        return set()

    schema = _load_schema("thresholds.schema.json")
    validator = Draft202012Validator(schema)
    for err in validator.iter_errors(config):
        result.errors.append(f"thresholds.yaml: {err.message} at /{'/'.join(map(str, err.absolute_path))}")

    return set(config.get("fields", {}).keys())


def _validate_golden_set(
    golden_path: Path,
    declared_fields: set[str],
    result: ValidationResult,
) -> tuple[list[dict[str, Any]], set[str]]:
    if not golden_path.exists():
        result.errors.append(f"golden-set.jsonl not found at {golden_path}")
        return [], set()

    raw = golden_path.read_text()
    if not raw.strip():
        result.errors.append("golden-set.jsonl is empty.")
        return [], set()

    schema = _load_schema("golden-set.schema.json")
    validator = Draft202012Validator(schema)

    rows: list[dict[str, Any]] = []
    transcripts: set[str] = set()

    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            result.errors.append(
                f"golden-set.jsonl line {lineno}: not valid JSON ({exc.msg})"
            )
            continue

        errors_for_line = list(validator.iter_errors(obj))
        for err in errors_for_line:
            result.errors.append(
                f"golden-set.jsonl line {lineno}: {err.message} at /{'/'.join(map(str, err.absolute_path))}"
            )
        if errors_for_line:
            continue

        # Cross-field contract: gold_evidence_quote must be null iff gold_value is null.
        if obj["gold_value"] is None and obj["gold_evidence_quote"] is not None:
            result.errors.append(
                f"golden-set.jsonl line {lineno}: gold_evidence_quote must be null "
                f"when gold_value is null (contract violation)."
            )
            continue

        # Field must be declared in thresholds.yaml.
        if declared_fields and obj["field"] not in declared_fields:
            result.errors.append(
                f"golden-set.jsonl line {lineno}: field '{obj['field']}' is not "
                f"declared in thresholds.yaml (declared: {sorted(declared_fields)})"
            )
            continue

        rows.append(obj)
        transcripts.add(obj["transcript_id"])

    return rows, transcripts


def _validate_extractions(
    extractions_dir: Path,
    declared_fields: set[str],
    transcripts_in_golden: set[str],
    result: ValidationResult,
) -> None:
    if not extractions_dir.exists():
        result.errors.append(f"extractions directory not found at {extractions_dir}")
        return

    schema = _load_schema("extraction.schema.json")
    validator = Draft202012Validator(schema)

    systems_found: set[str] = set()

    for system_dir in sorted(extractions_dir.iterdir()):
        if not system_dir.is_dir():
            continue
        systems_found.add(system_dir.name)

        for extraction_file in sorted(system_dir.glob("*.json")):
            transcript_id = extraction_file.stem
            location = f"extractions/{system_dir.name}/{extraction_file.name}"

            try:
                obj = json.loads(extraction_file.read_text())
            except json.JSONDecodeError as exc:
                result.errors.append(f"{location}: not valid JSON ({exc.msg})")
                continue

            errors_for_file = list(validator.iter_errors(obj))
            for err in errors_for_file:
                result.errors.append(
                    f"{location}: {err.message} at /{'/'.join(map(str, err.absolute_path))}"
                )
            if errors_for_file:
                continue

            # Check declared-field coverage: every declared field must be present.
            if declared_fields:
                missing = declared_fields - obj.keys()
                if missing:
                    result.errors.append(
                        f"{location}: missing field(s) {sorted(missing)} that are "
                        f"declared in thresholds.yaml"
                    )
                extra = obj.keys() - declared_fields
                if extra:
                    result.errors.append(
                        f"{location}: additional field(s) {sorted(extra)} not declared "
                        f"in thresholds.yaml"
                    )

            # Inner contract: value=null implies confidence=none (or vice versa is a warning).
            for fname, fextract in obj.items():
                if fextract["value"] is None and fextract["confidence"] != "none":
                    result.warnings.append(
                        f"{location} field={fname}: value is null but confidence is "
                        f"'{fextract['confidence']}' (expected 'none')."
                    )
                if fextract["value"] is not None and fextract["confidence"] == "none":
                    result.warnings.append(
                        f"{location} field={fname}: confidence is 'none' but value is "
                        f"non-null ('{fextract['value']}')."
                    )

            # Orphan-extraction check: transcript_id must appear in the golden set.
            if transcripts_in_golden and transcript_id not in transcripts_in_golden:
                result.errors.append(
                    f"{location}: orphan extraction — transcript_id '{transcript_id}' "
                    f"is not in the golden set"
                )

    result.systems = sorted(systems_found)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Validate MEDDPICC eval inputs.")
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--extractions", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    args = parser.parse_args()

    result = validate(args.golden, args.extractions, args.thresholds)

    if result.warnings:
        print("Warnings:", file=sys.stderr)
        for w in result.warnings:
            print(f"  - {w}", file=sys.stderr)

    if not result.ok:
        print("Validation failed:", file=sys.stderr)
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        f"OK. systems={result.systems} transcripts={result.transcripts} "
        f"declared_fields={result.declared_fields}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
