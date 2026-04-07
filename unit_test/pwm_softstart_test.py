#!/usr/bin/env python3
"""
pwm_softstart_test.py

this script creates a ramp up pwm output from the pi.
to test, probe the output from pi then connect to gate drivers after verifying the output.

note:
before testing, install pigpio library using 'sudo apt install pigpio python3-pigpio'
and start the daemon with 'sudo systemctl enable pigpiod'

pinouts:
- PWM output: BCM12 (physical pin 32)
- gate driver enable: BCM6 (physical pin 31)

Behavior:
1. Connects to pigpiod
2. Sets GD_ENABLE low 
3. Starts hardware PWM on BCM12 at 0% duty
4. Asserts GD_ENABLE // not needed if not connected to gate driver
5. Soft-start ramps duty from 0% to 50%
6. on exit, wind down duty, disable gate driver, stop hardware PWM

P.S using pigpio since apparently its better for fast switching use cases.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys
import time

import pigpio


class PwmTestError(RuntimeError):
    """PWM test related errors."""


@dataclass(frozen=True)
class TestPins:
    pwm_bcm: int = 12        # physical pin 32
    gd_enable_bcm: int = 6   # physical pin 31


@dataclass(frozen=True)
class PwmConfig:
    frequency_hz: int = 10_000
    target_duty: float = 0.50       # 50%
    ramp_time_s: float = 2.0
    ramp_steps: int = 100
    hold_time_s: float = 10.0       # how long to stay at 50% before exiting naturally


class PiPwmSoftStartTest:
    """
    Encapsulates the pigpio hardware initialization and PWM ramp behavior.
    """

    def __init__(self, pins: TestPins = TestPins(), cfg: PwmConfig = PwmConfig()):
        self.pins = pins
        self.cfg = cfg
        self.pi: pigpio.pi | None = None
        self._inited = False

    def init(self) -> None:
        """
        Connect to pigpiod and initialize GPIO states safely.
        """
        if self._inited:
            return

        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise PwmTestError(
                "Could not connect to pigpiod. "
                "Make sure pigpio is installed and the daemon is running:\n"
                "  sudo systemctl start pigpiod"
            )

        # Gate driver enable as standard output, default LOW
        self.pi.set_mode(self.pins.gd_enable_bcm, pigpio.OUTPUT)
        self.pi.write(self.pins.gd_enable_bcm, 0)

        # Set PWM pin to output and start at 0% duty
        self.pi.set_mode(self.pins.pwm_bcm, pigpio.OUTPUT)
        self.pi.hardware_PWM(self.pins.pwm_bcm, self.cfg.frequency_hz, 0)

        self._inited = True

    def deinit(self) -> None:
        """
        Return outputs to a safe state and disconnect from pigpiod.
        """
        if not self._inited or self.pi is None:
            return

        try:
            # Stop PWM
            self.pi.hardware_PWM(self.pins.pwm_bcm, self.cfg.frequency_hz, 0)

            # Disable gate driver
            self.pi.write(self.pins.gd_enable_bcm, 0)
        finally:
            self.pi.stop()
            self.pi = None
            self._inited = False

    def set_enable(self, enable: bool) -> None:
        self._require_init()
        assert self.pi is not None
        self.pi.write(self.pins.gd_enable_bcm, 1 if enable else 0)

    def set_duty_fraction(self, duty: float) -> None:
        """
        Set hardware PWM duty as a fraction from 0.0 to 1.0.

        pigpio hardware_PWM duty cycle uses a range of 0 to 1_000_000.
        """
        self._require_init()
        assert self.pi is not None

        duty = max(0.0, min(1.0, duty))
        duty_ppm = int(duty * 1_000_000)
        self.pi.hardware_PWM(self.pins.pwm_bcm, self.cfg.frequency_hz, duty_ppm)

    def ramp_duty(self, start: float, stop: float, ramp_time_s: float, steps: int) -> None:
        """
        Linear soft-start / soft-stop duty ramp.
        """
        self._require_init()

        if steps <= 0:
            raise PwmTestError("ramp steps must be > 0")
        if ramp_time_s < 0:
            raise PwmTestError("ramp time must be >= 0")

        dt = ramp_time_s / steps if steps > 0 else 0.0

        for i in range(steps + 1):
            frac = i / steps
            duty = start + (stop - start) * frac
            self.set_duty_fraction(duty)
            time.sleep(dt)

    def run(self) -> None:
        """
        Main test procedure.
        """
        self.init()

        print("=== PWM Soft-Start Test ===")
        print(f"PWM pin        : BCM{self.pins.pwm_bcm} (physical pin 32)")
        print(f"GD_ENABLE pin  : BCM{self.pins.gd_enable_bcm} (physical pin 31)")
        print(f"Frequency      : {self.cfg.frequency_hz} Hz")
        print(f"Target duty    : {self.cfg.target_duty * 100:.1f}%")
        print(f"Ramp time      : {self.cfg.ramp_time_s:.2f} s")
        print()

        print("Initial safe state: GD_ENABLE=LOW, PWM duty=0%")
        self.set_enable(False)
        self.set_duty_fraction(0.0)
        time.sleep(0.25)

        print("Enabling gate driver...")
        self.set_enable(True)
        time.sleep(0.1)

        print("Soft-start ramp up...")
        self.ramp_duty(
            start=0.0,
            stop=self.cfg.target_duty,
            ramp_time_s=self.cfg.ramp_time_s,
            steps=self.cfg.ramp_steps,
        )

        print(f"Holding at {self.cfg.target_duty * 100:.1f}% duty for {self.cfg.hold_time_s:.1f} s...")
        time.sleep(self.cfg.hold_time_s)

        print("Soft-stop ramp down...")
        self.ramp_duty(
            start=self.cfg.target_duty,
            stop=0.0,
            ramp_time_s=self.cfg.ramp_time_s,
            steps=self.cfg.ramp_steps,
        )

        print("Disabling gate driver...")
        self.set_enable(False)
        print("Done.")

    def _require_init(self) -> None:
        if not self._inited or self.pi is None:
            raise PwmTestError("Test not initialized. Call init() first.")


def main() -> int:
    test = PiPwmSoftStartTest()

    try:
        test.run()
        return 0
    except KeyboardInterrupt:
        print("\nCtrl+C received. Ramping down and shutting off safely...")
        try:
            if test._inited:
                test.ramp_duty(
                    start=test.cfg.target_duty,
                    stop=0.0,
                    ramp_time_s=1.0,
                    steps=50,
                )
                test.set_enable(False)
        except Exception:
            pass
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        test.deinit()


if __name__ == "__main__":
    raise SystemExit(main())