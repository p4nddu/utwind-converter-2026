from dataclasses import dataclass
from mcp3208 import MCP3208
import pigpio 
import time


class Buck:

    #def __init__(self, spi: rpiSpi = rpiSpi(), vref: float = 3.3):
        
    #def deinit(self) -> None:


    def softstart(self, 
                  Vtarget: float = 10.0,     # target Vout
                  Ts: float = 0.001,         # 1 ms loop time → 1 kHz
                  ramp_rate: float = 10      # the voltage amount to increase in 1 second
                ) -> None:
        
        #configure coefficients
        max_duty = 0.90     # max duty allowed
        Kp       = 0.05     # can be tuned by testing on the buck
        Ki       = 5.0
        tolerance = 0.2     #tolerance for the final Vout value
        


        #use these values if we want to account for the existing voltage
        Vref = mcp.read_voltage(channel=1)
        duty = Vout/Vtarget
        integral = duty/Ki

        #but we initialize to these for now
        Vref = 0.0
        integral = 0.0
        duty = 0.0
        
        
        softstart_complete = False
        Time = time.time()

        while not softstart_complete:

            # ramp reference
            if Vref < Vtarget:
                Vref += ramp_rate * Ts
                if Vref > Vtarget:
                    Vref = Vtarget

            # read voltage using the mcp3208 class from channel CH1 
            mcp = MCP3208()
            Vout = mcp.read_voltage(channel=1)

            # compute the error
            error = Vref - Vout

            # integral to represent the non-oscilliating component of duty
            if 0.0 < duty < max_duty:
                integral += error * Ts

            duty = Kp * error + Ki * integral

            # clamp duty
            if duty > max_duty:
                duty = max_duty
            elif duty < 0.0:
                duty = 0.0

            # set pwm using the PWM library (not yet written), I am using pigpio for now
            pi = pigpio.pi()
            pi.hardware_PWM(18, 100000, duty)

            if (Vref >= Vtarget and
                abs(Vout - Vtarget) < tolerance * Vtarget):
                startup_complete = True

            # sleep for the rest of the cycle
            Time += Ts
            time.sleep(Time - time.time())

        return