from dataclasses import dataclass

from .gpio import PiGpio


class PwmError(RuntimeError):
    pass


@dataclass(frozen=True)
class PwmConfig:
    frequency_hz: int = 100_000

    min_duty: float = 0.0
    max_duty: float = 0.95


class PiPwm:
    def __init__(self, gpio: PiGpio, config: PwmConfig = PwmConfig()):
        self.gpio = gpio
        self.config = config

        self._inited = False
        self._duty_pwm1 = 0.0
        self._duty_pwm2 = 0.0

    def init(self) -> None:
        if self.gpio is None or self.gpio.pi is None:
            raise PwmError("pigpio is not initialized")
        
        if self._inited:
            return
        
        self.stop_pwm("pwm1")
        self.stop_pwm("pwm2")

        self._inited = True
    
    def deinit(self) -> None:
        if not self._inited:
            return
        
        self.stop_pwm("pwm1")
        self.stop_pwm("pwm2")

        self._inited = False
    

    # -------------- internal helpers --------------
        
    def _require_init(self) -> None:
        if not self._inited:
            raise PwmError("pwm not initialized. use init()")
    
    def _clamp_duty(self, duty: float) -> float:
        if duty < self.config.min_duty:
            return self.config.min_duty
        
        if duty > self.config.max_duty:
            return self.config.max_duty
        
        return duty
    
    @staticmethod
    def _to_pigpio_duty(duty: float) -> int:
        return int(round(duty * 1_000_000))
    

    # -------------- public pwm functions --------------\

    def set_duty(self, name: str, duty: float) -> None:
        self._require_init()

        duty = float(duty)
        duty = self._clamp_duty(duty)

        pin = self.gpio.get_pwm_pin(name)
        pigpio_duty = self._to_pigpio_duty(duty)

        self.gpio.pi.hardware_PWM(
            pin,
            self.config.frequency_hz,
            pigpio_duty,
        )

        channel = name.strip().lower()
        if channel in ("pwm1",):
            self._duty_pwm1 = duty
        if channel in ("pwm2",):
            self._duty_pwm2 = duty
        else:
            raise PwmError("unknown pwm channel: use pwm1 or pwm2")

    def stop_pwm(self, name: str) -> None:
        self._require_init()

        pin = self.gpio.get_pwm_pin(name)
        self.gpio.pi.hardware_PWM(pin, 0, 0)
        self.gpio.pi.write(pin, 0)

        channel = name.strip().lower()
        if channel in ("pwm1",):
            self._duty_pwm1 = 0.0
        if channel in ("pwm2",):
            self._duty_pwm2 = 0.0
        else:
            raise PwmError("unknown pwm channel: use pwm1 or pwm2")
        


