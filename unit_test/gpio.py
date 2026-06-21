from dataclasses import dataclass
import time

try:
    import pigpio
except ImportError:
    pigpio = None

class gpioError(RuntimeError):
    """GPIO related errors"""

@dataclass(frozen=True)
class gpioPins():
    """
    note: BCM numbering is used in the code, refer to documentation for translating BCM to physical pin headers when debugging

    we have on a raspberry pi zero 2W:
    - INA229 input current sensor using GPIO 23 as CS (manual)
    - INA229 output current sensor using GPIO 24 as CS (manual)
    - MCP3208 ADC using GPIO 25 as CS (manual)
    - GD_ENABLE1 on GPIO 5 (pin 29) and GD_ENABLE2 on GPIO 6 (pin 31)
    - PWM1 on GPIO 12 (pin 32) and PWM2 on GPIO 13 (pin 33)

    we use pigpio for hardware timed pwm to account for fast switching
    """

    cs_ina_in_bcm: int = 25
    gd_enable_bcm: int = 6

    # these pins are most likely gonna be handled by the built in spi functions so leave as none
    # but i made it either int or none just in case we want to initialize it to something else
    cs_ina_out_bcm: int | None = None
    cs_mcp3208_bcm: int | None = None

    # Note: ina_in, ina_out, and mcp3208 cs pins are all ACTIVE LOW

class piGpio:
    def __init__(self, pins: gpioPins = gpioPins()):
        self.pins = pins
        self._inited = False
        self.pi = None

    def init(self) -> None:
        if self._inited:
            return

        if pigpio is None:
            raise gpioError("pigpio library not found")

        self.pi = pigpio.pi()

        if not self.pi.connected:
            raise gpioError("Could not connet to pigpio daemon")


        # set ina cs pin active low / high in default
        for pin in (self.pins.cs_ina_in_bcm, self.pins.cs_ina_out_bcm, self.pins.cs_mcp3208_bcm):
            if pin is None:
                continue
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 1)  # HIGH

        # set gate driver enable to default low
        self.pi.set_mode(self.pins.gd_enable_bcm, pigpio.OUTPUT)
        self.pi.write(self.pins.gd_enable_bcm, 0)

        self._inited = True
    
    def deinit(self) -> None:
        if not self._inited:
            return

        try:
            if self.pi is not None:
                self.pi.write(self.pins.gd_enable_bcm, 0)
                for pin in (self.pins.cs_ina_in_bcm, self.pins.cs_ina_out_bcm, self.pins.cs_mcp3208_bcm):
                    if pin is None:
                        continue
                    self.pi.write(pin, 1)
        finally:
            if self.pi is not None:
                self.pi.stop()
            self.pi = None
            self._inited = False        

    # ------- consistency checks / helper fucntions -------

    def _require_init(self) -> None:
        if not self._inited:
            raise gpioError("gpio not initialized, must call piGpio.init() first")
    
    def _get_cs_pin(self, name: str) -> int:
        device = name.strip().lower()

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
        self.pi.write(pin, 0)

    def cs_release(self, name: str) -> None:
        self._require_init()
        pin = self._get_cs_pin(name)
        self.pi.write(pin, 1)
    
    # ------- Gate Driver controls -------

    def set_gd_enable(self, enable: bool) -> None:
        self._require_init()
        self.pi.write(self.pins.gd_enable_bcm, 1 if enable else 0)
