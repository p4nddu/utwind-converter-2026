from dataclasses import dataclass

# INA229 REGISTERS

REG_CONFIG = 0x00
REG_ADC_CONFIG = 0x01
REG_SHUNT_CAL = 0x02
REG_CURRENT = 0x07
REG_MANUFACTURER_ID = 0x3E
REG_DEVICE_ID = 0x3F


@dataclass(frozen=True)
class PinConfig:
    pwm_bcm: int = 12
    pwm_bcm2: int = 13
    
    gd_enable_bcm: int = 5
    gb_enable_bcm2: int = 6

    cs_ina_in_bcm: int = 25
    cs_ina_out_bcm: int = 17
    cs_adc_bcm: int = 27


@dataclass(frozen=True)
class PwmConfig:
    frequency_hz: int = 10_000
    duty_fraction: float = 0.50
    ramp_time_s: float = 2.0
    ramp_steps: int = 100

    min_duty: float = 0
    max_duty: float = 0.95 


@dataclass(frozen=True)
class SpiConfig:
    bus: int = 0
    device: int = 0
    max_speed_hz: int = 1_000_000
    mode: int = 1
    read_period_s: float = 0.2


@dataclass(frozen=True)
class SensorConfig:
    vref: float = 3.3
    div_inv: float = 12.5
    vin_channel: int = 0
    vout_channel: int = 1

    rshunt_ohms: float = 0.01
    max_expected_current_a: float = 11
    use_low_shunt_range: bool = False


class SimpleTestError(RuntimeError):
    pass


def sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit