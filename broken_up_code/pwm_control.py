from __future__ import annotations

import time

import pigpio

from config import PinConfig, PwmConfig, SimpleTestError


class PwmController:
    def __init__(
        self,
        pi: pigpio.pi,
        pins: PinConfig,
        pwm_cfg: PwmConfig,
    ):
        self.pi = pi
        self.pins = pins
        self.pwm_cfg = pwm_cfg

    def set_gd_enable(self, enable: bool) -> None:
        self.pi.write(self.pins.gd_enable_bcm, 1 if enable else 0)

    def set_pwm_duty(self, duty_fraction: float) -> None:
        duty = max(0.0, min(1.0, duty_fraction))
        duty_ppm = int(round(duty * 1_000_000))

        self.pi.hardware_PWM(
            self.pins.pwm_bcm,
            self.pwm_cfg.frequency_hz,
            duty_ppm,
        )

    def ramp_pwm(
        self,
        start: float,
        stop: float,
        ramp_time_s: float,
        steps: int,
    ) -> None:
        if steps <= 0:
            self.set_pwm_duty(stop)
            return

        dt = ramp_time_s / steps

        for i in range(steps + 1):
            frac = i / steps
            duty = start + (stop - start) * frac
            self.set_pwm_duty(duty)
            time.sleep(dt)

    def run(self) -> None:
        target_duty = max(0.0, min(1.0, self.pwm_cfg.duty_fraction))

        self.set_gd_enable(False)
        self.set_pwm_duty(0.0)
        time.sleep(0.25)

        self.set_gd_enable(True)
        time.sleep(0.1)

        self.ramp_pwm(
            0.0,
            target_duty,
            self.pwm_cfg.ramp_time_s,
            self.pwm_cfg.ramp_steps,
        )