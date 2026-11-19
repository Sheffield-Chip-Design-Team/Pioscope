"""PIO scenarios: the PIO model is the master/transmitter; device models play the
other end. Host words follow the Pico SDK conventions of each program (left-justified
for MSB-first shifts, bits 31..24 for UART RX)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

from piolab.asm import assemble_file
from piolab.bench import Attach, HostScript, Scenario, Wiring
from piolab.devices import I2cSlave, SpiSlave, UartConfig, UartPartner, UartSend
from piolab.image import ProgramImage
from piolab.packs import get

BAUD = 115_200
SYSCLK = 50e6


@cache
def program(name: str) -> ProgramImage:
    return assemble_file(get("pio").program(name))


def uart_tx(payload: Sequence[int] = (0x55, 0x00, 0xFF, 0x3C)) -> Scenario:
    image = program("uart_tx")
    partner = UartPartner("partner", UartConfig(BAUD))
    return Scenario(
        name="uart_tx[pio]", image=image, wiring=Wiring.from_image(image),
        devices=[Attach(partner, {"rx": "tx"})],
        host=HostScript(tx={0: list(payload)}),
        max_cycles=int((len(payload) + 2) * 10 * SYSCLK / BAUD),
        until=lambda s: len([t for t in s.devices["partner"].log if t.kind == "rx"])
        >= len(payload),
    )


def uart_rx(payload: Sequence[int] = (0x55, 0x00, 0xFF, 0x3C), ppm: float = 0.0,
            sends: Sequence[UartSend] | None = None, offset_s: float = 0.0,
            glitch_prob: float = 0.0, seed: int = 0) -> Scenario:
    image = program("uart_rx")
    items = list(sends) if sends is not None else list(payload)
    partner = UartPartner("partner", UartConfig(BAUD, ppm=ppm, offset_s=offset_s), send=items,
                          glitch_prob=glitch_prob, glitch_cycles=20, seed=seed)
    return Scenario(
        name="uart_rx[pio]", image=image, wiring=Wiring.from_image(image, pulls={"rx": 1}),
        devices=[Attach(partner, {"tx": "rx"})],
        max_cycles=int((len(items) + 3) * 11 * SYSCLK / BAUD), seed=seed,
    )


def spi_master(mode: int = 0, words: Sequence[int] = (0x96, 0x3C, 0x00, 0xFF),
               responses: Sequence[int] = (0xA5, 0x5A, 0xFF, 0x00)) -> Scenario:
    image = program(f"spi_m{mode}")
    slave = SpiSlave("slave", mode=mode, responses=responses, use_cs=False)
    return Scenario(
        name=f"spi_master[pio m{mode}]", image=image,
        wiring=Wiring.from_image(image, pulls={"miso": 1}),
        devices=[Attach(slave, {"sck": "sck", "mosi": "mosi", "miso": "miso"})],
        host=HostScript(tx={0: [w << 24 for w in words]}),
        max_cycles=20_000,
        until=lambda s: len(s.host_rx.get(0, [])) >= len(words)
        and len([t for t in s.devices["slave"].log if t.kind == "word"]) >= len(words),
    )


def i2c_write(data: Sequence[int] = (0xA7,), address: int = 0x42, nack: bool = False,
              stretch: int = 0) -> Scenario:
    image = program("i2c_write")
    slave = I2cSlave("slave", address, nack_after=0 if nack else None, stretch_cycles=stretch)
    words = [address << 1, *data]
    return Scenario(
        name="i2c_write[pio]", image=image, wiring=Wiring.from_image(image),
        devices=[Attach(slave, {"scl": "scl", "sda": "sda"})],
        host=HostScript(tx={0: [w << 24 for w in words]}),
        max_cycles=60_000 * (len(words) + 1),
        until=lambda s: len(s.host_rx.get(0, [])) >= len(words) and s.cycle > 0,
    )
