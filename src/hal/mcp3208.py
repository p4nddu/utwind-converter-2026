from dataclasses import dataclass
import threading
import time

try:
    import spidev
except ImportError:
    spidev = None

class MCP3208Error(RuntimeError):
    """MCP3208 related errors"""


class MCP3208:
    """
    MCP3208 12-bit SPI ADC
    """

    def __init__(
        self,
        bus: int = 0,
        device: int = 0,          # CE0 by default
        max_speed_hz: int = 1_000_000,
        vref: float = 3.3
    ):
        self.bus = bus
        self.device = device
        self.max_speed_hz = max_speed_hz
        self.vref = vref

        self._spi = spidev.SpiDev()
        self._lock = threading.Lock()
        self._opened = False

    # ---------- lifecycle ----------

    def open(self) -> None:
        if self._opened:
            return

        self._spi.open(self.bus, self.device)
        self._spi.max_speed_hz = self.max_speed_hz
        self._spi.mode = 0                  # SPI Mode 0
        self._spi.bits_per_word = 8

        self._opened = True

    def close(self) -> None:
        if self._opened:
            self._spi.close()
            self._opened = False

    # ---------- helpers ----------

    def _require_open(self) -> None:
        if not self._opened:
            raise MCP3208Error("SPI device not open. Call open() first.")

    @staticmethod
    def _check_channel(channel: int) -> None:
        if not 0 <= channel <= 7:
            raise MCP3208Error("Channel must be 0–7")

    # ---------- ADC read ----------

    def read_raw(self, channel: int) -> int:
        """
        Read raw 12-bit ADC value (0–4095)
        """
        self._require_open()
        self._check_channel(channel)

        # MCP3208 command format
        tx = [
            0x06 | (channel >> 2),      # Start bit + single-ended
            (channel & 0x03) << 6,
            0x00
        ]

        with self._lock:
            rx = self._spi.xfer2(tx)

        # Extract 12-bit result
        value = ((rx[1] & 0x0F) << 8) | rx[2]
        return value

    def read_voltage(self, channel: int) -> float:
        """
        Read voltage on channel
        """
        raw = self.read_raw(channel)
        return (raw / 4095.0) * self.vref
