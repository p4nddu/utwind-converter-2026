from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SensorReading:
    current_in: float
    current_out: float
    vin: float
    vout: float


class SensorReader:
    def __init__(self, parent):
        self.parent = parent

    def run(self) -> SensorReading:
        return SensorReading(
            current_in=self.parent.read_current("ina_in"),
            current_out=self.parent.read_current("ina_out"),
            vin=self.parent.read_scaled_voltage(
                self.parent.sensor_cfg.vin_channel
            ),
            vout=self.parent.read_scaled_voltage(
                self.parent.sensor_cfg.vout_channel
            ),
        )