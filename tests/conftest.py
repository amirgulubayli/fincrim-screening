from pathlib import Path

import pytest

from screening.loaders import load_dataset

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


@pytest.fixture(scope="session")
def mini():
    return load_dataset(FIXTURE)
