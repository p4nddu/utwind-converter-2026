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

@dataclass(frozen=True)
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
    def __init__(self, config: spiConfig = spiConfig(), gpio: rpiGpio | None = None):
        self.config = config
        self.gpio = gpio
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
        spi_ce1 = spidev.SpiDev()
        spi_manual = spidev.SpiDev()

        spi_ce0.open(self.config.bus, 0) # ce0 is device 0
        spi_ce0.max_speed_hz = self.config.max_speed_hz
        spi_ce0.mode = self.config.mode
        spi_ce0.bits_per_word = self.config.bits_per_word

        spi_ce1.open(self.config.bus, 1)
        spi_ce1.max_speed_hz = self.config.max_speed_hz
        spi_ce1.mode = self.config.mode
        spi_ce1.bits_per_word = self.config.bits_per_word

        spi_manual.open(self.config.bus, 1)
        spi_manual.no_cs = True # sets the device to use a manual gpio cs
        spi_manual.max_speed_hz = self.config.max_speed_hz
        spi_manual.mode = self.config.mode
        spi_manual.bits_per_word = self.config.bits_per_word

        self._spi_mcp = spi_ce0
        self._spi_ina_out = spi_ce1
        self._spi_ina_in = spi_manual

        self._opened = True

    def deinit(self) -> None:
        if not self._opened:
            return
        # close everything (or maybe just specific channels?)
        try:
            for s in (self._spi_ina_in, self._spi_ina_out, self._spi_mcp):
                if s is not None:
                    s.close()
        finally:
            self._spi_mcp = None
            self._spi_ina_in = None
            self._spi_ina_out = None
        
            self._opened = False

    # ------- helper functions -------

    def _require_init(self) -> None:
        if not self._opened:
            raise spiError("spi not initialized. call rpiSpi.init() first")
    
    @staticmethod
    def _require_bytes(tx) -> None:
        if not isinstance(tx, (bytes, bytearray)):
            raise spiError("pass bytes or bytearray for spi transfers")
        if len(tx) == 0:
            raise spiError("must transfer at least 1 byte")

    # ------- spi Transfers -------

    def transfer_mcp(self, tx: bytes | bytearray) -> bytes:
        # uses ce0 as channel select
        self._require_init()
        self._require_bytes(tx)
            
        tx_list = list(tx)

        with self._lock:
            rx_list = self._spi_mcp.xfer2(tx_list)
        return bytes(rx_list)
    
    def transfer_ina_out(self, tx: bytes | bytearray) -> bytes:
        self._require_init()
        self._require_bytes(tx)
            
        tx_list = list(tx)

        with self._lock:
            rx_list = self._spi_ina_out.xfer2(tx_list)
        return bytes(rx_list)
    
    def transfer_ina_in(self, tx: bytes | bytearray, cs_setup: float = 1e-6, cs_hold: float = 1e-6) -> bytes:
        self._require_init()
        self._require_bytes(tx)

        if self.gpio is None:
            raise spiError("input current sensor requires gpio to be set up")
        
        tx_list = list(tx)

        with self._lock:
            self.gpio.cs_pull("ina_in")
            if cs_setup > 0:
                time.sleep(cs_setup)
            
            rx_list = self._spi_ina_in.xfer2(tx_list)

            if cs_hold > 0:
                time.sleep(cs_hold)
            self.gpio.cs_release("ina_in")
            
        return bytes(rx_list)

