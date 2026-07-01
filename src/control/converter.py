from dataclasses import dataclass

from hal.gpio import PiGpio
from hal.spi import PiSpi
from hal.pwm import PiPwm, PwmConfig

from drivers.mcp3208 import MCP3208
from drivers.ina229 import INA229, INA229Config
from drivers.si8274 import SI8274

from control.control import (
    ConverterMode,
    ConverterState,
    Measurements,
    build_measurements,
    LowPassFilter,
    Debounce,
    SoftStartController,
    PIController,
    PerturbObserve,
    ModeManager,
    DutyTransition,
    SafetyChecker,
    map_mode_to_duties,
    transition_targets,
)


class ConverterError(RuntimeError):
    pass


@dataclass
class ConverterConfig:
    pwm_freq: int = 300_000
    pi_rate: int = 30_000
    po_rate: int = 1_000

    cut_in_voltage: float = 15.0
    cut_in_debounce_count: int = 20

    startup_steps: int = 1_500
    startup_end_duty: float = 0.25

    pwm_max_duty: float = 0.95


@dataclass
class ConverterStatus:
    state: ConverterState
    mode: ConverterMode
    duty: float
    duty1: float
    duty2: float
    vtarget: float
    fault_reason: str | None


class Converter:
    def __init__(self, config: ConverterConfig = ConverterConfig()):
        self.config = config

        self.gpio = PiGpio()
        self.spi = PiSpi(gpio=self.gpio)
        self.pwm = PiPwm(
            gpio=self.gpio,
            config=PwmConfig(
                frequency_hz=self.config.pwm_freq,
                max_duty=self.config.pwm_max_duty,
            ),
        )

        self.adc = MCP3208(self.spi)
        self.ina = INA229(
            self.spi,
            config=INA229Config(
                rshunt_ohms=0.01,
                max_expected_current=14.0,
                use_low_shunt_range=False,
            ),
        )
        self.gate = SI8274(self.gpio)

        self.vin_filter = LowPassFilter(alpha=0.3)
        self.vout_filter = LowPassFilter(alpha=0.3)

        self.cut_in = Debounce(
            cut_in_voltage=self.config.cut_in_voltage,
            required_count=self.config.cut_in_debounce_count,
        )

        self.soft_start = SoftStartController(
            start_duty=0.0,
            end_duty=self.config.startup_end_duty,
            steps=self.config.startup_steps,
        )

        self.pi = PIController(
            dt=1.0 / self.config.pi_rate
        )

        self.po = PerturbObserve()
        self.mode_manager = ModeManager()
        self.transition = DutyTransition(step=0.03)
        self.safety = SafetyChecker()

        self.state = ConverterState.OFF
        self.mode = ConverterMode.BUCK
        
        self.tick = 0
        self.po_divider = int(self.config.pi_rate / self.config.po_rate)

        self.duty = 0.0
        self.duty1 = 0.0
        self.duty2 = 0.0

        self.vtarget = 0.0
        self.fault_reason = None

        self.last_measurements = Measurements()

    
    def create_hardware(self) -> None:
        """
        hardware objects created in __init__
        """
        if self.state != ConverterState.OFF:
            return
        
    def enter_standby(self) -> None:
        """
        initialized hardware and force safe outputs.
        no switching yet.
        """
        self.gpio.init()
        self.spi.init()
        self.pwm.init()

        self.gate.disable_all()
        self.pwm.stop_pwm("pwm1")
        self.pwm.stop_pwm("pwm2")
        self.force_safe_outputs()

        self.ina.initialize_all_ina(check_id=True)

        self.cut_in.reset()
        self.pi.reset()
        self.soft_start.reset()
        self.mode_manager.reset(ConverterMode.PASS_BUCK)

        self.tick = 0
        self.duty = 0.0
        self.duty1 = 0.0
        self.duty2 = 0.0
        self.vtarget = 0.0
        self.fault_reason = None

        self.state = ConverterState.STANDBY
    
    def start_converter(self) -> None:
        if self.state == ConverterState.OFF:
            self.enter_standby()

        if self.state not in (ConverterState.STANDBY,):
            raise ConverterError(f"cannot start from state {self.state}")
        
        self.soft_start.reset()
        self.pi.reset(0.0)
        self.mode = ConverterMode.BUCK
        self.duty = 0.0

        self.gate.enable("gd1")
        self.gate.disable("gd2")
        self.pwm.set_duty("pwm1", 0.0)
        self.pwm.stop_pwm("pwm2")

        self.state = ConverterState.STARTUP

    def stop_converter(self) -> None:
        if self.state in (ConverterState.OFF, ConverterState.STANDBY,):
            self.force_safe_outputs()
            self.state = ConverterState.STANDBY
            return
        
        self.transition.reset(
            duty1=self.duty1,
            duty2=self.duty2,
            target1=0.0,
            target2=0.0,
        )

        self.state = ConverterState.STOPPING

    def deinit(self) -> None:
        self.force_safe_outputs()

        try:
            self.pwm.deinit()
            self.spi.deinit()
        finally:
            self.gpio.deinit()
        
        self.state = ConverterState.OFF
    
    # -------------- update converter --------------
    
    def update_converter(self) -> ConverterStatus:
        if self.state == ConverterState.OFF:
            return self.get_status()

        try:
            m = self._read_measurements()
            self.last_measurements = m

            if self.state == ConverterState.STANDBY:
                self._update_standby(m)

            elif self.state == ConverterState.STARTUP:
                self._update_startup(m)

            elif self.state == ConverterState.NORMAL:
                self._update_normal(m)

            elif self.state == ConverterState.STOPPING:
                self._update_stopping()

            elif self.state == ConverterState.FAULT:
                self.force_safe_outputs()

            else:
                self.fault_stop(f"unknown converter state: {self.state}")

        except Exception as exc:
            self.fault_stop(str(exc))

        return self.get_status()

    # -------------- update handlers --------------

    def _update_standby(self, m: Measurements) -> None:
        """
        wait for cut in voltage
        """
        self.force_safe_outputs()
        
        if self.cut_in.update(m.vin):
            self.start_converter()

    def _update_startup(self, m: Measurements) -> None:
        reason = self.safety.check(m)
        if reason is not None:
            self.fault_stop(reason)
            return
        
        duty, done = self.soft_start.update()

        self.mode = ConverterMode.BUCK
        self.duty = duty
        self._apply_mode_and_duty(self.mode, self.duty)

        if done:
            # seed filtered values and make vtarget the actual output
            self.vin_filter.reset(m.vin)
            self.vout_filter.reset(m.vout)

            self.vtarget = m.vout
            self.po.reset(self.vtarget)
            self.pi.reset(duty)

            self.mode = ConverterMode.BUCK
            self.mode_manager.reset(self.mode)

            self.duty = self.soft_start.end_duty
            self._apply_mode_and_duty(self.mode, self.duty)

            self.state = ConverterState.NORMAL

    def _update_normal(self, m: Measurements) -> None:
        reason = self.safety.check(m)
        if reason is not None:
            self.fault_stop(reason)
            return
        
        self.tick += 1

        vin_f = self.vin_filter.update(m.vin)
        vout_f = self.vout_filter.update(m.vout)

        if self.tick % self.po_divider == 0:
            self.vtarget =  self.po.update(vin_f, m.iin)
        
        requested_mode = self.mode_manager.update(vin_f, self.vtarget)

        if self.transition.active:
            self.duty1, self.duty2, done = self.transition.update()
            self._apply_raw_duties(self.duty1, self.duty2)
            return

        # if mode changed start a duty transition
        if requested_mode != self.mode:
            target1, target2 = transition_targets(
                mode_from = self.mode,
                mode_to=requested_mode,
                duty = self.duty,
            )

            self.transition.reset(
                duty1=self.duty1,
                duty2=self.duty2,
                target1=target1,
                target2=target2,
            )

            self.mode = requested_mode
            self.duty1, self.duty2, done = self.transition.update()
            self._apply_raw_duties(self.duty1, self.duty2)
            return
        
        # normal PI update
        self.duty = self.pi.update(
            vtarget=self.vtarget,
            vout=vout_f,
            vin=vin_f,
            mode=self.mode,
        )

        self._apply_mode_and_duty(self.mode, self.duty)

    def _update_stopping(self) -> None:
        self.duty1, self.duty2, done = self.transition.update()
        self._apply_raw_duties(self.duty1, self.duty2)

        if done:
            self.force_safe_outputs()
            self.state = ConverterState.STANDBY
    
    # -------------- mode and duty --------------

    def _apply_mode_and_duty(self, mode: ConverterMode, duty: float) -> None:
        duty1, duty2 = map_mode_to_duties(mode, duty)
        self._apply_raw_duties(duty1, duty2)

    def _apply_raw_duties(self, duty1: float, duty2: float) -> None:
        self.duty1 = duty1
        self.duty2 = duty2

        if duty1 > 0:
            self.gate.enable("gd1")
            self.pwm.set_duty("pwm1", duty1)
        else:
            self.pwm.stop_pwm("pwm1")
            self.gate.disable("gd1")
        
        if duty2 > 0:
            self.gate.enable("gd2")
            self.pwm.set_duty("pwm2", duty2)
        else:
            self.pwm.stop_pwm("pwm2")
            self.gate.disable("gd2")
    
    def force_safe_outputs(self) -> None:
        try:
            self.gpio.force_safe_outputs()
        except Exception:
            pass

        self.duty = 0.0
        self.duty1 = 0.0
        self.duty2 = 0.0
    
    # -------------- fault handling --------------

    def fault_stop(self, reason: str) -> None:
        self.fault_reason = reason
        self.force_safe_outputs()
        self.state = ConverterState.FAULT
    
    def clear_fault(self) -> None:
        if self.state != ConverterState.FAULT:
            return
        
        self.fault_reason = None
        self.cut_in.reset()
        self.pi.reset()
        self.soft_start.reset()
        self.mode_manager.reset()
        self.state = ConverterState.STANDBY

    # -------------- measurements / status --------------

    def _read_measurements(self) -> Measurements:
        vin = self.adc.read_vin()
        vout = self.adc.read_vout()
        iin = self.ina.read_ina_in()
        iout = self.ina.read_ina_out()

        return build_measurements(vin, vout, iin, iout)
    
    def get_status(self) -> ConverterStatus:
        return ConverterStatus(
            state=self.state,
            mode=self.mode,
            duty=self.duty,
            duty1=self.duty1,
            duty2=self.duty2,
            vtarget=self.vtarget,
            fault_reason=self.fault_reason,
        )







