import pytest

from piolab.bench.nets import resolve
from piolab.wave import X, Z

CASES = [
    ([], None, X),
    ([], 1, 1),
    ([], 0, 0),
    ([Z, Z], 1, 1),
    ([0], 1, 0),
    ([1], None, 1),
    ([0, Z], 1, 0),  # open-drain: one device pulls low
    ([0, 0], 1, 0),  # wired-AND
    ([1, 1], None, 1),
    ([X], 1, X),
    ([X, 0], 1, X),
]


@pytest.mark.parametrize("drives, pull, level", CASES)
def test_resolution(drives: list[int], pull: int | None, level: int) -> None:
    assert resolve([(f"d{i}", d) for i, d in enumerate(drives)], pull) == (level, False)


def test_contention_is_reported() -> None:
    assert resolve([("a", 0), ("b", 1)], 1) == (X, True)
