from dataclasses import dataclass
import time

from hal.spi import PiSpi


class INA229Error(RuntimeError):
   pass


# -------------- INA229 registers --------------
REG_CONFIG = 0x00
REG_ADC_CONFIG = 0x01
REG_SHUNT_CAL = 0x02
REG_VSHUNT = 0x04
REG_VBUS = 0x05
REG_DIETEMP = 0x06
REG_CURRENT = 0x07
REG_POWER = 0x08
REG_DIAG_ALRT = 0x0B
REG_MANUFACTURER_ID = 0x3E
REG_DEVICE_ID = 0x3F


def sign_extend(value: int, bits:int) -> int:
   sign_bit = 1 << (bits-1)
   return (value ^ sign_bit) - sign_bit


@dataclass(frozen=True)
class INA229Config:
   rshunt_ohms: float = 0.01
   max_expected_current: float = 11

   use_low_shunt_range: bool = False

   avg_code: int = 0b010
   vshct_code: int = 0b101
   vbusct_code: int = 0b101
   vtct_code: int = 0b101

   mode_continuous_shunt_only: int = 0xA

   expected_manufacturer_id: int = 0x5449
   expected_device_id: int = 0x2291


class INA229:
   def __init__(self, spi: PiSpi, config: INA229Config = INA229Config()):
      self.spi = spi
      self.config = config

      self.current_lsb = self.config.max_expected_current / (2 ** 19)
      self.shunt_cal = self._compute_shunt_cal()
   

   # -------------- helpers --------------

   def _compute_shunt_cal(self) -> int:
      shunt_cal = (13107.2e6 * self.current_lsb * self.config.rshunt_ohms)

      if self.config.use_low_shunt_range:
         shunt_cal *= 4.0

      return int(round(shunt_cal)) & 0x7FFF
   
   def _transfer(self, sensor: str, tx: bytes | bytearray) -> bytes:
      name = sensor.strip().lower()

      if name in ("ina_in", "ina229_in", "input"):
         return self.spi.transfer_ina_in(tx)
      
      if name in ("ina_out", "ina229_out", "output"):
         return self.spi.transfer_ina_out(tx)

      raise INA229Error("unknown sensor: use ina_in or ina_out")
   

   # -------------- register access --------------

   def read_reg(self, sensor: str, reg_addr: int, num_bytes: int) -> int:
      cmd = ((reg_addr & 0x3F) << 2) | 0x01
      tx = bytes([cmd] + [0x00] * num_bytes)
      rx = self._transfer(sensor, tx)

      data = 0
      for b in rx[1:]:
         data = (data << 8) | b
      
      return data
   
   def write_reg(self, sensor: str, reg_addr: int, value: int, num_bytes: int) -> None:
      cmd = ((reg_addr & 0x3F) << 2) | 0x00
      tx = [cmd]

      for shift in range(8 * (num_bytes - 1), -1, -8):
         tx.append((value >> shift) & 0xFF)
      
      self._transfer(sensor, bytes(tx))
   

   # -------------- sensor reads --------------

   def read_current(self, sensor: str) -> float:
      raw24 = self.read_reg(sensor, REG_CURRENT, 3)

      raw20 = (raw24 >> 4) & 0xFFFFF
      raw_signed = sign_extend(raw20,20)

      return raw_signed * self.current_lsb
   
   def read_ina_in(self) -> float:
      return self.read_current("ina_in")
   
   def read_ina_out(self) -> float:
      return self.read_current("ina_out")
   
   def read_vshunt(self, sensor: str) -> float:
      raw24 = self.read_reg(sensor, REG_VSHUNT, 3)

      raw20 = (raw24 >> 4) & 0xFFFFF
      raw_signed = sign_extend(raw20, 20)

      lsb = 78.125e-9 if self.config.use_low_shunt_range else 312.5e-9
      return raw_signed * lsb

   def read_ids_ina(self, sensor: str) -> tuple[int, int]:
      man_id = self.read_reg(sensor, REG_MANUFACTURER_ID, 2)
      dev_id = self.read_reg(sensor, REG_DEVICE_ID, 2)

      return man_id, dev_id
   

   # -------------- utility --------------

   def reset_ina(self, sensor: str) -> None:
      self.write_reg(sensor, REG_CONFIG, 0x8000, 2)
      time.sleep(0.010)

   def configure_ina(self, sensor: str) -> None:
      config_reg = 0x0010 if self.config.use_low_shunt_range else 0x0000
      self.write_reg(sensor, REG_CONFIG, config_reg, 2)

      adc_config = (
         (self.config.mode_continuous_shunt_only << 12)
         | (self.config.vbusct_code << 9)
         | (self.config.vshct_code << 6)
         | (self.config.vtct_code << 3)
         | (self.config.avg_code)
      )

      self.write_reg(sensor, REG_ADC_CONFIG, adc_config, 2)
      self.write_reg(sensor, REG_SHUNT_CAL, self.shunt_cal, 2)

      time.sleep(0.050)

   def check_ids_ina(self, sensor: str) -> bool:
      man_id, dev_id = self.read_ids_ina(sensor)

      return (
         man_id == self.config.expected_manufacturer_id
         and dev_id == self.config.expected_device_id
      )
   
   def initialize_ina(self, sensor: str, check_id: bool = True) -> None:
      self.reset_ina(sensor)

      if check_id and not self.check_ids_ina(sensor):
         manufacturer_id, device_id = self.read_ids_ina(sensor)
         raise INA229Error(
            f"{sensor} id check failed: "
            f"manufacturer=0x{manufacturer_id:04X}, "
            f"device=0x{device_id:04X}"
         )
      
      self.configure_ina(sensor)

   def initialize_all_ina(self, check_id: bool = True) -> None:
      self.initialize_ina("ina_in", check_id=check_id)
      self.initialize_ina("ina_out", check_id=check_id)
   