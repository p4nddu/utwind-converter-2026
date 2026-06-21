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

    def set_gd_enable2(self, enable: bool) -> None:
        self.pi.write(self.pins.gd_enable_bcm2, 1 if enable else 0)

    def set_pwm_duty(self, duty_fraction: float) -> None:
        duty = max(0.0, min(1.0, duty_fraction))
        duty_ppm = int(round(duty * 1_000_000))

        self.pi.hardware_PWM(
            self.pins.pwm_bcm,
            self.pwm_cfg.frequency_hz,
            duty_ppm,
        )

    def set_pwm_duty2(self, duty_fraction: float) -> None:
        duty = max(0.0, min(1.0, duty_fraction))
        duty_ppm = int(round(duty * 1_000_000))

        self.pi.hardware_PWM(
            self.pins.pwm_bcm2,
            self.pwm_cfg.frequency_hz,
            duty_ppm,
        )

    def ramp_pwm(
        self,
        pwm_channel: int,
        current_duty: float,
        target_duty: float,
        ramp_time_s: float,
        steps: int,
    ) -> None:
        if steps <= 0:
            if pwm_channel == 0:
                self.set_pwm_duty(target_duty)
            elif pwm_channel == 1:
                self.set_pwm_duty2(target_duty)
            else:
                raise ValueError("pwm_channel must be 0 or 1")
            return

        dt = ramp_time_s / steps

        for i in range(steps + 1):
            frac = i / steps
            duty = current_duty + (target_duty - current_duty) * frac

            if pwm_channel == 0:
                self.set_pwm_duty(duty)
            elif pwm_channel == 1:
                self.set_pwm_duty2(duty)
            else:
                raise ValueError("pwm_channel must be 0 or 1")

            time.sleep(dt)
        return duty


    def init_pwm(self):

        self.set_gd_enable(False)
        self.set_gd_enable2(False)

        self.set_pwm_duty(0.0)
        self.set_pwm_duty2(0.0)

        time.sleep(0.25)

        self.set_gd_enable(True)
        self.set_gd_enable2(True)

    def run(self) -> None:
        target_duty = max(0.0, min(1.0, self.pwm_cfg.duty_fraction))

        self.init_pwm(self)
        current_pwm1_duty = 0.0
        current_pwm2_duty = 0.0
        
        time.sleep(0.1)

        current_pwm1_duty = self.ramp_pwm(
            pwm_channel=self.pins.pwm_bcm,
            current_duty=current_pwm1_duty,
            target_duty=target_duty,
            ramp_time_s=self.pwm_cfg.ramp_time_s,
            steps=self.pwm_cfg.ramp_steps,
        )

        current_pwm2_duty = self.ramp_pwm(
            pwm_channel=self.pins.pwm_bcm2,
            current_duty=current_pwm2_duty,
            target_duty=target_duty,
            ramp_time_s=self.pwm_cfg.ramp_time_s,
            steps=self.pwm_cfg.ramp_steps,
        )