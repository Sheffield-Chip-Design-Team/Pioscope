"""Hand-built waveforms for decoder self-tests (no bench, no devices)."""

from sim.wave import Waveform


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
