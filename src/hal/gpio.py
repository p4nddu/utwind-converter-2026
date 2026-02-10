from dataclasses import dataclass
import time

import RPi.GPIO as gpio

class gpioError(RuntimeError):
    """GPIO related errors"""

@dataclass(frozen=True)
class gpioPins():
    """
    note: BCM numbering is used in the code, refer to docu for translating BCM to physical pin headers

    we have:
    - INA229 input current sensor using physical pin 22 as CS
    - INA229 output current sensor using default ce1 / physical pin 26 (handeld by spidev)
    - MCP3208 ADC using default ce0 / physical pin 24 as CS (handled by spidev)
    - GD_ENABLE on physical pin 31
    - PWM on physical pin 32
    """

    cs_ina_in_bcm: int = 25
    gd_enable_bcm: int = 6

    # these pins are most likely gonna be handled by the built in spi functions so leave as none
    # but i made it either int or none just in case we want to initialize it to something else
    cs_ina_out_bcm: int | None = None
    cs_mcp3208_bcm: int | None = None

    # NOTE: ina_in, ina_out, and mcp3208 cs pins are all ACTIVE LOW

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
        for pin in (self.pins.cs_ina_in_bcm, self.pins.cs_ina_out_bcm, self.pins.cs_mcp3208_bcm):
            if pin is None:
                continue
            gpio.setup(pin, gpio.OUT, initial=gpio.HIGH)

        # set gate driver enable to default low
        gpio.setup(self.pins.gd_enable_bcm, gpio.OUT, initial=gpio.LOW)

        self._inited = True
    
    def deinit(self) -> None:
        if not self._inited:
            return
        
        try:
            gpio.output(self.pins.gd_enable_bcm, gpio.LOW)
            for pin in (self.pins.cs_ina_in_bcm, self.pins.cs_ina_out_bcm, self.pins.cs_mcp3208_bcm):
                if pin is None:
                    continue
                gpio.output(pin, gpio.HIGH)
        finally:
            gpio.cleanup() # unsure as this resets ALL channels - some process could be using one
            self._inited = False

    # ------- consistency checks / helper fucntions -------

    def _require_init(self) -> None:
        if not self._inited:
            raise gpioError("gpio not initialized, must call rpiGpio.init() first")
    
    def _get_cs_pin(self, name: str) -> int:
        device = name.strip().lower

        if device == "ina_in":
            return self.pins.cs_ina_in_bcm
        if device == "ina_out":
            if self.pins.cs_ina_out_bcm is None:
                raise gpioError("cs_ina_out_bcm is not configured")
            return self.pins.cs_ina_out_bcm
        if device == "mcp3208":
            if self.pins.cs_mcp3208_bcm is None:
                raise gpioError("cs_mcp3208_bcm is not configured")
            return self.pins.cs_mcp3208_bcm
        
        raise gpioError("unknown device name - use: ina_in, ina_out or mcp3208")
    
    # ------- INA229 IN cs controls -------

    def cs_pull(self, name: str) -> None:
        self._require_init()
        pin = self._get_cs_pin(name)
        gpio.output(pin, gpio.LOW)

    def cs_release(self, name: str) -> None:
        self._require_init()
        pin = self._get_cs_pin(name)
        gpio.output(pin, gpio.HIGH)
    
    # ------- Gate Driver controls -------

    def set_gd_enable(self, enable: bool) -> None:
        self._require_init()
        gpio.output(self.pins.gd_enable_bcm, gpio.HIGH if enable else gpio.LOW)
