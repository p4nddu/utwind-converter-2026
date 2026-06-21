from dataclasses import dataclass
import threading
import time

try:
    import spidev
except ImportError:
    spidev = None

from .gpio import PiGpio


class SpiError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpiConfig:
    """
    SPI configuration for shared SPI bus.

    All devices use manual CS through gpio.py:
    - INA229_in:  GPIO 23
    - INA229_out: GPIO 24
    - MCP3208:    GPIO 25

    SPI pins:
    - MOSI: GPIO 10
    - MISO: GPIO 9
    - SCLK: GPIO 11
    """

    bus: int = 0
    device: int = 0

    max_speed_hz: int = 1_000_000
    bits_per_word: int = 8

    mode_ina229: int = 1
    mode_mcp3208: int = 0


class PiSpi:
    def __init__(self, config: SpiConfig = SpiConfig(), gpio: PiGpio | None = None):
        self.config = config
        self.gpio = gpio
        self.spi = None
        self._opened = False
        self._lock = threading.Lock()

    def init(self) -> None:
        if self._opened:
            return
        
        if spidev is None:
            raise SpiError("spidev library not found. install and enable spi")
        
        if self.gpio is None:
            raise SpiError("PiGpio instance not found")
        
        self.spi = spidev.SpiDev()
        self.spi.open(self.config.bus, self.config.device)

        self.spi.no_cs = True
        self.spi.max_speed_hz = self.config.max_speed_hz
        self.spi.bits_per_word = self.config.bits_per_word

        self._opened = True

    def deinit(self) -> None:
        if not self._opened:
            return
        
        try:
            if self.spi is not None:
                self.spi.close()
        finally:
            self.spi = None
            self._opened = False


    # ------------- helper functions --------------    

    def _require_init(self) -> None:
        if not self._opened or self.spi is None:
            raise SpiError("spi not initialized")
    
    @staticmethod
    def _require_bytes(tx: bytes | bytearray) -> None:
        if not isinstance(tx, (bytes, bytearray)):
            raise SpiError("spi transfer requires bytes or bytearray")
        
        if len(tx) == 0:
            raise SpiError("spi transfer requires at least one byte")
    
    def _transfer_manual(
            self,
            device_name: str,
            tx: bytes | bytearray,
            mode: int,
            cs_setup_s: float = 1e-6,
            cs_hold_s: float = 1e-6,
    ) -> bytes:
        self._require_init()
        self._require_bytes(tx)

        if self.gpio is None:
            raise SpiError("gpio not initialized")
        
        tx_list = list(tx)

        with self._lock:
            self.spi.mode = mode
            self.gpio.cs_pull(device_name)

            try:
                if cs_setup_s > 0:
                    time.sleep(cs_setup_s)
                
                rx_list = self.spi.xfer2(tx_list)

                if cs_hold_s > 0:
                    time.sleep(cs_hold_s)

            finally:
                self.gpio.cs_release(device_name)
            
        return bytes(rx_list)


    # -------------- public functions --------------

    def transfer_ina_in(self, tx:bytes | bytearray) -> bytes:
        return self._transfer_manual(
            device_name="ina_in",
            tx=tx,
            mode=self.config.mode_ina229,
        )
    
    def transfer_ina_out(self, tx:bytes | bytearray) -> bytes:
        return self._transfer_manual(
            device_name="ina_out",
            tx=tx,
            mode=self.config.mode_ina229,
        )
    
    def transfer_mcp3208(self, tx:bytes | bytearray) -> bytes:
        return self._transfer_manual(
            device_name="mcp3208",
            tx=tx,
            mode=self.config.mode_mcp3208,
        )
    

