from dataclasses import dataclass
import threading
import time

try:
    import spidev
except ImportError:
    spidev = None

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

        self._spi_mcp = None
        self._spi_ina_in = None
        self._spi_ina_out = None

    def init(self) -> None:
        if spidev is None:
            raise spiError("spidev not available, run on raspi and make sure it is installed")
        
        if self._opened:
            return
        # open spidev, maybe ill separate this to two different instances
        # use the members abdullah wrote above

        spi_ce0 = spidev.SpiDev()

        spi_ce0.open(self.config.bus, 0) # ce0 is device 0
        spi_ce0.max_speed_hz = self.config.max_speed_hz
        spi_ce0.mode = self.config.mode
        spi_ce0.bits_per_word = self.config.bits_per_word

        self._spi_mcp = spi_ce0

        self._opened = True

    def deinit(self) -> None:
        if not self._opened:
            return
        # close everything (or maybe just specific channels?)

        
        self._opened = False

    def transfer_mcp(self, tx) -> bytes:
        # uses ce0 as channel select

    # ------- consistency checks -------
    def _require_init()

