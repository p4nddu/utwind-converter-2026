import sys

from hal.gpio import PiGpio
from hal.pwm import PiPwm, PwmConfig
from drivers.si8274 import SI8274


PWM_FREQ = 10_000


def main() -> int:
    gpio = PiGpio()
    pwm = None
    gate = None

    try:
        print("initializing gpio...")
        gpio.init()

        pwm = PiPwm(gpio=gpio, config=PwmConfig(frequency_hz=PWM_FREQ))
        gate = SI8274(gpio)

        print("initializing pwm...")
        pwm.init()

        print("forcing all outputs off...")
        gate.disable_all()
        pwm.stop_pwm("pwm1")
        pwm.stop_pwm("pwm2")
        gpio.force_safe_outputs()

        print("shutdown complete")
        return 0

    except Exception as exc:
        print(f"ERROR during shutdown: {exc}")
        return 1

    finally:
        try:
            if pwm is not None:
                pwm.stop_pwm("pwm1")
                pwm.stop_pwm("pwm2")
                pwm.deinit()
        except Exception:
            pass

        try:
            if gate is not None:
                gate.disable_all()
        except Exception:
            pass

        try:
            gpio.force_safe_outputs()
        except Exception:
            pass

        try:
            gpio.deinit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
