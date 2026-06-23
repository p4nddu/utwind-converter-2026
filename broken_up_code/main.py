from __future__ import annotations

import signal
import time

from simple_pwm_sensor_test import SimplePwmSensorTest


def main() -> None:
    test = SimplePwmSensorTest()

    signal.signal(signal.SIGINT, test.request_stop)
    signal.signal(signal.SIGTERM, test.request_stop)

    try:
        test.setup()

        print("\nStarting sensor print loop. Press Ctrl+C to stop.\n")

        test.run_pwm()

        step = 0
        next_time = time.time()

        while test.running:
            try:
                test.run_sensor_read(step)

                step += 1
                next_time += test.spi_cfg.read_period_s

                sleep_s = next_time - time.time()

                if sleep_s > 0:
                    time.sleep(sleep_s)
                else:
                    next_time = time.time()

            except Exception as exc:
                print(f"[ERROR] {exc}")
                time.sleep(0.5)

    finally:
        try:
            target_duty = max(
                0.0,
                min(1.0, test.pwm_cfg.duty_fraction),
            )

            if test.pwm is not None:
                test.pwm.ramp_pwm(
                    target_duty,
                    0.0,
                    1.0,
                    50,
                )

                test.pwm.set_gd_enable(False)

        finally:
            test.cleanup()


if __name__ == "__main__":
    main()



#still need
#ramp up/down for clean shuttoff
#have main pwm controller function that controls which pwm is on and what is being sent to the mosfet
# turn on and off pwm 
# pwm shutdown 

#PO
#set voltage target
#change slightly
#use power functe
#decide if going up or down
#max 41.25
#runs every time 
