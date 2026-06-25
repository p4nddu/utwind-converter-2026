from dataclasses import dataclass

try:
    import pigpio
except ImportError:
    pigpio = None


class GpioError(RuntimeError):
    pass


@dataclass(frozen=True)
class GpioPins:
    """
    Raspberry Pi Zero 2W pinout for load control:

    Manual cs lines, active LOW:
    - INA229_in current sensor CS:     GPIO 23
    - INA229_out current sensor CS:    GPIO 24
    - MCP3208 ADC CS:                  GPIO 25

    Gate driver enable lines:
    - GD_ENABLE1: GPIO 5   physical pin 29
    - GD_ENABLE2: GPIO 6   physical pin 31

    Hardware PWM outputs:
    - PWM1: GPIO 12
    - PWM2: GPIO 13  

    SPI pins:
    - MOSI: GPIO 10
    - MISO: GPIO 9
    - SCLK: GPIO 11

    useful functions:
    init()
    deinit()
    cs_pull()
    cs_release()
    """

    cs_ina_in: int = 25
    cs_ina_out: int = 17
    cs_mcp3208: int = 27

    gd_enable1: int = 6
    gd_enable2: int = 5

    pwm1: int = 12
    pwm2: int = 13


class PiGpio:
    def __init__(self, pins: GpioPins = GpioPins()):
        self.pins = pins
        self._inited = False
        self.pi = None

    def init(self) -> None:
        if self._inited:  # check that it has not been initialized
            return
        
        if pigpio is None:  # check import errors
            raise GpioError("pigpio library not found. check if installed or started")
        
        self.pi = pigpio.pi()

        if not self.pi.connected:
            raise GpioError("failed connecting to pigpio daemon")
        
        # initialize manual cs lines as default HIGH
        for pin in (
            self.pins.cs_ina_in,
            self.pins.cs_ina_out,
            self.pins.cs_mcp3208,
        ):
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 1)
        
        # gate driver disabled as default
        for pin in (
            self.pins.gd_enable1,
            self.pins.gd_enable2,
        ):
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 0)
        
        # pwm pins default off
        for pin in (
            self.pins.pwm1,
            self.pins.pwm2,
        ):
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 0)
        
        self._inited = True
    
    def deinit(self) -> None:
        if not self._inited:
            return
        
        try:
            if self.pi is not None:
                # safe shutdown order: gate driver -> pwm -> cs pins
                self.set_gd_enable("gd1", False)
                self.set_gd_enable("gd2", False)

                for pin in (
                    self.pins.pwm1,
                    self.pins.pwm2,
                ):
                    self.pi.hardware_PWM(pin, 0, 0)
                    self.pi.write(pin, 0)
                
                for pin in (
                    self.pins.cs_ina_in,
                    self.pins.cs_ina_out,
                    self.pins.cs_mcp3208,
                ):
                    self.pi.write(pin, 1)

        finally:
            if self.pi is not None:
                self.pi.stop()
            
            self.pi = None
            self._inited = False


    # -------------- internal helper functions --------------

    def _require_init(self) -> None:
        if not self._inited or self.pi is None:
            raise GpioError("GPIO not initialized. call PiGpio.init() first")
        
    def _get_cs_pin(self, name: str) -> int:
        device = name.strip().lower()

        if device in ("ina_in", "ina229_in", "input_ina"):
            return self.pins.cs_ina_in
        
        if device in ("ina_out", "ina229_out", "output_ina"):
            return self.pins.cs_ina_out
        
        if device in ("mcp", "mcp3208", "adc"):
            return self.pins.cs_mcp3208
        
        raise GpioError("unknown cs device name. use: ina229_in, ina229_out, mcp3208")
    
    def get_gd_enable_pin(self, name: str) -> int:
        driver = name.strip().lower()

        if driver in ("gd1", "gd_enable1"):
            return self.pins.gd_enable1
        
        if driver in ("gd2", "gd_enable2"):
            return self.pins.gd_enable2
        
        raise GpioError("unknown gd name. use: gd_enable1 or gd_enable2")
    
    def get_pwm_pin(self, name: str) -> int:
        channel = name.strip().lower()

        if channel in ("pwm1",):
            return self.pins.pwm1
        
        if channel in ("pwm2",):
            return self.pins.pwm2
        
        raise GpioError("unknown pwm channel. use: pwm1 or pwm2")
    
    def force_safe_outputs(self) -> None:
        self._require_init()

        self.set_gd_enable("gd1", False)
        self.set_gd_enable("gd2", False)

        for pin in (
            self.pins.pwm1,
            self.pins.pwm2,
        ):
            self.pi.hardware_PWM(pin, 0, 0)
            self.pi.write(pin, 0)

        for pin in (
            self.pins.cs_ina_in,
            self.pins.cs_ina_out,
            self.pins.cs_mcp3208,
        ):
            self.pi.write(pin, 1)
    

    # -------------- gate driver helpers --------------

    def set_gd_enable(self, name: str, enable: bool) -> None:
        self._require_init()
        self.pi.write(self.get_gd_enable_pin(name), 1 if enable else 0)


    # -------------- chip select helpers --------------

    def cs_pull(self, name: str) -> None:
        # pulls selected cs pin LOW
        self._require_init()
        self.pi.write(self._get_cs_pin(name), 0)
    
    def cs_release(self, name: str) -> None:
        # releases selected cs pin HIGH
        self._require_init()
        self.pi.write(self._get_cs_pin(name), 1)

    
    

        
