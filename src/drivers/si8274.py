from hal.gpio import PiGpio


class SI8274Error(RuntimeError):
    pass

class SI8274:
    """
    SI8274 gate-driver enable control.

    - gd1 = input-side / buck MOSFET
    - gd2 = output-side / boost MOSFET
    """

    def __init__(self, gpio: PiGpio):
        self.gpio = gpio
        

    # -------------- gate driver control -------------

    def enable(self, driver: str) -> None:
        self._require_gpio()
        self.gpio.set_gd_enable(driver, True)

    def disable(self, driver:str) -> None:
        self._require_gpio()
        self.gpio.set_gd_enable(driver, False)
    
    def enable_all(self) -> None:
        self.enable("gd1")
        self.enable("gd2")
    
    def disable_all(self) -> None:
        self.disable("gd1")
        self.disable("gd2")


    # ------------- helper functions -------------

    def _require_gpio(self) -> None:
        if self.gpio is None or self.gpio.pi is None:
            raise SI8274Error("pigpio is not initialized")

