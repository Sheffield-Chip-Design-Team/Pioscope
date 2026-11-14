import itertools

import pytest

from sim.devices import DeviceClock


def test_period_offset_and_ppm() -> None:
    c = DeviceClock(1e6, ppm=1000, offset_s=5e-9)
    assert c.period_s == pytest.approx(1 / 1.001e6)
    assert c.tick(0) == 5e-9
    assert c.tick(10) == pytest.approx(5e-9 + 10 / 1.001e6)


def test_jitter_is_seeded_bounded_and_monotonic() -> None:
    a = DeviceClock(10e6, jitter_rms_s=1e-9, seed=3)
    b = DeviceClock(10e6, jitter_rms_s=1e-9, seed=3)
    c = DeviceClock(10e6, jitter_rms_s=1e-9, seed=4)
    ta = [a.tick(n) for n in range(2000)]
    assert ta == [b.tick(n) for n in range(2000)]
    assert ta != [c.tick(n) for n in range(2000)]
    assert all(x < y for x, y in itertools.pairwise(ta))
    dev = [t - n * a.period_s for n, t in enumerate(ta)]
    assert max(abs(d) for d in dev) <= 3e-9
    rms = (sum(d * d for d in dev) / len(dev)) ** 0.5
    assert 0.7e-9 < rms < 1.3e-9


def test_fractional_ticks_have_no_jitter() -> None:
    c = DeviceClock(1e6, jitter_rms_s=1e-9)
    assert c.tick(1.5) == pytest.approx(1.5e-6)


def test_rejects_jitter_that_could_reorder_edges() -> None:
    with pytest.raises(ValueError):
        DeviceClock(100e6, jitter_rms_s=2e-9)
