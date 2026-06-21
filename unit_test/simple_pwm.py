#!/usr/bin/env python3
"""
simple_pwm_sensor_test.py

simple integration test for:
- Raspberry pi PWM output to SI8274
- GD_ENABLE control
- Read all sensors

There is no buck/boost control or MPPT.

Make sure to run:
    sudo apt install pigpio python3-pigpio python3-spidev
    sudo systemctl start pigpiod
and enable spidev

Checks:
    1. Probe pi PWM pin relative to pi GND.
    2. Probe GD_ENABLE pin relative to Pi GND.
    3. Probe VOA-to-source and VOB-to-source at the gates.
"""

from __future__ import annotations

import signal
import sys
import time
from dataclasses import dataclass

import pigpio
import spidev


# DATA CLASSES 

@dataclass(frozen=True)
class PinConfig:
    # PWM / gate driver
    pwm_bcm: int = 12          # physical pin 32
    gd_enable_bcm: int = 6     # physical pin 31

    # manual chip selects, active low
    cs_ina_in_bcm: int = 25
    cs_ina_out_bcm: int = 17
    cs_adc_bcm: int = 27


@dataclass(frozen=True)
class PwmConfig:
    frequency_hz: int = 10_000
    duty_fraction: float = 0.50
    ramp_time_s: float = 2.0
    ramp_steps: int = 100


@dataclass(frozen=True)
class SpiConfig:
    bus: int = 0
    device: int = 0
    max_speed_hz: int = 1_000_000
    mode: int = 1
    read_period_s: float = 0.2


@dataclass(frozen=True)
class SensorConfig:
    # MCP3208 / voltage divider
    vref: float = 3.3
    div_inv: float = 12.5
    vin_channel: int = 0
    vout_channel: int = 1

    # INA229
    rshunt_ohms: float = 1.5
    max_expected_current_a: float = 0.1
    use_low_shunt_range: bool = False


# INA229 REGISTERS

REG_CONFIG = 0x00
REG_ADC_CONFIG = 0x01
REG_SHUNT_CAL = 0x02
REG_CURRENT = 0x07


# HELPER FUNCTIONS DOWN HERE

class SimpleTestError(RuntimeError):
    pass


def sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit


# TEST CLASS AND CONFIGS

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

        self.running = True
        self.current_lsb: float | None = None
        self.shunt_cal: int | None = None

    # INIT AND CLEANUP

    def setup(self) -> None:
        self.setup_gpio_and_pwm()
        self.setup_spi()
        self.reset_ina_devices()
        self.configure_ina_devices()

    def setup_gpio_and_pwm(self) -> None:
        print("[GPIO] Connecting to pigpio...")
        self.pi = pigpio.pi()

        if not self.pi.connected:
            raise SimpleTestError(
                "pigpio daemon not running. Start it with:\n"
                "  sudo systemctl start pigpiod"
            )

        # set all cs pins high initially
        for pin in [
            self.pins.cs_ina_in_bcm,
            self.pins.cs_ina_out_bcm,
            self.pins.cs_adc_bcm,
        ]:
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 1)

        # gate driver enable should be low initially
        self.pi.set_mode(self.pins.gd_enable_bcm, pigpio.OUTPUT)
        self.pi.write(self.pins.gd_enable_bcm, 0)

        # PWM is 0% as default
        self.pi.set_mode(self.pins.pwm_bcm, pigpio.OUTPUT)
        self.set_pwm_duty(0.0)

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
            if self.pi is not None:
                self.set_pwm_duty(0.0)
                self.pi.write(self.pins.gd_enable_bcm, 0)

                # release CS pins
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

    # PWM STUFF

    def set_gd_enable(self, enable: bool) -> None:
        if self.pi is None:
            raise SimpleTestError("pigpio not initialized")
        self.pi.write(self.pins.gd_enable_bcm, 1 if enable else 0)

    def set_pwm_duty(self, duty_fraction: float) -> None:
        if self.pi is None:
            raise SimpleTestError("pigpio not initialized")

        duty = max(0.0, min(1.0, duty_fraction))
        duty_ppm = int(round(duty * 1_000_000))

        self.pi.hardware_PWM(
            self.pins.pwm_bcm,
            self.pwm_cfg.frequency_hz,
            duty_ppm,
        )

    def ramp_pwm(self, start: float, stop: float, ramp_time_s: float, steps: int) -> None:
        if steps <= 0:
            self.set_pwm_duty(stop)
            return

        dt = ramp_time_s / steps

        for i in range(steps + 1):
            frac = i / steps
            duty = start + (stop - start) * frac
            self.set_pwm_duty(duty)
            time.sleep(dt)

    # SPI functions

    def release_all_cs(self) -> None:
        if self.pi is None:
            raise SimpleTestError("pigpio not initialized")

        self.pi.write(self.pins.cs_ina_in_bcm, 1)
        self.pi.write(self.pins.cs_ina_out_bcm, 1)
        self.pi.write(self.pins.cs_adc_bcm, 1)

    def cs_low(self, device: str) -> None:
        if self.pi is None:
            raise SimpleTestError("pigpio not initialized")

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
        if self.pi is None:
            raise SimpleTestError("pigpio not initialized")

        if device == "ina_in":
            self.pi.write(self.pins.cs_ina_in_bcm, 1)
        elif device == "ina_out":
            self.pi.write(self.pins.cs_ina_out_bcm, 1)
        elif device == "adc":
            self.pi.write(self.pins.cs_adc_bcm, 1)
        else:
            raise ValueError(f"Unknown SPI device: {device}")

    def transfer(self, tx: list[int], device: str) -> list[int]:
        if self.spi is None:
            raise SimpleTestError("SPI not initialized")

        self.cs_low(device)
        try:
            return self.spi.xfer2(tx)
        finally:
            self.cs_high(device)

    # MCP3208

    def read_adc_raw(self, channel: int) -> int:
        if not 0 <= channel <= 7:
            raise ValueError("MCP3208 channel must be 0..7")

        cmd = 0x06 | ((channel & 0x04) >> 2)
        msb = (channel & 0x03) << 6

        rx = self.transfer([cmd, msb, 0x00], "adc")
        return ((rx[1] & 0x0F) << 8) | rx[2]

    def read_adc_voltage(self, channel: int) -> float:
        raw = self.read_adc_raw(channel)
        return (raw / 4095.0) * self.sensor_cfg.vref

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

    def compute_current_lsb_and_cal(self) -> tuple[float, int]:
        current_lsb = self.sensor_cfg.max_expected_current_a / (2 ** 19)
        shunt_cal = 13107.2e6 * current_lsb * self.sensor_cfg.rshunt_ohms

        if self.sensor_cfg.use_low_shunt_range:
            shunt_cal *= 4

        return current_lsb, int(round(shunt_cal)) & 0x7FFF

    def configure_ina_devices(self) -> None:
        print("[INA229] Configuring devices...")
        self.current_lsb, self.shunt_cal = self.compute_current_lsb_and_cal()

        config = 0x0010 if self.sensor_cfg.use_low_shunt_range else 0x0000

        # copied from gemini
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
        if self.current_lsb is None:
            raise SimpleTestError("INA current_lsb not configured")

        raw24 = self.read_reg(REG_CURRENT, 3, device)
        raw20 = (raw24 >> 4) & 0xFFFFF
        signed = sign_extend(raw20, 20)

        return signed * self.current_lsb

    # MAIN LOOP

    def request_stop(self, *_args) -> None:
        self.running = False

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        self.setup()

        target_duty = max(0.0, min(1.0, self.pwm_cfg.duty_fraction))
        expected_vob = 1.0 - target_duty

        print("\n=== Simple PWM + Sensor Integration Test ===")
        print(f"PWM pin       : BCM{self.pins.pwm_bcm} / physical pin 32")
        print(f"GD_ENABLE pin : BCM{self.pins.gd_enable_bcm} / physical pin 31")
        print(f"Frequency     : {self.pwm_cfg.frequency_hz} Hz")
        print(f"VOA duty      : {target_duty * 100:.1f}% expected")
        print(f"VOB duty      : {expected_vob * 100:.1f}% expected")
        print("Mode          : fixed duty, no MPPT, no buck/boost decisions")
        print()

        print("[PWM] Safe state: GD_ENABLE=LOW, PWM=0%")
        self.set_gd_enable(False)
        self.set_pwm_duty(0.0)
        time.sleep(0.25)

        print("[PWM] Enabling gate driver...")
        self.set_gd_enable(True)
        time.sleep(0.1)

        print(f"[PWM] Ramping to {target_duty * 100:.1f}% duty...")
        self.ramp_pwm(
            start=0.0,
            stop=target_duty,
            ramp_time_s=self.pwm_cfg.ramp_time_s,
            steps=self.pwm_cfg.ramp_steps,
        )

        print("\nStarting sensor print loop. Press Ctrl+C to stop.\n")

        step = 0
        next_time = time.time()

        while self.running:
            try:
                current_in = self.read_current("ina_in")
                current_out = self.read_current("ina_out")

                vin = self.read_scaled_voltage(self.sensor_cfg.vin_channel)
                vout = self.read_scaled_voltage(self.sensor_cfg.vout_channel)

                print(
                    f"[{step:05d}] "
                    f"D_VOA={target_duty * 100:5.1f}% | "
                    f"D_VOB={expected_vob * 100:5.1f}% | "
                    f"I_IN={current_in:+.6f} A | "
                    f"I_OUT={current_out:+.6f} A | "
                    f"Vin={vin:.3f} V | "
                    f"Vout={vout:.3f} V"
                )

                step += 1
                next_time += self.spi_cfg.read_period_s
                sleep_s = next_time - time.time()

                if sleep_s > 0:
                    time.sleep(sleep_s)
                else:
                    next_time = time.time()

            except Exception as exc:
                print(f"[ERROR] {exc}")
                time.sleep(0.5)

        print("\nStopping: ramping PWM down...")
        self.ramp_pwm(
            start=target_duty,
            stop=0.0,
            ramp_time_s=1.0,
            steps=50,
        )
        self.set_gd_enable(False)


def main() -> int:
    app = SimplePwmSensorTest()

    try:
        app.run()
        return 0
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 1
    finally:
        app.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
