"""PIO pack tests: they run with the pio pack active."""

from collections.abc import Iterator

import pytest

from pioscope.packs import IsaPack, use


@pytest.fixture(autouse=True)
def pio_pack() -> Iterator[IsaPack]:
    with use("pio") as pack:
        yield pack
