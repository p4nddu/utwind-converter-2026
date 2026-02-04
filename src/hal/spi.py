from dataclasses import dataclass
import threading
import time

import spidev
from .gpio import rpiGpio

class spiError(RuntimeError):
    """SPI related errors"""

class spiConfig:
    """
    SPI configuration parameters
    """
    bus: int = 0
    device: int = 0
    max_speed_hz: int = 1_000_000
    mode: int = 0
    bits_per_word: int = 8

class rpiSpi:
    def __init__(self, config: spiConfig = spiConfig()):
        self.config = config
        self._spi = None
        self._lock = threading.Lock()
        self._opened = False
