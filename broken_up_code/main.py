from __future__ import annotations

import signal
import time

from simple_pwm_sensor_test import SimplePwmSensorTest
from pwm_control import PwmController

def main() -> None:
    test = SimplePwmSensorTest()
    pwm = PwmController()


    signal.signal(signal.SIGINT, test.request_stop)
    signal.signal(signal.SIGTERM, test.request_stop)

    try:
        test.setup()

        print("\nStarting sensor print loop. Press Ctrl+C to stop.\n")

        test.run_pwm()

        pwm.init_pwm()
        current_pwm1_duty = 0.0
        current_pwm2_duty = 0.0

        previous_power = 0.0
        previous_voltage = test.read_scaled_voltage(test.sensor_cfg.vout_channel)

        vref = 15.0
        direction = 1.0  # IMPORTANT: MPPT search direction

        step = 0
        next_time = time.time()
        vin = 0.0

        #measure vout and start loop only when vin is 15
        while vin <= 15.0:
            vin = test.read_scaled_voltage(test.sensor_cfg.vin_channel)

        vref = vin

        while test.running:
            try:
                test.run_sensor_read(step)

                vout = test.read_scaled_voltage(test.sensor_cfg.vout_channel)
                iout = test.read_current("ina_out")

                current_power = test.calculate_power(vout, iout)

                # --- PI LOOP ---
                current_pwm1_duty = test.pi_voltage_control(
                    vout=vout,
                    vref=vref,
                    dt=test.spi_cfg.read_period_s,
                )

                pwm.set_pwm_duty(current_pwm1_duty)

                # --- PO LOOP (now true MPPT) ---
                if step % 10 == 0:
                    vref, previous_power, previous_voltage, direction = test.run_po_loop(
                        previous_power=previous_power,
                        previous_voltage=previous_voltage,
                        vref=vref,
                        direction=direction,
                    )

                step += 1
                next_time += test.spi_cfg.read_period_s

                sleep_s = next_time - time.time()
                if sleep_s > 0:
                    time.sleep(sleep_s)
                else:
                    next_time = time.time()

            except Exception as exc:
                print(f"[ERROR] {exc}")

    finally:
        try:

            
            target_duty = max(
                0.0,
                min(1.0, test.pwm_cfg.duty_fraction),
            )

            test.pwm.set_gd_enable(False)
            test.pwm.set_gd_enable2(False)


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
