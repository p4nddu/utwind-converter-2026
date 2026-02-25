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
   
   def 
   




    

R_SHUNT = 1  # setting 1ohm shunt resistor (update w actual value)

CURRENT_LSB = 1 # update w actual value

SHUNT_CAL = int(13107.2 * 10e6 * CURRENT_LSB * R_SHUNT) # SHUNT_CAL calculation (from manual)


# SPI Setup (AI wrote so might be wrong, but looks similar to what Eleni wrote in the other program)
spi = spidev.SpiDev()
spi.open(0, 0)          # Bus 0, CS 0
spi.max_speed_hz = 1_000_000
spi.mode = 0b01         # INA229 uses SPI mode 1


# Helper Functions (I did most of the logic for these, but AI put it in the right 
#syntax bc I don’t know python syntax)
def write_register(reg, value):
   """
   Write 16-bit value to register
   """
   cmd = (reg << 2) | 0x00  # shifts register address left and adds write command
   tx = [ cmd, (value >> 8) & 0xFF, value & 0xFF] # AI says pi takes one byte at a time, so this breaks up the full thing (24 bits) into the three bytes (8 bits each): cmd from prev line, first 8 bits of input data, second 8 bits of input data
   spi.xfer2(tx) # sending to pi one byte at a time (AI wrote, might be wrong)


def read_register_24(reg):
   """
   Read 24-bit signed register (where current is)
   """
   cmd = (reg << 2) | 0x01  # Read command, same as above
   rx = spi.xfer2([cmd, 0x00, 0x00, 0x00]) # reads the three bytes from sensor [23:4]
   raw = (rx[1] << 16) | (rx[2] << 8) | rx[3] # puts three bytes into one variable


   # Convert from 24-bit two's complement
   if raw & 0x800000: # AI says this checks if the number is negative
       raw -= 1 << 24 # converts to signed integer 


   return raw # returns signed integer


# INA229 Initialization (AI wrote a bunch of this, read comments)


# CONFIG:
# ADCRANGE = 0 (+/-163.84 mV)
write_register(REG_CONFIG, 0x0000) # AI wrote


# ADC_CONFIG:
# MODE = Continuous shunt + bus (0xB)
# VBUSCT = 1ms
# VSHCT = 1ms
# AVG = 1
adc_config = 0xB000 | (5 << 9) | (5 << 6) # AI wrote
write_register(REG_ADC_CONFIG, adc_config) # AI wrote, sends configuration from above to pi


# SHUNT_CAL
write_register(REG_SHUNT_CAL, SHUNT_CAL) # sets up values for shunt resistor in sensor


# Give time for first conversion (AI says we need this)
time.sleep(0.1)

try:
   while True: # infinite loop
       raw_current = read_register_24(REG_CURRENT) # reads raw current value from sensor
       current_amps = raw_current * CURRENT_LSB # converts raw current to actual value (from manual)


       print(f"Current: {current_amps} A") # prints current (I wasn’t sure what to do with it)


       time.sleep(0.5) # AI says this makes it update twice per second


# AI wrote this part, says that Ctrl+C interrupts to close program
except KeyboardInterrupt:
   spi.close()
   print("\nSPI closed")