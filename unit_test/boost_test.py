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

VIN_EXPECTED = 10.0
VTARGET = 15.0

PASS_DUTY = 0.85
BOOST_DUTY_START = 0.00
BOOST_DUTY_MIN = 0.00
BOOST_DUTY_MAX = 0.35

LOOP_PERIOD = 0.02
LOG_PERIOD = 0.25
MAX_DUTY_STEP = 0.002
VOLTAGE_KP = 0.01
FILTER_ALPHA = 0.25

VIN_MIN_TO_RUN = 8.0
VIN_MAX_TO_RUN = 12.0
PASS_FROM_BOOST_MARGIN = 0.30
VOUT_MARGIN = 0.10
MAX_VOUT_ABS = 16.5
MAX_CURRENT_ABS = 1.0
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


def boost_duty_delta(vout: float, vtarget: float) -> float:
    error = vtarget - vout

    if abs(error) <= VOUT_MARGIN:
        return 0.0

    return VOLTAGE_KP * error


def choose_mode(vin: float, vtarget: float) -> str:
    if vtarget <= vin + PASS_FROM_BOOST_MARGIN:
        return "PASS_FROM_BOOST"

    return "BOOST"


def apply_outputs(pwm: PiPwm, gate: SI8274, mode: str, boost_duty: float) -> None:
    gate.enable("gd1")
    pwm.set_duty("pwm1", PASS_DUTY)

    if mode == "PASS_FROM_BOOST":
        pwm.stop_pwm("pwm2")
        gate.disable("gd2")
        return

    if mode == "BOOST":
        gate.enable("gd2")
        pwm.set_duty("pwm2", boost_duty)
        return

    raise RuntimeError(f"unknown mode: {mode}")


def check_safety(vin: float, vout: float, iin: float, iout: float) -> None:
    if vin < VIN_MIN_TO_RUN:
        raise RuntimeError(f"Vin too low: {vin:.3f} V")

    if vin > VIN_MAX_TO_RUN:
        raise RuntimeError(f"Vin too high: {vin:.3f} V")

    if vout > MAX_VOUT_ABS:
        raise RuntimeError(f"Vout overvoltage: {vout:.3f} V")

    if abs(iin) > MAX_CURRENT_ABS:
        raise RuntimeError(f"Input overcurrent: {iin:.3f} A")

    if abs(iout) > MAX_CURRENT_ABS:
        raise RuntimeError(f"Output overcurrent: {iout:.3f} A")


def main() -> int:
    global running

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    gpio = PiGpio()
    spi = PiSpi(gpio=gpio)
    pwm = PiPwm(gpio=gpio, config=PwmConfig(frequency_hz=PWM_FREQ, max_duty=PASS_DUTY))
    adc = MCP3208(spi)
    ina = INA229(
        spi,
        INA229Config(
            rshunt_ohms=0.01,
            max_expected_current=MAX_CURRENT_ABS,
        ),
    )
    gate = SI8274(gpio)

    boost_duty = BOOST_DUTY_START
    vin_f = 0.0
    vout_f = 0.0

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

        print(
            f"Initial Vin = {vin:.3f} V | "
            f"Vout = {vout:.3f} V | "
            f"Iin = {iin:.3f} A | "
            f"Iout = {iout:.3f} A"
        )
        print(f"Expected Vin ~= {VIN_EXPECTED:.3f} V | Target Vout = {VTARGET:.3f} V")

        check_safety(vin_f, vout_f, iin, iout)

        print("starting boost test: PWM1 pass duty, PWM2 boost duty")
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

            check_safety(vin_f, vout_f, iin, iout)

            mode = choose_mode(vin_f, VTARGET)
            if mode == "BOOST":
                desired_duty = boost_duty + boost_duty_delta(vout_f, VTARGET)
                desired_duty = clamp(desired_duty, BOOST_DUTY_MIN, BOOST_DUTY_MAX)
                boost_duty = step_toward(boost_duty, desired_duty, MAX_DUTY_STEP)
            else:
                desired_duty = 0.0
                boost_duty = step_toward(boost_duty, 0.0, MAX_DUTY_STEP)

            apply_outputs(pwm, gate, mode, boost_duty)

            if now >= next_log:
                print(
                    f"mode={mode:15s} | "
                    f"Vin={vin_f:7.3f} V | "
                    f"Vout={vout_f:7.3f} V | "
                    f"Iin={iin:7.3f} A | "
                    f"Iout={iout:7.3f} A | "
                    f"Target={VTARGET:7.3f} V | "
                    f"Cmd={desired_duty * 100:6.2f}% | "
                    f"BoostDuty={boost_duty * 100:6.2f}% | "
                    f"PassDuty={PASS_DUTY * 100:6.2f}%"
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
            while boost_duty > 0:
                boost_duty = max(0.0, boost_duty - MAX_DUTY_STEP)
                pwm.set_duty("pwm2", boost_duty)
                time.sleep(LOOP_PERIOD)
        except Exception:
            pass

        try:
            pwm.stop_pwm("pwm2")
            gate.disable("gd2")
        except Exception:
            pass

        try:
            pwm.stop_pwm("pwm1")
            gate.disable("gd1")
        except Exception:
            pass

        try:
            pwm.deinit()
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
