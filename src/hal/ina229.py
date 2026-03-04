from dataclasses import dataclass
from typing import Literal

from .spi import rpiSpi, spiError

class inaError(RuntimeError):
    """INA229 related errors"""

# INA229 Register Addresses (from manual, assuming hex)
REG_CONFIG       = 0x00
REG_ADC_CONFIG   = 0x01
REG_SHUNT_CAL    = 0x02
REG_VSHUNT       = 0X04
REG_VBUS         = 0X05
REG_CURRENT      = 0x07
REG_POWER        = 0X08

# Constants
CURRENT_MAX = 14 # in amps
R_SHUNT = float(10e-3)  # R_SHUNT < V_max / CURRENT_MAX
CURRENT_LSB = float(CURRENT_MAX) / pow(2, 19) # CURRENT_MAX / 2^19, from manual
SHUNT_CAL = int(13107.2 * 10e6 * CURRENT_LSB * R_SHUNT) # SHUNT_CAL calculation (from manual)

@dataclass(frozen=True)
class ina229Cal:
   r_shunt: float
   max_current: float

   current_lsb: float | None = None
   adc_range: int = 0

class ina229:
   """
   Select device to be either ina_in or ina_out, since they use different mechanisms for their
   channel select: spi.transfer_ina_in() or spi.transfer_ina_out()
   """
   def __init__(self, spi: rpiSpi, device: Literal["ina_in", "ina_out"], cal: ina229Cal | None = None):
      self.spi = spi
      self.device = device
      self.cal = cal
      self. current_lsb: float | None = cal.current_lsb if cal else None

   # ------- low level helpers -------
    
   @staticmethod
   def _cmd(reg: bytes, read:bool) -> int:
      # check if reg is between 0x00 and 0x3F
      # build the initial 8 bit command
      if reg < 0 or reg > 0x3F:
         raise inaError("INA229 register address is between 0x00 and 0x3F")
      return ((reg & 0x3F) << 2) | (0 << 1) | (1 if read else 0)
   
   def _xfer(self, tx: bytes | bytearray) -> bytes:
      # routing the correct transfer function to the sensors
      if self.device == "ina_in":
         return self.spi.transfer_ina_in(tx)
      elif self.device == "ina_out":
         return self.spi.transfer_ina_out(tx)
      
   def twos_complement(value: int, bits: int) -> int:
         '''
         Reads a signed value and converts it to a negative integer in python
         '''
         signed = 1 <<(bits -1)
         if value & signed:
            return value - (1<<bits) # if the sign bit is set, subtract 2^bits to get the negative value
         
         return value # if the sign bit is not set, return the value as is
    
    # ------- register read/write -------
   def read_u16(self, reg: int) -> int:
      # send cmd bits + 2 extra bits (16 bits after cmd) - which are dont cares
      # should return 2 bytes, 0x00XX, where XX is the data in the register
      cmd = self._cmd(reg, read=True)
      rx = self.xfer(bytes([cmd, 0x00, 0x00]))

      return (rx[1] << 8) | rx[2] # rx[1] is a byte of zeros
   
   def write_u16(self, reg: int, value: int) -> int:
      # writes a 16 bit value to a register
      pass
   
   def read_s24(self, reg: int) -> int:
      '''
      Read a signed 24-bit register from INA229
      '''

      # Build the SPI command byte for this register.
      # - 'reg' is the register address inside the INA229
      # - read=True means we want to read, not write
      cmd = self._cmd(reg, read=True)

      #   cmd   → tells INA229 which register to read
      #   0x00  → dummy byte, three dummy bytes (clock out 3 data bytes)
    
      rx = self._xfer(bytes([cmd, 0x00, 0x00, 0x00]))

      # Reconstruct the 24-bit register value from three received bytes.
      # Shift each byte into its correct bit position and OR them together. --> Not familiar with this, done by AI
      raw = (rx[1] << 16) | (rx[2] << 8) | rx[3] #combines three separate bytes into one 24-bit number.

      # Convert from 24-bit two's complement to a Python signed integer.
      raw = self.twos_complement(raw, 24)

      # Return the signed measurement value.
      # Return is a raw number and must be scaled (e.g. by CURRENT_LSB) to become a real physical quantity.
      return raw

   def write_u24(self, reg: int, value: int) -> None:
      '''
      Write a 24-bit value to a register.
      Most INA229 24-bit registers are read-only,
      but this function exists for completeness.
      '''

      #Point of write: Splits a 24-bit number into bytes, sends them to the INA229, INA229 stores them internally

      # Check that the value fits in 24 bits.
      # Valid range: 0 to (2^24 - 1)
      # This prevents accidentally sending too much data.
      if not (0 <= value < (1 << 24)):
         raise inaError("24-bit value out of range")

      # Build the SPI command byte for a write operation.
      # read=False means this is a write command.
      cmd = self._cmd(reg, read=False)

      # Build the byte sequence to send over SPI.
      # Byte 0 → command byte (register address + write bit)
      # Byte 1 → upper 8 bits of the 24-bit value
      # Byte 2 → middle 8 bits
      # Byte 3 → lower 8 bits

      # Bit shifts extract the correct part of the value.
      tx = bytes([
         cmd,                     # Command byte
         (value >> 16) & 0xFF,    # Bits 23–16
         (value >> 8) & 0xFF,     # Bits 15–8
         value & 0xFF             # Bits 7–0
      ])

      # Send the bytes over SPI.
      # The INA229 reads the command and stores the 24-bit value
      # into the specified register.
      self._xfer(tx)

# INA229 Initialization 

calibration = ina229Cal(R_SHUNT, CURRENT_MAX, CURRENT_LSB, 0)
spi_obj = rpiSpi()
spi_obj.init()
sensor = ina229(spi_obj, 'ina_in', calibration)

# CONFIG:
# ADCRANGE = 0 (+/-163.84 mV)
sensor.write_u16(REG_CONFIG, 0x0000)


# ADC_CONFIG:
# MODE = Continuous shunt + bus (0xB)
# Since we're not measuring power, I think we can just do 0xA (cont. shunt voltage)
# VBUSCT = 1ms
# VSHCT = 1ms <- this is typical, but maybe we can go longer
# AVG = 1 <- discuss with leads if we want longer averaging. The basic question is how frequently do we want our samples?
# we also have to take noise into account (can also use external filters)
adc_config = 0xB000 | (5 << 9) | (5 << 6) 
sensor.write_u16(REG_ADC_CONFIG, adc_config) 


# SHUNT_CAL
sensor.write_u16(REG_SHUNT_CAL, sensor.cal.r_shunt) # sets up values for shunt resistor in sensor


# Give time for first conversion (AI says we need this)
time.sleep(0.1)

try:
   while True: # infinite loop
      raw_current = sensor.read_s24(REG_CURRENT) # reads raw current value from sensor
      current_amps = raw_current * CURRENT_LSB # converts raw current to actual value (from manual)
      print(f"Current: {current_amps} A") # prints current (I wasn’t sure what to do with it)
      time.sleep(0.5) # AI says this makes it update twice per second
except spiError:
   spi_obj.deinit()