"""Independent waveform decoders.

Rule: these modules import only `piolab.wave` (and the standard library). They
must not share code with devices, the bench or any engine model, so that a
bug in one cannot hide a bug in the other (piolab/tests/decoders/test_independence.py).
"""

from .can import CanDecodedFrame, decode_can
from .i2c import I2cEvent, decode_i2c
from .spi import SpiWord, decode_spi
from .uart import UartFrame, decode_uart

__all__ = [
    "CanDecodedFrame",
    "I2cEvent",
    "SpiWord",
    "UartFrame",
    "decode_can",
    "decode_i2c",
    "decode_spi",
    "decode_uart",
]
