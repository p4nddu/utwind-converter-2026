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
            self.pi.write(self.pins.gd_enable_bcm, 0)
            for pin in (self.pins.cs_ina_in_bcm, self.pins.cs_ina_out_bcm, self.pins.cs_mcp3208_bcm):
                if pin is None:
                    continue
                self.pi.write(pin, 1)
        finally:
            self.pi.stop()
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
