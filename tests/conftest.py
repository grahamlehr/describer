import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def departures_payload() -> dict:
    return load_fixture("pad_departures.json")


@pytest.fixture
def arrivals_payload() -> dict:
    return load_fixture("rdg_arrivals.json")
