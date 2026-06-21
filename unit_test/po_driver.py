#!/usr/bin/env python3
"""
po_mppt_si8274_test.py

Perturb-and-observe MPPT-style bench test for a two-switch buck-boost PCB
where one Si8274 PWM-input ISOdriver drives both switches complementarily:

    Pi PWM BCM12 / physical pin 32 -> Si8274 PWM input
    Pi GD_ENABLE BCM6 / physical pin 31 -> gate-driver enable
    Si8274 VOA -> one MOSFET gate
    Si8274 VOB -> the other MOSFET gate, complementary/inverse of VOA

This script:
1. Initializes pigpio and manual SPI chip-selects.
2. Initializes MCP3208 voltage ADC and two INA229 current sensors.
3. Starts PWM safely at 0% duty with GD_ENABLE low.
4. Soft-starts VOA duty to an initial value.
5. Repeatedly measures Vin, Vout, Iin, Iout, computes power.
6. Perturbs commanded duty D, where D is VOA duty. VOB duty is approximately 1-D.
7. If power increases, keeps perturbing in the same direction. If power decreases, reverses direction.
8. On Ctrl+C or fault, ramps down, disables gate driver, releases CS lines, and closes SPI.

WARNING:
- This is for low-voltage, current-limited bench testing first.
- Scope VOA/VOB and both MOSFET Vgs waveforms before applying meaningful converter power.
- Because the hardware is mistakenly wired as complementary synchronous switching, this does not explicitly select buck/boost mode.
- Clamp duty range conservatively until you verify safe waveforms and current paths.
"""

from __future__ import annotations

from dataclasses import dataclass
import signal
import sys
import time

import pigpio
import spidev


# =========================
# CONFIGURATION
# =========================

@dataclass(frozen=True)
class Pins:
    # Raspberry Pi pins, BCM numbering
    pwm_bcm: int = 12          # physical pin 32
    gd_enable_bcm: int = 6     # physical pin 31

    # Manual chip selects, active-low
    cs_ina_in: int = 25
    cs_ina_out: int = 17
    cs_adc: int = 27


@dataclass(frozen=True)
class SpiConfig:
    bus: int = 0
    device: int = 0            # CE pin unused because no_cs=True
    max_speed_hz: int = 1_000_000
    mode: int = 1              # from your INA/MCP test script


@dataclass(frozen=True)
class SensorConfig:
    # MCP3208 voltage channels
    vin_adc_channel: int = 0
    vout_adc_channel: int = 1
    vref: float = 3.3

    # Voltage divider scaling. Your previous script used DIV_INV = 12.5.
    # V_actual = V_adc * voltage_divider_inverse
    voltage_divider_inverse: float = 12.5

    # INA229 calibration
    rshunt_ohms: float = 1.5
    max_expected_current_a: float = 0.1
    use_low_shunt_range: bool = False

    # Depending on sensor orientation, output current may read negative.
    # Set this to -1.0 if Pout prints negative while power is actually flowing to the load.
    iout_sign: float = 1.0
    iin_sign: float = 1.0


@dataclass(frozen=True)
class PwmMpptConfig:
    frequency_hz: int = 10_000

    # Commanded duty is VOA duty. VOB duty is approximately 1 - VOA duty.
    initial_duty: float = 0.50
    min_duty: float = 0.25
    max_duty: float = 0.85

    # P&O step size. Keep small for first power tests.
    duty_step: float = 0.005      # 0.5% duty per perturbation

    # Timing
    softstart_time_s: float = 3.0
    softstart_steps: int = 150
    sample_period_s: float = 0.2
    settle_time_s: float = 0.15

    # Averaging per decision step
    samples_per_step: int = 5

    # Safety limits for early bench testing. Adjust for your setup.
    max_vin_v: float = 60.0
    max_vout_v: float = 60.0
    max_abs_iin_a: float = 0.5
    max_abs_iout_a: float = 0.5
    max_pout_w: float = 20.0

    # Optional load/output undervoltage stop guard.
    # Set to 0 to disable. Useful if sensor reads nonsense or converter collapses.
    min_vout_when_enabled_v: float = 0.0


# =========================
# INA229 REGISTERS
# =========================

REG_CONFIG = 0x00
REG_ADC_CONFIG = 0x01
REG_SHUNT_CAL = 0x02
REG_CURRENT = 0x07


# =========================
# HELPERS
# =========================

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit


class MpptTestError(RuntimeError):
    pass


# =========================
# MAIN TEST CLASS
# =========================

class Si8274PerturbObserveTest:
    def __init__(
        self,
        pins: Pins = Pins(),
        spi_cfg: SpiConfig = SpiConfig(),
        sensor_cfg: SensorConfig = SensorConfig(),
        mppt_cfg: PwmMpptConfig = PwmMpptConfig(),
    ):
        self.pins = pins
        self.spi_cfg = spi_cfg
        self.sensor_cfg = sensor_cfg
        self.mppt_cfg = mppt_cfg

        self.pi: pigpio.pi | None = None
        self.spi: spidev.SpiDev | None = None
        self.running = True

        self.current_lsb: float | None = None
        self.shunt_cal: int | None = None

        self.duty = self.mppt_cfg.initial_duty
        self.direction = +1
        self.previous_power: float | None = None

    # -------------------------
    # GPIO / PWM
    # -------------------------

    def setup_gpio_pwm(self) -> None:
        print("[GPIO] Connecting to pigpio...")
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise MpptTestError("pigpio daemon not running. Try: sudo systemctl start pigpiod")

        # Manual active-low chip selects: inactive high
        for pin in [self.pins.cs_ina_in, self.pins.cs_ina_out, self.pins.cs_adc]:
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 1)

        # Gate-driver enable starts disabled
        self.pi.set_mode(self.pins.gd_enable_bcm, pigpio.OUTPUT)
        self.pi.write(self.pins.gd_enable_bcm, 0)

        # PWM starts at 0% duty
        self.pi.set_mode(self.pins.pwm_bcm, pigpio.OUTPUT)
        self.set_pwm_duty(0.0)

    def set_gd_enable(self, enable: bool) -> None:
        if self.pi is None:
            raise MpptTestError("GPIO not initialized")
        self.pi.write(self.pins.gd_enable_bcm, 1 if enable else 0)

    def set_pwm_duty(self, duty: float) -> None:
        if self.pi is None:
            raise MpptTestError("GPIO not initialized")
        duty = clamp(duty, 0.0, 1.0)
        duty_ppm = int(round(duty * 1_000_000))
        self.pi.hardware_PWM(self.pins.pwm_bcm, self.mppt_cfg.frequency_hz, duty_ppm)

    def ramp_duty(self, start: float, stop: float, ramp_time_s: float, steps: int) -> None:
        if steps <= 0:
            self.set_pwm_duty(stop)
            return
        dt = ramp_time_s / steps
        for i in range(steps + 1):
            if not self.running:
                break
            frac = i / steps
            duty = start + (stop - start) * frac
            self.set_pwm_duty(duty)
            time.sleep(dt)

    # -------------------------
    # SPI / chip selects
    # -------------------------

    def setup_spi(self) -> None:
        print("[SPI] Opening SPI...")
        self.spi = spidev.SpiDev()
        self.spi.open(self.spi_cfg.bus, self.spi_cfg.device)
        self.spi.no_cs = True
        self.spi.mode = self.spi_cfg.mode
        self.spi.max_speed_hz = self.spi_cfg.max_speed_hz

    def all_cs_high(self) -> None:
        if self.pi is None:
            return
        self.pi.write(self.pins.cs_ina_in, 1)
        self.pi.write(self.pins.cs_ina_out, 1)
        self.pi.write(self.pins.cs_adc, 1)

    def cs_low(self, device: str) -> None:
        if self.pi is None:
            raise MpptTestError("GPIO not initialized")
        self.all_cs_high()
        if device == "ina_in":
            self.pi.write(self.pins.cs_ina_in, 0)
        elif device == "ina_out":
            self.pi.write(self.pins.cs_ina_out, 0)
        elif device == "adc":
            self.pi.write(self.pins.cs_adc, 0)
        else:
            raise ValueError(f"Unknown SPI device: {device}")

    def cs_high(self, device: str) -> None:
        if self.pi is None:
            return
        if device == "ina_in":
            self.pi.write(self.pins.cs_ina_in, 1)
        elif device == "ina_out":
            self.pi.write(self.pins.cs_ina_out, 1)
        elif device == "adc":
            self.pi.write(self.pins.cs_adc, 1)
        else:
            raise ValueError(f"Unknown SPI device: {device}")

    def transfer(self, tx: list[int], device: str) -> list[int]:
        if self.spi is None:
            raise MpptTestError("SPI not initialized")
        self.cs_low(device)
        try:
            return self.spi.xfer2(tx)
        finally:
            self.cs_high(device)

    # -------------------------
    # MCP3208 voltage ADC
    # -------------------------

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
        return self.read_adc_voltage(channel) * self.sensor_cfg.voltage_divider_inverse

    # -------------------------
    # INA229 current sensors
    # -------------------------

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
        self.write_reg(REG_CONFIG, 0x8000, 2, "ina_in")
        self.write_reg(REG_CONFIG, 0x8000, 2, "ina_out")
        time.sleep(0.02)

    def compute_current_lsb_and_cal(self) -> tuple[float, int]:
        current_lsb = self.sensor_cfg.max_expected_current_a / (2 ** 19)
        shunt_cal = 13107.2e6 * current_lsb * self.sensor_cfg.rshunt_ohms
        if self.sensor_cfg.use_low_shunt_range:
            shunt_cal *= 4
        return current_lsb, int(round(shunt_cal)) & 0x7FFF

    def configure_ina_devices(self) -> None:
        self.current_lsb, self.shunt_cal = self.compute_current_lsb_and_cal()

        config = 0x0010 if self.sensor_cfg.use_low_shunt_range else 0x0000

        # Same ADC_CONFIG as your existing sensor script.
        adc_config = (0xA << 12) | (0x5 << 9) | (0x5 << 6) | (0x5 << 3) | 0x2

        for device in ["ina_in", "ina_out"]:
            self.write_reg(REG_CONFIG, config, 2, device)
            self.write_reg(REG_ADC_CONFIG, adc_config, 2, device)
            self.write_reg(REG_SHUNT_CAL, self.shunt_cal, 2, device)

    def read_current(self, device: str) -> float:
        if self.current_lsb is None:
            raise MpptTestError("INA229 not configured")
        raw24 = self.read_reg(REG_CURRENT, 3, device)
        raw20 = (raw24 >> 4) & 0xFFFFF
        signed = sign_extend(raw20, 20)
        return signed * self.current_lsb

    # -------------------------
    # Measurements / P&O
    # -------------------------

    def read_once(self) -> dict[str, float]:
        iin = self.sensor_cfg.iin_sign * self.read_current("ina_in")
        iout = self.sensor_cfg.iout_sign * self.read_current("ina_out")
        vin = self.read_scaled_voltage(self.sensor_cfg.vin_adc_channel)
        vout = self.read_scaled_voltage(self.sensor_cfg.vout_adc_channel)
        pin = vin * iin
        pout = vout * iout
        return {
            "vin": vin,
            "vout": vout,
            "iin": iin,
            "iout": iout,
            "pin": pin,
            "pout": pout,
        }

    def read_averaged(self, n: int) -> dict[str, float]:
        acc = {"vin": 0.0, "vout": 0.0, "iin": 0.0, "iout": 0.0, "pin": 0.0, "pout": 0.0}
        n = max(1, n)
        for _ in range(n):
            m = self.read_once()
            for k in acc:
                acc[k] += m[k]
            time.sleep(self.mppt_cfg.sample_period_s)
        return {k: v / n for k, v in acc.items()}

    def check_safety(self, m: dict[str, float]) -> None:
        cfg = self.mppt_cfg
        faults: list[str] = []
        if m["vin"] > cfg.max_vin_v:
            faults.append(f"Vin {m['vin']:.3f} V > limit {cfg.max_vin_v:.3f} V")
        if m["vout"] > cfg.max_vout_v:
            faults.append(f"Vout {m['vout']:.3f} V > limit {cfg.max_vout_v:.3f} V")
        if abs(m["iin"]) > cfg.max_abs_iin_a:
            faults.append(f"|Iin| {abs(m['iin']):.6f} A > limit {cfg.max_abs_iin_a:.6f} A")
        if abs(m["iout"]) > cfg.max_abs_iout_a:
            faults.append(f"|Iout| {abs(m['iout']):.6f} A > limit {cfg.max_abs_iout_a:.6f} A")
        if abs(m["pout"]) > cfg.max_pout_w:
            faults.append(f"|Pout| {abs(m['pout']):.6f} W > limit {cfg.max_pout_w:.6f} W")
        if cfg.min_vout_when_enabled_v > 0 and m["vout"] < cfg.min_vout_when_enabled_v:
            faults.append(f"Vout {m['vout']:.3f} V < minimum {cfg.min_vout_when_enabled_v:.3f} V")
        if faults:
            raise MpptTestError("Safety fault: " + "; ".join(faults))

    def perturb_observe_update(self, power_now: float) -> None:
        if self.previous_power is None:
            self.previous_power = power_now
            return

        delta_p = power_now - self.previous_power

        # Classic P&O: if the last perturbation reduced power, reverse direction.
        if delta_p < 0:
            self.direction *= -1

        self.duty = clamp(
            self.duty + self.direction * self.mppt_cfg.duty_step,
            self.mppt_cfg.min_duty,
            self.mppt_cfg.max_duty,
        )
        self.set_pwm_duty(self.duty)
        self.previous_power = power_now

    # -------------------------
    # Startup / shutdown
    # -------------------------

    def setup_all(self) -> None:
        self.setup_gpio_pwm()
        self.setup_spi()
        self.reset_ina_devices()
        self.configure_ina_devices()

    def safe_shutdown(self) -> None:
        print("\n[SHUTDOWN] Ramping PWM down, disabling gate driver, cleaning up...")
        try:
            if self.pi is not None:
                current_duty = self.duty
                self.ramp_duty(current_duty, 0.0, ramp_time_s=1.0, steps=50)
                self.set_pwm_duty(0.0)
                self.set_gd_enable(False)
        except Exception as exc:
            print(f"[WARN] PWM shutdown issue: {exc}")

        try:
            self.all_cs_high()
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

    def run(self) -> None:
        def stop_handler(sig, frame):
            self.running = False

        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)

        self.setup_all()

        print("\n=== Si8274 Complementary P&O Test ===")
        print(f"PWM: BCM{self.pins.pwm_bcm} physical pin 32")
        print(f"GD_ENABLE: BCM{self.pins.gd_enable_bcm} physical pin 31")
        print(f"PWM frequency: {self.mppt_cfg.frequency_hz} Hz")
        print(f"Initial VOA duty: {self.mppt_cfg.initial_duty * 100:.2f}%")
        print(f"Duty limits: {self.mppt_cfg.min_duty * 100:.1f}% to {self.mppt_cfg.max_duty * 100:.1f}%")
        print(f"Duty step: {self.mppt_cfg.duty_step * 100:.3f}%")
        print("Note: VOB duty is approximately 1 - VOA duty because one Si8274 PWM input is used.\n")

        # Safe pre-enable state
        self.set_gd_enable(False)
        self.set_pwm_duty(0.0)
        time.sleep(0.25)

        print("[START] Enabling gate driver at 0% duty...")
        self.set_gd_enable(True)
        time.sleep(0.1)

        self.duty = clamp(self.mppt_cfg.initial_duty, self.mppt_cfg.min_duty, self.mppt_cfg.max_duty)
        print(f"[SOFTSTART] Ramping VOA duty to {self.duty * 100:.2f}%...")
        self.ramp_duty(0.0, self.duty, self.mppt_cfg.softstart_time_s, self.mppt_cfg.softstart_steps)
        time.sleep(self.mppt_cfg.settle_time_s)

        step = 0
        while self.running:
            m = self.read_averaged(self.mppt_cfg.samples_per_step)
            self.check_safety(m)

            # Use output power as MPPT objective. If your current orientation is negative,
            # change SensorConfig.iout_sign to -1.0.
            p_obj = m["pout"]
            self.perturb_observe_update(p_obj)

            print(
                f"[{step:05d}] "
                f"D_VOA={self.duty * 100:6.2f}% | "
                f"D_VOB~{(1.0 - self.duty) * 100:6.2f}% | "
                f"Vin={m['vin']:7.3f} V | Vout={m['vout']:7.3f} V | "
                f"Iin={m['iin']:+.6f} A | Iout={m['iout']:+.6f} A | "
                f"Pin={m['pin']:+.4f} W | Pout={m['pout']:+.4f} W | "
                f"dir={self.direction:+d}"
            )

            step += 1
            time.sleep(self.mppt_cfg.settle_time_s)


# =========================
# ENTRY POINT
# =========================

def main() -> int:
    app = Si8274PerturbObserveTest()
    try:
        app.run()
        return 0
    except KeyboardInterrupt:
        print("\nCtrl+C received.")
        return 130
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 1
    finally:
        app.safe_shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
