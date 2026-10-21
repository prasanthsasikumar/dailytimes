import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_brief.json"


@pytest.fixture
def sample_dict() -> dict:
    return json.loads(SAMPLE.read_text())


@pytest.fixture
def sample_brief(sample_dict):
    from dailytimes.blocks import Brief

    return Brief.model_validate(sample_dict)
