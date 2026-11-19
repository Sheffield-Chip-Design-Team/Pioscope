from piolab.decoders import decode_can
from piolab.decoders.can import crc15

from .wavegen import can_frame_bits, can_levels, from_levels

BIT = 20  # cycles per bit: 1 Mbit/s at 20 MHz
HZ = 20e6


def _decode(frames: list[list[int]]):  # type: ignore[no-untyped-def]
    return decode_can(from_levels(HZ, l=can_levels(frames, BIT)), "l", 1_000_000)


def test_crc15_check_value() -> None:
    # CRC-15/CAN catalogue check value over ASCII "123456789"
    bits = [(c >> (7 - i)) & 1 for c in b"123456789" for i in range(8)]
    assert crc15(bits) == 0x059E


def test_standard_data_frame() -> None:
    (f,) = _decode([can_frame_bits(0x123, bytes([0xDE, 0xAD]))])
    assert (f.id, f.ext, f.rtr, f.dlc, f.data) == (0x123, False, False, 2, bytes([0xDE, 0xAD]))
    assert f.crc_ok and f.acked and f.error is None


def test_extended_and_remote_frames() -> None:
    a, b = _decode([can_frame_bits(0x1ABCDE01, b"\x11", ext=True),
                    can_frame_bits(0x7FF, rtr=True, dlc=4)])
    assert (a.id, a.ext, a.data) == (0x1ABCDE01, True, b"\x11")
    assert (b.id, b.rtr, b.dlc, b.data) == (0x7FF, True, 4, b"")
    assert a.crc_ok and b.crc_ok


def test_stuff_bits_are_removed() -> None:
    (f,) = _decode([can_frame_bits(0x000, bytes(8))])  # long runs of zeros
    assert f.data == bytes(8) and f.crc_ok and f.stuff_bits > 10


def test_crc_error_and_missing_ack() -> None:
    (f,) = _decode([can_frame_bits(0x55, b"\x01", crc_xor=1, ack=False)])
    assert not f.crc_ok and not f.acked and f.error == "crc"


def test_stuff_error() -> None:
    bits = can_frame_bits(0x000, b"")
    bits[5] = 0  # SOF + 4 ID zeros = 5 equal bits; the stuff bit (1) is forced to 0
    (f,) = _decode([bits + [1] * 20])
    assert f.error == "stuff"
