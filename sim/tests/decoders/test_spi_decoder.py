import pytest

from sim.decoders import decode_spi

from .wavegen import from_levels


def spi_levels(word: int, mode: int, half: int = 4) -> dict[str, list[int]]:
    cpol, cpha = mode >> 1, mode & 1
    sck, mosi, cs = [cpol] * 6, [0] * 6, [1] * 2 + [0] * 4
    for i in range(8):
        b = (word >> (7 - i)) & 1
        if cpha == 0:
            sck += [cpol] * half + [1 - cpol] * half
            mosi += [b] * (2 * half)
        else:
            sck += [1 - cpol] * half + [cpol] * half
            mosi += [b] * (2 * half)
        cs += [0] * (2 * half)
    tail = 6
    return {"sck": sck + [cpol] * tail, "mosi": mosi + [0] * tail, "cs": cs + [1] * tail}


@pytest.mark.parametrize("mode", range(4))
def test_all_modes(mode: int) -> None:
    lv = spi_levels(0xA6, mode)
    wf = from_levels(1e6, sck=lv["sck"], mosi=lv["mosi"], cs=lv["cs"], miso=lv["mosi"])
    words = decode_spi(wf, "sck", "mosi", "miso", "cs", mode=mode)
    assert [(w.mosi, w.miso) for w in words] == [(0xA6, 0xA6)]
    assert words[0].min_setup is not None and words[0].min_setup >= 1


def test_lsb_first_and_no_cs() -> None:
    lv = spi_levels(0x01, 0)
    wf = from_levels(1e6, sck=lv["sck"], mosi=lv["mosi"])
    words = decode_spi(wf, "sck", "mosi", None, None, mode=0, msb_first=False)
    assert words[0].mosi == 0x80


def test_deselect_drops_partial_word() -> None:
    lv = spi_levels(0xFF, 0)
    cs = lv["cs"][:20] + [1] * 4 + lv["cs"][24:]
    wf = from_levels(1e6, sck=lv["sck"], mosi=lv["mosi"], cs=cs)
    assert decode_spi(wf, "sck", "mosi", None, "cs", mode=0) == []
