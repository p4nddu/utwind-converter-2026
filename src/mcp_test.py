import time
import signal
import sys

from hal.gpio import PiGpio
from hal.spi import PiSpi
from hal.pwm import PiPwm, PwmConfig
from drivers.mcp3208 import MCP3208
from drivers.si8274 import SI8274

running = True

TARGET_DUTY = 0.50
PWM_FREQ = 10_000

STEP = 0.02
RAMP_DELAY = 0.05
READ_PERIOD = 0.5

# we will keep output side mosfet off for this test
PWM_CHANNEL = "pwm1"
GD_DRIVER = "gd1"


def handle_signal(signum, frame):
    global running
    running = False


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