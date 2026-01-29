import time
from controller import PIController, get_voltage_target
import hardware as helpers


def main():
    Vref = 10.0      # target Vout
    Ts   = 0.001     # 1 ms loop time → 1 kHz
    ctrl = PIController(Kp=0.35, Ki=0.01)

    #start: 4m/s max: 12-13m/s incr: 0.5
    
    wind_voltage_table = [
        (5.0,  50.0),
        (10.0, 55.0),
        (15.0, 60.0),
        (20.0, 65.0),
    ]


    # Initialize ADC & PWM
    helpers.init_adc(bus=0, device=0, max_speed_hz=100000)
    helpers.init_pwm(pin=12, freq_hz=int(1/Ts))

    next_time = time.time()
    step = 0

    try:
        while True:
            #Read Vout (channel 0)
            Vout = helpers.read_voltage(channel=0,vref=5)
            

            
            wind_speed = 10.0 # needs to change based on the real wind
             
            Vref = get_voltage_target(wind_speed, wind_voltage_table)

            #Compute new duty
            duty = ctrl.update(Vref, Vout, Ts)
            
            # Drive MOSFET
            helpers.set_duty_cycle(duty)

            print(f"Step {step:4d} | Vout = {Vout:6.2f} V | Duty = {duty*100:5.1f}%")
            step += 1

            # Wait for next period
            next_time += Ts
            sleep = next_time - time.time()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_time = time.time()

    except KeyboardInterrupt:
        print("Stopping…")
    finally:
        helpers.shutdown()

if __name__ == "__main__":
    main()

