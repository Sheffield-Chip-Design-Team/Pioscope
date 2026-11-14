"""PIO programs against the device models and the independent decoders (CLAUDE.md §6).
They run on the PIO model (`--dut auto`, the default)."""

import functools

import pytest

from pioscope.checks import (
    I2C_STANDARD,
    expect_sequence,
    i2c_timing,
    no_conflicts,
    spi_timing,
    uart_bit_period,
)
from pioscope.decoders import decode_i2c, decode_spi, decode_uart
from pioscope.devices import UartSend

from .scenarios import BAUD, i2c_write, spi_master, uart_rx, uart_tx

PAYLOAD = [0x55, 0x00, 0xFF, 0x3C]
WORDS = [0x96, 0x3C, 0x00, 0xFF]
RESP = [0xA5, 0x5A, 0xFF, 0x00]


@pytest.mark.requires_engine
def test_uart_tx_8n1(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(uart_tx)
    no_conflicts(r)
    frames = decode_uart(r.waveform, "tx", BAUD)
    expect_sequence("decoded", PAYLOAD, [f.value for f in frames])
    expect_sequence("partner", PAYLOAD, [t.data["value"] for t in r.txns("partner", "rx")])
    uart_bit_period(r, frames, BAUD, tol_ppm=20_000)


@pytest.mark.requires_engine
@pytest.mark.parametrize("ppm", [-20_000, 0, 20_000])
def test_uart_rx_8n1_with_baud_error(bench, ppm: int) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(uart_rx, ppm=ppm))
    expect_sequence("host rx", PAYLOAD, [w >> 24 for w in r.host_rx[0]])


@pytest.mark.requires_engine
def test_uart_rx_framing_error_raises_irq_and_is_not_pushed(bench) -> None:  # type: ignore[no-untyped-def]
    sends = [UartSend(0x11), UartSend(0x22, framing_error=True), UartSend(0x33, gap_bits=2)]
    r = bench(functools.partial(uart_rx, sends=sends))
    # The aborted frame's bits stay in ISR below the next byte, as on an RP2040; the
    # byte is bits 31..24.
    expect_sequence("host rx", [0x11, 0x33], [w >> 24 for w in r.host_rx[0]])
    assert [t.data for t in r.txns("sm0", "irq")] == [{"op": "set", "n": 4}]


@pytest.mark.requires_engine
@pytest.mark.parametrize("mode", range(4))
def test_spi_master_modes(bench, mode: int) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(spi_master, mode=mode))
    no_conflicts(r)
    expect_sequence("host rx", RESP, r.host_rx[0])
    words = decode_spi(r.waveform, "sck", "mosi", "miso", None, mode=mode)
    expect_sequence("decoded mosi", WORDS, [w.mosi for w in words])
    spi_timing(r, words)


@pytest.mark.requires_engine
def test_i2c_write_ack(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(i2c_write)
    no_conflicts(r)
    assert [t.data["byte"] for t in r.txns("slave", "write")] == [0xA7]
    expect_sequence("acks", [0, 0], r.host_rx[0])
    i2c_timing(r, decode_i2c(r.waveform, "scl", "sda"), I2C_STANDARD)


@pytest.mark.requires_engine
def test_i2c_write_nack(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(i2c_write, nack=True))
    assert [t.data["ack"] for t in r.txns("slave", "write")] == [False]
    expect_sequence("acks", [0, 1], r.host_rx[0])


@pytest.mark.requires_engine
def test_i2c_clock_stretching(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(i2c_write, data=(0xA7, 0x18), stretch=2000))
    assert [t.data["byte"] for t in r.txns("slave", "write")] == [0xA7, 0x18]
    i2c_timing(r, decode_i2c(r.waveform, "scl", "sda"), I2C_STANDARD)
