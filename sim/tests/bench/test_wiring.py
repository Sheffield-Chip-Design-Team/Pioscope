import pytest

from sim.asm import assemble_file
from sim.bench import NetSpec, Scenario, Wiring
from sim.packs import get


def test_from_image_uses_roles_and_pulls_open_drain() -> None:
    im = assemble_file(get("pio").program("i2c_write"))
    w = Wiring.from_image(im)
    assert w.nets == {"sda": NetSpec("uio0", 1), "scl": NetSpec("uio1", 1)}
    assert w.check_against(im) == []


def test_mismatched_board_is_reported() -> None:
    im = assemble_file(get("pio").program("i2c_write"))
    w = Wiring({"sda": NetSpec("uio2", 1), "scl": NetSpec("uio1")})
    problems = w.check_against(im)
    assert any("program uses uio0, board wires uio2" in p for p in problems)
    assert any("no pull-up" in p for p in problems)
    with pytest.raises(ValueError, match="scenario x"):
        Scenario("x", w, [], 10, image=im)
