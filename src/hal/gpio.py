from dataclasses import dataclass
import time

import RPi.GPIO as gpio

class gpioError(RuntimeError):
    """errors during init or use"""

@dataclass(frozen=True)
class gpioPins():
    """
    note: BCM numbering is used in the code, refer to docu for translating BCM to physical pin headers

    we have:
    - INA229 input current sensor using physical pin 22 as CS
    - MCP3208 ADC using default ce0 / physical pin 24 as CS (handled by spidev)
    - GD_ENABLE on physical pin 31
    - PWM on physical pin 32
    """

    cs_ina_in_bcm = 25
    gd_enable_bcm = 6

class rpiGpio:
    def __init__(self, pins: gpioPins = gpioPins()):
        self.pins = pins
        self._inited = False

    def init(self) -> None:
        if self._inited:
            return
        
        gpio.setwarnings(False)
        gpio.setmode(gpio.BCM)

        # set ina cs pin active low / high in default
        gpio.setup(self.pins.cs_ina_in_bcm, gpio.OUT, initial=gpio.HIGH)

        # set gate driver enable to default low
        gpio.setup(self.pins.gd_enable_bcm, gpio.OUT, initial=gpio.LOW)

        self._inited = True
    
    def deinit(self) -> None:
        if not self._inited:
            return
        
        try:
            gpio.output(self.pins.gd_enable_bcm, gpio.LOW)
            gpio.output(self.pins.cs_ina_in_bcm, gpio.HIGH)
        finally:
            gpio.cleanup()
            self._inited = False

    # ------- consistency checks -------

    def _require_init(self) -> None:
        if not self._inited:
            raise gpioError("gpio not initialized, must call rpiGpio.init() first")
    
    # ------- INA229 IN cs controls -------

    def cs_ina_in_pull(self) -> None:
        self._require_init()
        gpio.output(self.pins.cs_ina_in_bcm, gpio.LOW)

    def cs_ina_in_release(self) -> None:
        self._require_init()
        gpio.output(self.pins.cs_ina_in_bcm, gpio.HIGH)
    
    # ------- Gate Driver controls -------

    def set_gd_enable(self, enable: bool) -> None:
        self._require_init()
        gpio.output(self.pins.gd_enable_bcm, gpio, gpio.HIGH if enable else gpio.LOW)

if __name__ == "__main__":
    g = rpiGpio()
    g.init()
    try:
        g.cs_ina_in_pull()
        time.sleep(1e-6)
        g.cs_ina_in_release()
        print("INA229 CS test successful")
    finally:
        g.deinit()

