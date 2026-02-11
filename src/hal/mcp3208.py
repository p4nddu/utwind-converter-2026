from dataclasses import dataclass
from spi import rpiSpi

class MCP3208Error(RuntimeError):
    """MCP3208 related errors"""


class MCP3208:
    """
    Driver for MCP3208 8-channel 12-bit SPI ADC (single-ended mode)
    """

    def __init__(self, spi: rpiSpi = rpiSpi(), vref: float = 3.3):
        self._spi = spi
        self._spi.init()
        self.vref = vref
        
    def deinit(self) -> None:
        if self._spi._opened:
            self._spi.deinit()

    # ---------- helpers ----------
    
    @staticmethod
    def _check_channel(channel: int) -> None:
        if not 0 <= channel <= 7:
            raise MCP3208Error("Channel must be 0–7")

    # ---------- ADC read ----------

    def read_raw(self, channel: int) -> int:
        """
        Read raw 12-bit ADC value (0–4095)
        """
        self._check_channel(channel)

        # MCP3208 command format
        tx = bytes([
            0x06 | (channel >> 2),      # Start bit + single-ended
            (channel & 0x03) << 6,
            0x00
        ])
        rx = self._spi.transfer_mcp(tx)

        # Extract 12-bit result
        value = ((rx[1] & 0x0F) << 8) | rx[2]
        return value

    def read_voltage(self, channel: int) -> float:
        """
        Read voltage on channel
        """
        raw = self.read_raw(channel)
        return (raw / 4095.0) * self.vref
