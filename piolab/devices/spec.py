"""Device specs for the command line: `--device uart:baud=115200,send=0x55,0x41`.

A spec is a device name, optionally followed by `:` and comma-separated `key=value`
options. A bare value after a list option extends that list (`send=0x55,0x41`). Integers
take any base (`0x42`). Defaults come from `[tool.piolab.devices]` in the project's
pyproject.toml, then the options of the spec.

    uart (uart_partner)  baud  ppm  send  bits  parity  stop
    spi  (spi_slave)     mode
    i2c  (i2c_slave)     addr  stretch

The same specs are accepted in VS Code launch configurations (`"devices": [...]`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from piolab.bench import Attach
from piolab.image import ProgramImage

from .i2c import I2cSlave
from .spi import SpiSlave
from .uart import UartConfig, UartPartner


class DeviceSpecError(ValueError):
    pass


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else int(str(v), 0)


def _float(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) else float(str(v))


def _str(v: Any) -> str:
    return str(v)


@dataclass(frozen=True)
class Option:
    kind: Callable[[Any], Any]
    default: Any
    help: str
    is_list: bool = False


@dataclass(frozen=True)
class DeviceKind:
    name: str
    aliases: tuple[str, ...]
    options: Mapping[str, Option]
    build: Callable[[ProgramImage, dict[str, Any]], Attach]
    help: str


def _roles(image: ProgramImage) -> set[str]:
    return set(image.config.get("pins", {}))


def _uart(image: ProgramImage, o: dict[str, Any]) -> Attach:
    tim = (image.config.get("tim") or {}).get("default", {})
    baud = o["baud"] or tim.get("rate")
    if not baud:
        raise DeviceSpecError("uart: the program does not give a baud rate; use "
                              "--device uart:baud=N (VS Code: \"devices\": [\"uart:baud=N\"])")
    roles = _roles(image)
    ports = {}
    if "tx" in roles:
        ports["rx"] = "tx"
    if "rx" in roles:
        ports["tx"] = "rx"
    cfg = UartConfig(baud, data_bits=o["bits"], parity=o["parity"], stop_bits=o["stop"],
                     ppm=o["ppm"])
    return Attach(UartPartner("uart_partner", cfg, send=list(o["send"])), ports)


def _spi(image: ProgramImage, o: dict[str, Any]) -> Attach:
    roles = _roles(image)
    dev = SpiSlave("spi_slave", mode=o["mode"], use_cs="cs" in roles)
    return Attach(dev, {p: p for p in dev.ports if p in roles})


def _i2c(image: ProgramImage, o: dict[str, Any]) -> Attach:
    return Attach(I2cSlave("i2c_slave", o["addr"], stretch_cycles=o["stretch"]),
                  {"scl": "scl", "sda": "sda"})


DEVICES: dict[str, DeviceKind] = {k.name: k for k in (
    DeviceKind("uart", ("uart_partner",), {
        "baud": Option(_float, None, "bit rate (default: the program's)"),
        "ppm": Option(_float, 0.0, "partner clock error"),
        "send": Option(_int, (), "bytes the partner transmits", is_list=True),
        "bits": Option(_int, 8, "data bits"),
        "parity": Option(_str, "none", "none | even | odd"),
        "stop": Option(_float, 1, "stop bits: 1, 1.5 or 2"),
    }, _uart, "UART partner on the program's tx/rx pins"),
    DeviceKind("spi", ("spi_slave",), {
        "mode": Option(_int, 0, "SPI mode 0-3"),
    }, _spi, "SPI slave on the program's sck/mosi/miso/cs pins"),
    DeviceKind("i2c", ("i2c_slave",), {
        "addr": Option(_int, 0x42, "7-bit address"),
        "stretch": Option(_int, 0, "clock-stretch cycles"),
    }, _i2c, "I2C slave on the program's scl/sda pins"),
)}


def kind(name: str) -> DeviceKind:
    for k in DEVICES.values():
        if name == k.name or name in k.aliases:
            return k
    names = ", ".join(f"{k.name} ({'/'.join(k.aliases)})" for k in DEVICES.values())
    raise DeviceSpecError(f"unknown device '{name}' (devices: {names})")


def _convert(k: DeviceKind, key: str, value: Any) -> Any:
    if key not in k.options:
        raise DeviceSpecError(f"device '{k.name}' has no option '{key}' "
                              f"(options: {', '.join(k.options)})")
    opt = k.options[key]
    try:
        if opt.is_list:
            items = value if isinstance(value, (list, tuple)) else [value]
            return tuple(opt.kind(v) for v in items)
        return opt.kind(value)
    except (TypeError, ValueError) as e:
        raise DeviceSpecError(f"{k.name}:{key}={value!r}: {e}") from None


def parse(spec: str, defaults: Mapping[str, Mapping[str, Any]] | None = None
          ) -> tuple[DeviceKind, dict[str, Any]]:
    """`uart:baud=9600,send=0x55,0x41` -> (the uart kind, its full option dict)."""
    name, _, rest = spec.strip().partition(":")
    k = kind(name.strip())
    opts = {key: o.default for key, o in k.options.items()}
    for dname, d in (defaults or {}).items():
        if dname == k.name or dname in k.aliases:
            for key, v in d.items():
                opts[key] = _convert(k, key, v)
    given: dict[str, list[str]] = {}
    last: str | None = None
    for tok in (t.strip() for t in rest.split(",") if t.strip()):
        key, eq, val = tok.partition("=")
        if eq:
            last = key.strip()
            given[last] = [val.strip()]
        elif last is not None and k.options.get(last, Option(_str, None, "")).is_list:
            given[last].append(tok)
        else:
            raise DeviceSpecError(f"'{tok}' in --device {spec}: expected key=value "
                                  f"(options: {', '.join(k.options)})")
    for key, vals in given.items():
        opt = k.options.get(key)
        opts[key] = _convert(k, key, vals if opt is not None and opt.is_list else vals[0])
    return k, opts


def build(specs: Sequence[str], image: ProgramImage,
          defaults: Mapping[str, Mapping[str, Any]] | None = None) -> list[Attach]:
    out = []
    for s in specs:
        k, opts = parse(s, defaults)
        out.append(k.build(image, opts))
    return out


def describe() -> str:
    """One line per device and its options (for --help)."""
    return "; ".join(f"{k.name}:{','.join(f'{o}=' for o in k.options)}"
                     for k in DEVICES.values())


__all__ = ["DEVICES", "DeviceKind", "DeviceSpecError", "Option", "build", "describe", "kind",
           "parse"]
