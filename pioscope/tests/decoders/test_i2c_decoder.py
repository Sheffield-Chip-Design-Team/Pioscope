from pioscope.decoders import decode_i2c

from .wavegen import from_levels


def i2c_levels(bytes_and_acks: list[tuple[int, bool]], h: int = 4) -> dict[str, list[int]]:
    scl, sda = [1] * 8, [1] * 8
    # START
    scl += [1] * h
    sda += [0] * h
    for byte, ack in bytes_and_acks:
        bits = [(byte >> (7 - i)) & 1 for i in range(8)] + [0 if ack else 1]
        for b in bits:
            scl += [0] * h + [1] * h
            sda += [b] * (2 * h)
    # STOP
    scl += [0] * h + [1] * (2 * h)
    sda += [0] * (h + h) + [1] * h
    return {"scl": scl + [1] * 8, "sda": sda + [1] * 8}


def test_address_data_ack_nack_and_stop() -> None:
    lv = i2c_levels([((0x42 << 1) | 0, True), (0x10, True), (0x20, False)])
    ev = decode_i2c(from_levels(1e6, **lv), "scl", "sda")
    kinds = [(e.kind, e.value, e.ack, e.rw) for e in ev]
    assert kinds == [("start", None, None, None), ("addr", 0x42, True, 0),
                     ("data", 0x10, True, None), ("data", 0x20, False, None),
                     ("stop", None, None, None)]
    assert all(t == 4 for t in ev[2].t_high)
