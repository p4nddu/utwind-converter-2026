from __future__ import annotations

from pwm_control import PwmController
from snesor_read import SensorReader

import signal
import time

import pigpio
import spidev

from config import (
    PinConfig,
    PwmConfig,
    SpiConfig,
    SensorConfig,
    SimpleTestError,
    sign_extend,
    REG_CONFIG,
    REG_ADC_CONFIG,
    REG_SHUNT_CAL,
    REG_CURRENT,
    REG_MANUFACTURER_ID,
    REG_DEVICE_ID,
)


class SimplePwmSensorTest:
    def __init__(
        self,
        pins: PinConfig = PinConfig(),
        pwm_cfg: PwmConfig = PwmConfig(),
        spi_cfg: SpiConfig = SpiConfig(),
        sensor_cfg: SensorConfig = SensorConfig(),
    ):
        self.pins = pins
        self.pwm_cfg = pwm_cfg
        self.spi_cfg = spi_cfg
        self.sensor_cfg = sensor_cfg

        self.pi: pigpio.pi | None = None
        self.spi: spidev.SpiDev | None = None

        self.pwm: PwmController | None = None
        self.sensor_reader: SensorReader | None = None

        self.running = True
        self.current_lsb: float | None = None
        self.shunt_cal: int | None = None

    # INIT AND CLEANUP

    def setup(self) -> None:
        self.setup_gpio_and_pwm()
        self.setup_spi()
        self.print_ina_identities()
        self.reset_ina_devices()
        self.configure_ina_devices()

        self.pwm = PwmController(
            self.pi,
            self.pins,
            self.pwm_cfg,
        )

        self.sensor_reader = SensorReader(self)

    def setup_gpio_and_pwm(self) -> None:
        print("[GPIO] Connecting to pigpio...")
        self.pi = pigpio.pi()

        if not self.pi.connected:
            raise SimpleTestError(
                "pigpio daemon not running. Start it with:\n"
                "  sudo systemctl start pigpiod"
            )

        for pin in [
            self.pins.cs_ina_in_bcm,
            self.pins.cs_ina_out_bcm,
            self.pins.cs_adc_bcm,
        ]:
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 1)

        self.pi.set_mode(self.pins.gd_enable_bcm, pigpio.OUTPUT)
        self.pi.write(self.pins.gd_enable_bcm, 0)

        self.pi.set_mode(self.pins.gd_enable_bcm2, pigpio.OUTPUT)
        self.pi.write(self.pins.gd_enable_bcm2, 0)

        self.pi.set_mode(self.pins.pwm_bcm, pigpio.OUTPUT)
        self.pi.set_mode(self.pins.pwm_bcm2, pigpio.OUTPUT)


    def setup_spi(self) -> None:
        print("[SPI] Opening SPI...")
        self.spi = spidev.SpiDev()
        self.spi.open(self.spi_cfg.bus, self.spi_cfg.device)
        self.spi.no_cs = True
        self.spi.mode = self.spi_cfg.mode
        self.spi.max_speed_hz = self.spi_cfg.max_speed_hz

    def cleanup(self) -> None:
        print("\n[Cleanup] Disabling PWM and gate driver...")

        try:
            if self.pwm is not None:
                self.pwm.set_pwm_duty(0.0)
                self.pwm.set_pwm_duty2(0.0)

                self.pwm.set_gd_enable(False)
                self.pwm.set_gd_enable2(False)

                self.pi.write(self.pins.cs_ina_in_bcm, 1)
                self.pi.write(self.pins.cs_ina_out_bcm, 1)
                self.pi.write(self.pins.cs_adc_bcm, 1)
        except Exception:
            pass

        try:
            if self.spi is not None:
                self.spi.close()
        except Exception:
            pass

        try:
            if self.pi is not None:
                self.pi.stop()
        except Exception:
            pass

        self.spi = None
        self.pi = None

    # SPI

    def release_all_cs(self) -> None:
        self.pi.write(self.pins.cs_ina_in_bcm, 1)
        self.pi.write(self.pins.cs_ina_out_bcm, 1)
        self.pi.write(self.pins.cs_adc_bcm, 1)

    def cs_low(self, device: str) -> None:
        self.release_all_cs()

        if device == "ina_in":
            self.pi.write(self.pins.cs_ina_in_bcm, 0)
        elif device == "ina_out":
            self.pi.write(self.pins.cs_ina_out_bcm, 0)
        elif device == "adc":
            self.pi.write(self.pins.cs_adc_bcm, 0)
        else:
            raise ValueError(f"Unknown SPI device: {device}")

    def cs_high(self, device: str) -> None:
        if device == "ina_in":
            self.pi.write(self.pins.cs_ina_in_bcm, 1)
        elif device == "ina_out":
            self.pi.write(self.pins.cs_ina_out_bcm, 1)
        elif device == "adc":
            self.pi.write(self.pins.cs_adc_bcm, 1)
        else:
            raise ValueError(f"Unknown SPI device: {device}")

    def transfer(self, tx: list[int], device: str) -> list[int]:
        self.cs_low(device)
        try:
            return self.spi.xfer2(tx)
        finally:
            self.cs_high(device)

    # MCP3208

    def read_adc_raw(self, channel: int) -> int:
        cmd = 0x06 | ((channel & 0x04) >> 2)
        msb = (channel & 0x03) << 6

        rx = self.transfer([cmd, msb, 0x00], "adc")
        return ((rx[1] & 0x0F) << 8) | rx[2]

    def read_adc_voltage(self, channel: int) -> float:
        return (self.read_adc_raw(channel) / 4095.0) * self.sensor_cfg.vref

    def read_scaled_voltage(self, channel: int) -> float:
        return self.read_adc_voltage(channel) * self.sensor_cfg.div_inv

    # INA229

    def read_reg(self, reg_addr: int, num_bytes: int, device: str) -> int:
        cmd = ((reg_addr & 0x3F) << 2) | 0x01
        rx = self.transfer([cmd] + [0x00] * num_bytes, device)

        data = 0
        for b in rx[1:]:
            data = (data << 8) | b

        return data

    def write_reg(self, reg_addr: int, value: int, num_bytes: int, device: str) -> None:
        cmd = ((reg_addr & 0x3F) << 2)

        tx = [cmd]
        for shift in range(8 * (num_bytes - 1), -1, -8):
            tx.append((value >> shift) & 0xFF)

        self.transfer(tx, device)

    def reset_ina_devices(self) -> None:
        print("[INA229] Resetting devices...")
        for device in ["ina_in", "ina_out"]:
            self.write_reg(REG_CONFIG, 0x8000, 2, device)
        time.sleep(0.01)

    def compute_current_lsb_and_cal(self):
        current_lsb = self.sensor_cfg.max_expected_current_a / (2 ** 19)
        shunt_cal = 13107.2e6 * current_lsb * self.sensor_cfg.rshunt_ohms

        if self.sensor_cfg.use_low_shunt_range:
            shunt_cal *= 4

        return current_lsb, int(round(shunt_cal)) & 0x7FFF

    def configure_ina_devices(self) -> None:
        print("[INA229] Configuring devices...")
        self.current_lsb, self.shunt_cal = self.compute_current_lsb_and_cal()

        config = 0x0010 if self.sensor_cfg.use_low_shunt_range else 0x0000

        adc_config = (
            (0xA << 12)
            | (0x5 << 9)
            | (0x5 << 6)
            | (0x5 << 3)
            | 0x2
        )

        for device in ["ina_in", "ina_out"]:
            self.write_reg(REG_CONFIG, config, 2, device)
            self.write_reg(REG_ADC_CONFIG, adc_config, 2, device)
            self.write_reg(REG_SHUNT_CAL, self.shunt_cal, 2, device)

    def read_current(self, device: str) -> float:
        raw24 = self.read_reg(REG_CURRENT, 3, device)
        raw20 = (raw24 >> 4) & 0xFFFFF
        signed = sign_extend(raw20, 20)
        return signed * self.current_lsb

    def read_ina_identity(self, device: str):
        return (
            self.read_reg(REG_MANUFACTURER_ID, 2, device),
            self.read_reg(REG_DEVICE_ID, 2, device),
        )

    def print_ina_identities(self) -> None:
        print("[INA229] Reading manufacturer/device IDs...")

        for device in ["ina_in", "ina_out"]:
            manufacturer_id, device_id = self.read_ina_identity(device)

            manufacturer_ok = "OK" if manufacturer_id == 0x5449 else "CHECK"
            device_ok = "OK" if device_id == 0x2290 else "CHECK"

            print(
                f"  {device}: "
                f"MANUFACTURER_ID=0x{manufacturer_id:04X} [{manufacturer_ok}] | "
                f"DEVICE_ID=0x{device_id:04X} [{device_ok}]"
            )

    # MAIN

    def request_stop(self, *_args) -> None:
        self.running = False

    def run_pwm(self) -> None:
        self.pwm.run()

    def run_sensor_read(self, step: int) -> None:
        target_duty = max(
            0.0,
            min(1.0, self.pwm_cfg.duty_fraction),
        )

        expected_vob = 1.0 - target_duty

        current_in = self.read_current("ina_in")
        current_out = self.read_current("ina_out")

        vin = self.read_scaled_voltage(
            self.sensor_cfg.vin_channel
        )
        vout = self.read_scaled_voltage(
            self.sensor_cfg.vout_channel
        )

        print(
            f"[{step:05d}] "
            f"D_VOA={target_duty * 100:5.1f}% | "
            f"D_VOB={expected_vob * 100:5.1f}% | "
            f"I_IN={current_in:+.6f} A | "
            f"I_OUT={current_out:+.6f} A | "
            f"Vin={vin:.3f} V | "
            f"Vout={vout:.3f} V"
        )

        return 


    def run(self) -> None:
        self.run_sensor_read(0)


    def calculate_power(self, voltage_out, current_out) -> float:

        power = voltage_out * current_out

        return power

    def run_po_loop(
        self,
        previous_power: float,
        previous_voltage: float,
        vref: float,
        direction: float,   # +1 or -1
        step_size: float = 0.2,
    ) -> tuple[float, float, float, float]:
        """
        MPPT-style PO loop:
        adjusts VREF directly to maximize power.
        """

        current_voltage = self.read_scaled_voltage(self.sensor_cfg.vout_channel)
        current = self.read_current("ina_out")

        current_power = self.calculate_power(current_voltage, current)

        #check if increase or decrease power/voltage
        delta_p = current_power - previous_power
        delta_v = current_voltage - previous_voltage

        # --- PO decision (hill climbing on power curve) ---
        if delta_p > 0:
            # keep going same direction
            if delta_v > 0:
                vref += step_size * direction
            else:
                vref -= step_size * direction
        else:
            # reverse direction
            direction *= -1
            if delta_v > 0:
                vref += step_size * direction
            else:
                vref -= step_size * direction

        # clamp (important for safety)
        vref = max(0.0, min(60.0, vref))

        return vref, current_power, current_voltage, direction

    
    def pi_voltage_control(
        self,
        vout: float,
        vref: float,
        dt: float,
    ) -> float:

        # initialize if not existing
        if not hasattr(self, "pi_integral"):
            self.pi_integral = 0.0

        error = vref - vout
        self.pi_integral += error * dt

        # PI gains (YOU WILL TUNE THESE)
        KP = 0.01
        KI = 0.005

        u = KP * error + KI * self.pi_integral

        MAX_PWM = 41.25 / 100.0

        pwm = max(0.0, min(MAX_PWM, u))

        return pwm
