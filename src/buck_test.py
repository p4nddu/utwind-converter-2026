import time
import signal
import sys

from hal.gpio import PiGpio
from hal.spi import PiSpi
from hal.pwm import PiPwm, PwmConfig
from drivers.ina229 import INA229, INA229Config
from drivers.mcp3208 import MCP3208
from drivers.si8274 import SI8274


PWM_FREQ = 150_000

DUTY_START = 0.00
DUTY_STEP = 0.01
DUTY_MAX = 0.75

RAMP_DELAY = 0.10
READ_PERIOD = 0.05
CURRENT_FILTER_ALPHA = 0.25

MIN_VIN_TO_RUN = 1.0
VOUT_MARGIN = 0.1      # stop ramp when within 0.25 V of target
MAX_VOUT_ABS = 15.0     # safety limit, adjust as needed
MAX_INPUT_CURRENT_ABS = 0.75
MAX_OUTPUT_CURRENT_ABS = 1.0
CHECK_INA_IDS = False

running = True


def handle_signal(signum, frame):
    global running
    running = False


def filtered(previous: float, raw: float, alpha: float) -> float:
    return (1.0 - alpha) * previous + alpha * raw


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
            max_expected_current=MAX_OUTPUT_CURRENT_ABS,
        ),
    )
    gate = SI8274(gpio)

    duty = DUTY_START
    last_vin = 0.0
    iin_f = 0.0
    iout_f = 0.0

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
        iin_f = iin
        iout_f = iout
        last_vin = vin
        vtarget = 0.5 * vin

        print(
            f"Initial Vin = {1.52 * vin:.3f} V | "
            f"Vout = {1.52 * vout:.3f} V | "
            f"Iin = {iin:.3f} A | "
            f"Iout = {iout:.3f} A"
        )
        print(f"Target Vout = Vin / 2 = {vtarget:.3f} V")

        if vin < MIN_VIN_TO_RUN:
            raise RuntimeError(f"Vin too low to run: {vin:.3f} V")

        print("enabling buck/input-side gate driver only...")
        gate.enable("gd1")
        gate.disable("gd2")

        print("soft-starting PWM1. Press Ctrl+C to stop.")

        while running:
            vin = adc.read_vin()
            vout = adc.read_vout()
            iin = ina.read_ina_in()
            iout = ina.read_ina_out()
            iin_f = filtered(iin_f, iin, CURRENT_FILTER_ALPHA)
            iout_f = filtered(iout_f, iout, CURRENT_FILTER_ALPHA)
            vtarget = 0.5*(0.5*vin) + 0.5*(0.5*last_vin)

            if vin < MIN_VIN_TO_RUN:
                raise RuntimeError(f"Vin dropped too low: {1.52 * vin:.3f} V")

            if vout > MAX_VOUT_ABS:
                raise RuntimeError(f"Absolute Vout overvoltage: {1.5 * vout:.3f} V")

            if abs(iin_f) >= MAX_INPUT_CURRENT_ABS:
                raise RuntimeError(f"Input overcurrent: {iin_f:.3f} A filtered ({iin:.3f} A raw)")

            if abs(iout_f) >= MAX_OUTPUT_CURRENT_ABS:
                raise RuntimeError(f"Output overcurrent: {iout_f:.3f} A filtered ({iout:.3f} A raw)")

            if vout < (vtarget - VOUT_MARGIN) and duty < DUTY_MAX:
                duty += DUTY_STEP
                if duty > DUTY_MAX:
                    duty = DUTY_MAX

            pwm.set_duty("pwm1", duty)
            pwm.stop_pwm("pwm2")

            print(
                f"Vin = {1.5 * vin:7.3f} V | "
                f"Vout = {1.5 * vout:7.3f} V | "
                f"Iin = {iin_f:7.3f} A ({iin:7.3f} raw) | "
                f"Iout = {iout_f:7.3f} A ({iout:7.3f} raw) | "
                f"Target = {1.5 * vtarget:7.3f} V | "
                f"Duty = {duty * 100:6.2f}%"
            )
            last_vin = vin
            time.sleep(RAMP_DELAY if vout < vtarget else READ_PERIOD)

    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    finally:
        print("\nshutting down...")

        try:
            while duty > 0:
                duty = max(0.0, duty - DUTY_STEP)
                pwm.set_duty("pwm1", duty)
                time.sleep(0.05)
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
            gpio.deinit()
        except Exception:
            pass

        print("shutdown complete")

    return 0


if __name__ == "__main__":
    sys.exit(main())
