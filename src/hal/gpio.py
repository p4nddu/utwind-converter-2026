from dataclasses import dataclass
import time

import RPi.GPIO as gpio

class gpioError(RuntimeError):
    """Errors during init or use"""

@dataclass(frozen=True)
class gpioPins():
    """
    Note: BCM numbering is used in the code, refer to docu for translating BCM to physical pin headers

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

    def init(self):
        if self._inited:
            return
        
        gpio.setwarnings(False)
        gpio.setmode(gpio.BCM)

        "set ina cs pin active low / high in default"
        gpio.setup(self.pins.cs_ina_in_bcm, gpio.OUT, initial=gpio.HIGH)

        "set gate driver enable to default low"
        gpio,setup(self.pins.gd_enable_bcm, gpio.OUT, initial=gpio.LOW)

        self._inited = True
    

