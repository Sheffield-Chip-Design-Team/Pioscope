from piolab.decoders import decode_uart

from .wavegen import from_levels, uart_levels


def test_decodes_bytes_and_measures_period() -> None:
    wf = from_levels(1e6, l=uart_levels([0x55, 0x00, 0xFF, 0x81], bit=10))
    frames = decode_uart(wf, "l", baud=100_000)
    assert [f.value for f in frames] == [0x55, 0x00, 0xFF, 0x81]
    assert all(f.framing_ok and f.parity_ok for f in frames)
    assert frames[0].measured_bit_cycles == 10.0


def test_parity() -> None:
    wf = from_levels(1e6, l=uart_levels([0x07, 0x03], bit=8, parity="even"))
    frames = decode_uart(wf, "l", baud=125_000, parity="even")
    assert [(f.value, f.parity_ok) for f in frames] == [(7, True), (3, True)]
    frames = decode_uart(wf, "l", baud=125_000, parity="odd")
    assert [f.parity_ok for f in frames] == [False, False]


def test_framing_error_and_break() -> None:
    lv = [1] * 10 + [0] * 80 + [0] * 8 + [1] * 40  # 11 bit-times low = break
    frames = decode_uart(from_levels(1e6, l=lv), "l", baud=125_000)
    assert frames[0].is_break and not frames[0].framing_ok


def test_glitch_is_not_a_start_bit() -> None:
    lv = [1] * 10 + [0] * 2 + [1] * 30 + uart_levels([0x3C], bit=10, lead=5)
    frames = decode_uart(from_levels(1e6, l=lv), "l", baud=100_000)
    assert [f.value for f in frames] == [0x3C]
