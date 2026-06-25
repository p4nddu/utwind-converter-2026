import time
import signal
import sys

from hal.gpio import PiGpio
from hal.spi import PiSpi
from drivers.ina229 import INA229, INA229Config


READ_PERIOD = 0.5

running = True


def handle_signal(signum, frame):
    global running
    running = False


def main() -> int:
    global running
    
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    gpio = PiGpio()
    spi = PiSpi(gpio=gpio)

    ina = INA229(
        spi=spi,
        config=INA229Config(
            rshunt_ohms = 0.01,
            max_expected_current=2.0,
            use_low_shunt_range=True,
        ),
    )

    try:
        print("initializing gpio...")
        gpio.init()

        print("initializing spi")
        spi.init()

        print("reading INA229 ids...")
        for sensor in ("ina_in", "ina_out"):
            man_id, dev_id = ina.read_ids_ina(sensor)
            print(
                f"{sensor}: manufacturer=0x{man_id:04X}, "
                f"device=0x{dev_id:04X}"
            )
        
        print("initializing INA sensors...")
        ina.initialize_all_ina(check_id=True)

        print("reading current. press Ctrl+C to stop...")
        while running:
            iin = ina.read_ina_in()
            iout = ina.read_ina_out()

            vshunt_in = ina.read_vshunt("ina_in")
            vshunt_out = ina.read_vshunt("ina_out")

            print(
                f"Iin = {iin:+9.6f} A | "
                f"Iout = {iout:+9.6f} A | "
                f"Vshunt_in = {vshunt_in * 1e3:+9.3f} mV | "
                f"Vshunt_out = {vshunt_out * 1e3:+9.3f} mV"
            )

            time.sleep(READ_PERIOD)

    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    
    finally:
        print("\nshutting down...")

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