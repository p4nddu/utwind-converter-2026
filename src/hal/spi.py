from dataclasses import dataclass
import threading
import time

import spidev
from .gpio import rpiGpio


class spiError():
    """spi related errors during init / use"""

@dataclass(frozen=True)
class spiConfig:
    bus: int = 0
    mode: int = 0
    max_speed_hz: int = 1000000
    bits_per_word: int = 8

class spi:
    def __init__(self, cfg: spiConfig = spiConfig(), gpio: rpiGpio | None = None):
        pass

    def init(self) -> None:
        pass

    def deinit(self) -> None:
        pass

    def transfer_mcp(self, tx) -> bytes:
        pass

    
