import time
import signal
import sys

from hal.gpio import PiGpio
from hal.spi import PiSpi
from hal.pwm import PiPwm, PwmConfig
from drivers.mcp3208 import MCP3208
from drivers.si8274 import SI8274


TARGET_DUTY = 0.50
PWM_FREQ = 10_000

STEP = 0.02
RAMP_DELAY = 0.05
READ_PERIOD = 0.5

# we will keep output side mosfet off for this test
PWM_CHANNEL = "pwm2"
GD_DRIVER = "gd2"

running = True


def handle_signal(signum, frame):
    global running
    running = False


def ramp_pwm(pwm: PiPwm, channel: str, start: float, target: float) -> None:
    duty = start

    if target > start:
        while duty < target:
            duty = min(duty + STEP, target)
            pwm.set_duty(channel, duty)
            time.sleep(RAMP_DELAY)
    else:
        while duty > target:
            duty = max(duty - STEP, target)
            pwm.set_duty(channel, duty)
            time.sleep(RAMP_DELAY)


def main() -> int:
    global running

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    gpio = PiGpio()
    spi = PiSpi(gpio=gpio)
    pwm = PiPwm(gpio=gpio, config=PwmConfig(frequency_hz=PWM_FREQ))
    adc = MCP3208(spi)
    gate = SI8274(gpio)

    try:
        print("initializing gpio...")
        gpio.init()

        print("initializing spi...")
        spi.init()

        print("initializing pwm...")
        pwm.init()

        print("defaulting to safe outputs...")
        gate.disable("gd1")
        gate.disable("gd2")
        pwm.stop_pwm("pwm1")
        pwm.stop_pwm("pwm2")

        print("READING initial MCP3208 values:")
        vin = adc.read_vin()
        vout = adc.read_vout()
        print(f"Vin = {vin:.3f} V | Vout = {vout:.3f} V")

        print(f"ENABLING gate driver...")
        gate.enable(GD_DRIVER)

        print(f"RAMPING {PWM_CHANNEL} to {TARGET_DUTY * 100:.1f}%")
        ramp_pwm(pwm, PWM_CHANNEL, 0.0, TARGET_DUTY)

        print("starting loop. press Ctrl+C to stop...")
        while running:
            vin = adc.read_vin()
            vout = adc.read_vout()
            
            print(f"Vin = {vin:8.3f} V | Vout = {vout:8.3f} V")
            time.sleep(READ_PERIOD)

    except Exception as exc:
        print(f"ERROR {exc}")
        return 1
    
    finally:
        print("\nSHUTTING DOWN...")

        try:
            ramp_pwm(pwm, PWM_CHANNEL, TARGET_DUTY, 0.0)
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
            gpio.deinit()
        except Exception:
            pass

        print("SHUTDOWN complete")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())