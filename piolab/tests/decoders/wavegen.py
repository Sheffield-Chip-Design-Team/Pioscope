"""Hand-built waveforms for decoder self-tests (no bench, no devices)."""

from piolab.wave import Waveform


def from_levels(sysclk_hz: float, **nets: list[int]) -> Waveform:
    wf = Waveform(sysclk_hz)
    n = max(len(v) for v in nets.values())
    for c in range(n):
        wf.record(c, {k: v[min(c, len(v) - 1)] for k, v in nets.items()})
    return wf


def uart_levels(bytes_: list[int], bit: int, parity: str = "none", stop: int = 1,
                lead: int = 20, gap: int = 0) -> list[int]:
    out = [1] * lead
    for b in bytes_:
        bits = [0] + [(b >> i) & 1 for i in range(8)]
        if parity != "none":
            p = bin(b).count("1") & 1
            bits.append(p if parity == "even" else p ^ 1)
        bits += [1] * stop
        for v in bits:
            out += [v] * bit
        out += [1] * gap
    return out + [1] * (bit * 2)


# ---- CAN (a third, test-only encoder: shares nothing with the device or decoder) ----

def _can_crc15(bits: list[int]) -> int:
    reg = 0
    for b in bits:
        top = (reg >> 14) & 1
        reg = (reg << 1) & 0x7FFF
        if b ^ top:
            reg ^= 0x4599
    return reg


def can_frame_bits(ident: int, data: bytes = b"", ext: bool = False, rtr: bool = False,
                   dlc: int | None = None, crc_xor: int = 0, ack: bool = True) -> list[int]:
    """The on-wire bits of one CAN 2.0 frame, SOF to the end of EOF (stuffed)."""
    dlc = len(data) if dlc is None else dlc
    hdr = [0]
    if ext:
        hdr += [(ident >> (28 - i)) & 1 for i in range(11)] + [1, 1]
        hdr += [(ident >> (17 - i)) & 1 for i in range(18)] + [int(rtr), 0, 0]
    else:
        hdr += [(ident >> (10 - i)) & 1 for i in range(11)] + [int(rtr), 0, 0]
    hdr += [(dlc >> (3 - i)) & 1 for i in range(4)]
    if not rtr:
        for byte in data[: min(dlc, 8)]:
            hdr += [(byte >> (7 - i)) & 1 for i in range(8)]
    crc = _can_crc15(hdr) ^ crc_xor
    body = hdr + [(crc >> (14 - i)) & 1 for i in range(15)]
    out: list[int] = []
    run, last = 0, -1
    for b in body:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            out.append(1 - b)
            run, last = 1, 1 - b
    return out + [1, 0 if ack else 1, 1] + [1] * 7


def can_levels(frames: list[list[int]], bit: int, lead_bits: int = 12,
               gap_bits: int = 3) -> list[int]:
    out = [1] * (lead_bits * bit)
    for f in frames:
        for b in f:
            out += [b] * bit
        out += [1] * (gap_bits * bit)
    return out + [1] * (bit * 2)
