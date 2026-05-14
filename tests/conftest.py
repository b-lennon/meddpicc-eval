"""Shared pytest fixtures and path constants for the meddpicc-eval test suite."""
from __future__ import annotations

from pathlib import Path

import pytest

# Directory roots used across the suite.
SKILL_ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
SCHEMAS = SKILL_ROOT / "schemas"


@pytest.fixture
def valid_golden_set_path() -> Path:
    return FIXTURES / "valid" / "golden-set.jsonl"


@pytest.fixture
def valid_extractions_dir() -> Path:
    return FIXTURES / "valid" / "extractions"


@pytest.fixture
def valid_thresholds_path() -> Path:
    return FIXTURES / "valid" / "thresholds.yaml"


@pytest.fixture
def schemas_dir() -> Path:
    return SCHEMAS
