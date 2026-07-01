import time
import signal
import sys

from hal.gpio import PiGpio
from hal.spi import PiSpi
from hal.pwm import PiPwm, PwmConfig
from drivers.ina229 import INA229, INA229Config
from drivers.mcp3208 import MCP3208
from drivers.si8274 import SI8274


PWM_FREQ = 10_000

DUTY_START = 0.00
DUTY_MAX = 0.85
DUTY_MIN = 0.00

LOOP_PERIOD = 0.02
LOG_PERIOD = 0.25
MAX_DUTY_STEP = 0.005
VOLTAGE_KP = 0.02
FILTER_ALPHA = 0.25

MIN_VIN_TO_RUN = 2.0
VOUT_MARGIN = 0.1
MAX_VOUT_ABS = 6.0     # safety limit
MAX_CURRENT_ABS = 1.0  # safety limit for either INA229 sensor
CHECK_INA_IDS = False

running = True


def handle_signal(signum, frame):
    global running
    running = False


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def step_toward(current: float, target: float, step: float) -> float:
    if current < target:
        return min(current + step, target)
    if current > target:
        return max(current - step, target)
    return current


def filtered(previous: float, raw: float, alpha: float) -> float:
    return (1.0 - alpha) * previous + alpha * raw


def buck_duty_command(vin: float, vout: float, vtarget: float) -> float:
    error = vtarget - vout

    if abs(error) <= VOUT_MARGIN:
        return 0.0

    return VOLTAGE_KP * error


def main() -> int:
    global running

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    gpio = PiGpio()
    spi = PiSpi(gpio=gpio)
    pwm = PiPwm(gpio=gpio, config=PwmConfig(frequency_hz=PWM_FREQ, max_duty=DUTY_MAX))
    adc = MCP3208(spi)
    ina = INA229(
        spi,
        INA229Config(
            rshunt_ohms=0.01,
            max_expected_current=MAX_CURRENT_ABS,
        ),
    )
    gate = SI8274(gpio)

    duty = DUTY_START
    vin_f = 0.0
    vout_f = 0.0
    next_log = 0.0

    try:
        print("initializing gpio...")
        gpio.init()

        print("initializing spi...")
        spi.init()

        print("initializing pwm...")
        pwm.init()

        print("initializing current sensors...")
        ina.initialize_all_ina(check_id=CHECK_INA_IDS)

        print("forcing safe defaults...")
        gate.disable("gd1")
        gate.disable("gd2")
        pwm.stop_pwm("pwm1")
        pwm.stop_pwm("pwm2")

        vin = adc.read_vin()
        vout = adc.read_vout()
        iin = ina.read_ina_in()
        iout = ina.read_ina_out()
        vin_f = vin
        vout_f = vout
        vtarget = 0.5 * vin_f

        print(
            f"Initial Vin = {vin:.3f} V | "
            f"Vout = {vout:.3f} V | "
            f"Iin = {iin:.3f} A | "
            f"Iout = {iout:.3f} A"
        )
        print(f"Target Vout = Vin / 2 = {vtarget:.3f} V")

        if vin < MIN_VIN_TO_RUN:
            raise RuntimeError(f"Vin too low to run: {vin:.3f} V")

        print("enabling buck/input-side gate driver only...")
        gate.enable("gd1")
        gate.disable("gd2")
        pwm.stop_pwm("pwm2")

        print("running closed-loop buck test on PWM1. Press Ctrl+C to stop.")
        next_time = time.monotonic()
        next_log = next_time

        while running:
            now = time.monotonic()
            vin = adc.read_vin()
            vout = adc.read_vout()
            iin = ina.read_ina_in()
            iout = ina.read_ina_out()
            vin_f = filtered(vin_f, vin, FILTER_ALPHA)
            vout_f = filtered(vout_f, vout, FILTER_ALPHA)
            vtarget = 0.5 * vin_f

            if vin_f < MIN_VIN_TO_RUN:
                raise RuntimeError(f"Vin dropped too low: {vin_f:.3f} V")

            if vout_f > MAX_VOUT_ABS:
                raise RuntimeError(f"Absolute Vout overvoltage: {vout_f:.3f} V")

            if abs(iin) > MAX_CURRENT_ABS:
                raise RuntimeError(f"Input overcurrent: {iin:.3f} A")

            if abs(iout) > MAX_CURRENT_ABS:
                raise RuntimeError(f"Output overcurrent: {iout:.3f} A")

            desired_duty = buck_duty_command(vin_f, vout_f, vtarget)
            desired_duty = clamp(duty + desired_duty, DUTY_MIN, DUTY_MAX)
            duty = step_toward(duty, desired_duty, MAX_DUTY_STEP)

            pwm.set_duty("pwm1", duty)

            if now >= next_log:
                print(
                    f"Vin = {vin_f:7.3f} V | "
                    f"Vout = {vout_f:7.3f} V | "
                    f"Iin = {iin:7.3f} A | "
                    f"Iout = {iout:7.3f} A | "
                    f"Target = {vtarget:7.3f} V | "
                    f"Cmd = {desired_duty * 100:6.2f}% | "
                    f"Duty = {duty * 100:6.2f}%"
                )
                next_log += LOG_PERIOD

            next_time += LOOP_PERIOD
            sleep_s = next_time - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_time = time.monotonic()

    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    finally:
        print("\nshutting down...")

        try:
            while duty > 0:
                duty = max(0.0, duty - MAX_DUTY_STEP)
                pwm.set_duty("pwm1", duty)
                time.sleep(LOOP_PERIOD)
        except Exception:
            pass

        try:
            pwm.stop_pwm("pwm1")
            pwm.stop_pwm("pwm2")
        except Exception:
            pass

        try:
            pwm.deinit()
        except Exception:
            pass

        try:
            gate.disable("gd1")
            gate.disable("gd2")
        except Exception:
            pass

        try:
            spi.deinit()
        except Exception:
            pass

        try:
            gpio.force_safe_outputs()
            gpio.deinit()
        except Exception:
            pass

        print("shutdown complete")

    return 0


if __name__ == "__main__":
    sys.exit(main())
