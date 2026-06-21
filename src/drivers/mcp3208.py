from dataclasses import dataclass
from hal.spi import PiSpi


class MCP3208Error(RuntimeError):
    pass


@dataclass(frozen=True)
class MCP3208Config:
    """
    Config for MCP3208 8-channel 12-bit SPI ADC
    - CH0 is Vin
    - CH1 is Vout

    true voltage = ADC voltage * divider ratio
    """
    vref: float = 3.3
    adc_bits: int = 12
    divider_ratio: float = 12.5

    ch_vin: int = 0
    ch_vout: int = 1


class MCP3208:
    def __init__(self, spi: PiSpi, config: MCP3208Config = MCP3208Config()):
        self.spi = spi
        self.config = config
    

    # -------------- helper functions --------------
    
    def _validate_channel(self, channel:int) -> None:
        if not isinstance(channel, int):
            raise MCP3208Error("ADC channel must be an integer")
        
        if channel < 0 or channel > 7:
            raise MCP3208Error("choose channels 0-7")


    # -------------- ADC reads --------------

    def read_raw(self, channel: int) -> int:
        """
        Reads raw ADC value (0–4095)
        """
        self._validate_channel(channel)

        tx = bytes([
            0x06 | (channel >> 2),      # Start bit + single-ended
            (channel & 0x03) << 6,
            0x00
        ])

        rx = self.spi.transfer_mcp3208(tx)

        if len(rx) != 3:
            raise MCP3208Error(f"expected 3 bytes from mcp3208, got {len(rx)}")

        # Extract 12-bit result
        raw = ((rx[1] & 0x0F) << 8) | rx[2]
        return raw

    def read_adc_voltage(self, channel: int) -> float:
        """
        Read voltage on channel
        """
        raw = self.read_raw(channel)
        return (raw / 4095.0) * self.config.vref
    
    def read_vin(self) -> float:
        adc_voltage = self.read_adc_voltage(self.config.ch_vin)
        return adc_voltage * self.config.divider_ratio
    
    def read_vout(self) -> float:
        adc_voltage = self.read_adc_voltage(self.config.ch_vout)
        return adc_voltage * self.config.divider_ratio