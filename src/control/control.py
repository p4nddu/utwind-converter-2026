from dataclasses import dataclass, field
from enum import IntEnum
import math


class ConverterMode(IntEnum):
    BUCK = 0
    BOOST = 1
    PASS_BUCK = 2
    PASS_BOOST = 3


class ConverterState(IntEnum):
    OFF = 0
    STANDBY = 1
    STARTUP = 2
    NORMAL = 3
    FAULT = 4
    STOPPING = 5


# -------------- Helpers --------------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(x, hi))


def calculate_power(voltage: float, current: float) -> float:
    return voltage * current


@dataclass
class Measurements:
    vin: float = 0.0
    vout: float = 0.0
    iin: float = 0.0
    iout: float = 0.0
    powin: float = 0.0
    powout: float = 0.0


def build_measurements(vin: float, vout: float, iin: float, iout: float) -> Measurements:
    return Measurements(
        vin=vin,
        vout=vout,
        iin=iin,
        iout=iout,
        powin=calculate_power(vin, iin),
        powout=calculate_power(vout, iout),
    )


# -------------- low pass filter --------------

@dataclass
class LowPassFilter:
    alpha: float = 0.3
    value: float = 0.0
    initialized: bool = False

    def reset(self, value: float = 0.0) -> None:
        self.value = value
        self.initialized = True

    def update(self, raw: float) -> float:
        if not self.initialized:
            self.reset(raw)
            return self.value
        
        self.value = (1.0 - self.alpha) * self.value + self.alpha * raw
        return self.value
    

# ------------- debounce --------------

@dataclass
class Debounce:
    cut_in_voltage: float = 15.0
    required_count: int = 20
    count: int = 0

    def reset(self) -> None:
        self.count = 0

    def update(self, vin: float) -> bool:
        if vin >= self.cut_in_voltage:
            self.count += 1
        else:
            self.count = 0
        
        return self.count >= self.required_count
    

# -------------- soft start --------------

@dataclass
class SoftStartController:
    start_duty: float = 0.0
    end_duty: float = 0.40
    steps: int = 1000

    duty: float = 0.0
    step_index: int = 0
    done: bool = False

    def reset(self) -> None:
        self.duty = self.start_duty
        self.step_index = 0
        self.done = False
    
    def update(self) -> tuple[float, bool]:
        if self.done:
            return self.duty, True
        
        if self.steps <= 0:
            self.duty = self.end_duty
            self.done = True
            return self.duty, True
        
        step_size = (self.end_duty - self.start_duty) / self.steps

        self.duty = self.start_duty + step_size * self.step_index
        self.duty = clamp(self.duty, min(self.start_duty, self.end_duty), max(self.start_duty, self.end_duty))

        self.step_index += 1

        if self.step_index > self.steps:
            self.duty = self.end_duty
            self.done = True
        
        return self.duty, self.done


# -------------- PI --------------

@dataclass
class PIController:
    kp: float = 0.01
    ki: float = 0.0
    dt: float = 1.0/30_000.0
    ff_gain: float = 0.2

    duty_min: float = 0.0
    duty_max_buck: float = 0.95
    duty_max_boost: float = 0.40

    max_duty_step: float = 0.01

    integral: float = 0.0
    duty: float = 0.0

    def reset(self, duty: float = 0.0) -> None:
        self.integral = 0.0
        self.duty = duty
    
    def update(self, vtarget: float, vout: float, vin: float, mode: ConverterMode) -> float:
        error = vtarget - vout

        if mode == ConverterMode.BUCK:
            duty_max = self.duty_max_buck
            feedforward = vtarget / max(vin, 1e-6)

        elif mode == ConverterMode.BOOST:
            duty_max = self.duty_max_boost
            feedforward = 1.0 - (vin / max(vtarget, 1e-6))
        
        else:
            return self.duty
        
        feedforward = clamp(feedforward, self.duty_min, duty_max)

        u_unsat = self.ff_gain * feedforward + self.kp * error + self.integral
        u_sat = clamp(u_unsat, self.duty_min, duty_max)

        # anti windup
        if (
            u_unsat == u_sat
            or (u_sat >= duty_max and error < 0)
            or (u_sat <= self.duty_min and error > 0)
        ):
            self.integral += self.ki * error * self.dt
        
        u_unsat = self.ff_gain * feedforward + self.kp * error + self.integral
        u_sat = clamp(u_unsat, self.duty_min, duty_max)

        delta = clamp(u_sat - self.duty, -self.max_duty_step, self.max_duty_step)
        self.duty = clamp(self.duty + delta, self.duty_min, duty_max)

        return self.duty


# -------------- P&O --------------

@dataclass
class PerturbObserve:
    step_v: float = 1
    vtarget_min: float = 15.0
    vtarget_max: float = 48

    vtarget: float = 0.0
    prev_power: float = 0.0
    direction: float = 1.0
    initialized: bool = False

    def reset(self, initial_vtarget: float) -> None:
        self.vtarget = initial_vtarget
        self.prev_power = 0.0
        self.direction = 1.0
        self.initialized = True
    
    def update(self, vin: float, iin: float) -> float:
        power = calculate_power(vin, iin)

        if not self.initialized:
            self.reset(vin)
            self.prev_power = power
            return self.vtarget
        
        if power < self.prev_power:
            self.direction *= -1.0

        self.vtarget += self.direction * self.step_v
        self.vtarget = clamp(self.vtarget, self.vtarget_min, self.vtarget_max)

        self.prev_power = power
        return self.vtarget
    

# -------------- mode manager --------------

@dataclass
class ModeManager:
    margin_v: float = 1.5
    mode: ConverterMode = ConverterMode.PASS_BUCK

    def reset(self, mode: ConverterMode = ConverterMode.PASS_BUCK) -> None:
        self.mode = mode

    def update(self, vin: float, vtarget: float) -> ConverterMode:
        if abs(vin-vtarget) <= self.margin_v:
            if self.mode in (ConverterMode.BUCK, ConverterMode.PASS_BUCK):
                self.mode = ConverterMode.PASS_BUCK
            else:
                self.mode = ConverterMode.PASS_BOOST

        elif vin > vtarget + self.margin_v:
            self.mode = ConverterMode.BUCK
        
        elif vin < vtarget - self.margin_v:
            self.mode = ConverterMode.BOOST

        return self.mode


# -------------- duty transitions --------------

@dataclass
class DutyTransition:
    step: float = 0.03
    active: bool = False
    duty1: float = 0.0
    duty2: float = 0.0
    target1: float = 0.0
    target2: float = 0.0

    def reset(self, duty1: float, duty2: float, target1: float, target2: float) -> None:
        self.duty1 = duty1
        self.duty2 = duty2
        self.target1 = target1
        self.target2 = target2
        self.active = True
    
    def update(self) -> tuple[float, float, bool]:
        if not self.active:
            return self.duty1, self.duty2, True
        
        self.duty1 = self._step_toward(self.duty1, self.target1)
        self.duty2 = self._step_toward(self.duty2, self.target2)

        done = (
            abs(self.duty1 - self.target1) < 1e-9
            and abs(self.duty2 - self.target2) < 1e-9
        )

        if done:
            self.active = False
        
        return self.duty1, self.duty2, done
    
    def _step_toward(self, current: float, target: float) -> float:
        if current < target:
            return min(current + self.step, target)
        if current > target:
            return max(current - self.step, target)
        return current


# -------------- duty mapping --------------

def map_mode_to_duties(mode: ConverterMode, duty: float) -> tuple[float, float]:
    if mode == ConverterMode.BUCK:
        return duty, 0.0
    
    if mode == ConverterMode.BOOST:
        return 0.85, duty
    
    if mode in (ConverterMode.PASS_BUCK, ConverterMode.PASS_BOOST):
        return 0.85, 0.0
    
    return 0.0, 0.0

def transition_targets(mode_from: ConverterMode, mode_to: ConverterMode, duty: float) -> tuple[float, float]:
    if mode_to == ConverterMode.BUCK:
        return duty, 0.0
    
    if mode_to == ConverterMode.BOOST:
        return 0.85, duty
    
    if mode_to in (ConverterMode.PASS_BUCK, ConverterMode.PASS_BOOST):
        return 0.85, 0.0
    
    return 0.0, 0.0


# -------------- safety limiter --------------

@dataclass
class SafetyLimits:
    vin_min: float = 0.0
    vin_max: float = 60
    vout_max: float = 60
    iin_max: float = 11.0
    iout_max: float = 11.0


@dataclass
class SafetyChecker:
    limits: SafetyLimits = field(default_factory=SafetyLimits)

    def check(self, m: Measurements) -> str | None:
        values = [m.vin, m.vout, m.iin, m.iout, m.powin, m.powout]

        for value in values:
            if not math.isfinite(value):
                return "non finite sensor value"
        
        if m.vin < self.limits.vin_min:
            return f"input undervoltage: {m.vin:.3f} V"

        if m.vin > self.limits.vin_max:
            return f"input overvoltage: {m.vin:.3f} V"

        if m.vout > self.limits.vout_max:
            return f"output overvoltage: {m.vout:.3f} V"

        if abs(m.iin) > self.limits.iin_max:
            return f"input overcurrent: {m.iin:.3f} A"

        if abs(m.iout) > self.limits.iout_max:
            return f"output overcurrent: {m.iout:.3f} A"

        return None
