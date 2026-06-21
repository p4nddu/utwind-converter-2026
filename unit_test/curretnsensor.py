#!/usr/bin/env python3

from __future__ import annotations

import time
import signal
import sys

import spidev
import pigpio

# =========================
# CONFIG
# =========================

SPI_BUS = 0
SPI_DEVICE = 0

CS_INA_IN = 25 # INA_IN chip select
CS_INA_OUT = 17 # INA_OUT chip select
CS_ADC = 27 # MCP3208 chip select

SPI_MAX_SPEED_HZ = 1_000_000
SPI_MODE = 1

READ_PERIOD_S = 0.2

# ADC voltage divider
R1 = 232
R2 = 20
DIV_INV = 12.5

VREF = 3.3

# INA229 config
RSHUNT_OHMS = 1.5
MAX_EXPECTED_CURRENT_A = 0.1
USE_LOW_SHUNT_RANGE = False

# =========================
# INA229 REGISTERS
# =========================

REG_CONFIG = 0x00
REG_ADC_CONFIG = 0x01
REG_SHUNT_CAL = 0x02
REG_CURRENT = 0x07

# =========================
# UTIL
# =========================

def sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit

# =========================
# MAIN CLASS
# =========================

class SystemTest:
    def __init__(self):
        self.spi = None
        self.pi = None
        self.running = True
        
        self.current_lsb = None
        self.shunt_cal = None

# =====================
# GPIO (pigpio)
# =====================

    def setup_gpio(self):
        print("[GPIO] Connecting to pigpio...")
        self.pi = pigpio.pi()

        if not self.pi.connected:
            raise RuntimeError("pigpio daemon not running")

    # Setup all CS pins
        for pin in [CS_INA_IN, CS_INA_OUT, CS_ADC]:
            self.pi.set_mode(pin, pigpio.OUTPUT)
            self.pi.write(pin, 1) # HIGH = inactive

    def cs_low(self, device):
        # Make sure no two devices have CS low at the same time
        self.pi.write(CS_INA_IN, 1)
        self.pi.write(CS_INA_OUT, 1)
        self.pi.write(CS_ADC, 1)

        if device == "ina_in":
            self.pi.write(CS_INA_IN, 0)
        elif device == "ina_out":
            self.pi.write(CS_INA_OUT, 0)
        elif device == "adc":
            self.pi.write(CS_ADC, 0)
        else:
            raise ValueError(f"Unknown SPI device: {device}")

    def cs_high(self, device):
        if device == "ina_in":
            self.pi.write(CS_INA_IN, 1)
        elif device == "ina_out":
            self.pi.write(CS_INA_OUT, 1)
        elif device == "adc":
            self.pi.write(CS_ADC, 1)
        else:
            raise ValueError(f"Unknown SPI device: {device}")

# =====================
# SPI
# =====================

    def setup_spi(self):
        print("[SPI] Opening SPI...")
        self.spi = spidev.SpiDev()
        self.spi.open(SPI_BUS, SPI_DEVICE)
        self.spi.no_cs = True
        self.spi.mode = SPI_MODE
        self.spi.max_speed_hz = SPI_MAX_SPEED_HZ

    def transfer(self, tx, device):
        self.cs_low(device)
        try:
            rx = self.spi.xfer2(tx)
        finally:
            self.cs_high(device)
        return rx

# =====================
# MCP3208 ADC
# =====================

    def read_adc_raw(self, channel: int) -> int:
        cmd = 0x06 | ((channel & 4) >> 2)
        msb = ((channel & 3) << 6)
        adc = self.transfer([cmd, msb, 0], "adc")
        return ((adc[1] & 0x0F) << 8) | adc[2]

    def read_adc_voltage(self, channel: int) -> float:
        raw = self.read_adc_raw(channel)
        return (raw / 4095.0) * VREF

    def read_scaled_voltage(self, channel: int) -> float:
        return round(self.read_adc_voltage(channel) * DIV_INV, 3)

# =====================
# INA229
# =====================

    def read_reg(self, reg_addr: int, num_bytes: int, device: str) -> int:
        cmd = ((reg_addr & 0x3F) << 2) | 0x01
        tx = [cmd] + [0x00] * num_bytes
        rx = self.transfer(tx, device)

        data = 0
        for b in rx[1:]:
            data = (data << 8) | b
        return data

    def write_reg(self, reg_addr: int, value: int, num_bytes: int, device: str):
        cmd = ((reg_addr & 0x3F) << 2)
        tx = [cmd]
        for shift in range(8 * (num_bytes - 1), -1, -8):
            tx.append((value >> shift) & 0xFF)
        self.transfer(tx, device)

    def reset_device(self):
        self.write_reg(REG_CONFIG, 0x8000, 2, "ina_in")
        self.write_reg(REG_CONFIG, 0x8000, 2, "ina_out")
        time.sleep(0.01)

    def compute_current_lsb_and_cal(self):
        current_lsb = MAX_EXPECTED_CURRENT_A / (2 ** 19)
        shunt_cal = 13107.2e6 * current_lsb * RSHUNT_OHMS

        if USE_LOW_SHUNT_RANGE:
            shunt_cal *= 4

        return current_lsb, int(round(shunt_cal)) & 0x7FFF

    def configure_device(self):
        self.current_lsb, self.shunt_cal = self.compute_current_lsb_and_cal()

        config = 0x0010 if USE_LOW_SHUNT_RANGE else 0x0000
        adc_config = (0xA << 12) | (0x5 << 9) | (0x5 << 6) | (0x5 << 3) | 0x2

        for device in ["ina_in", "ina_out"]:
            self.write_reg(REG_CONFIG, config, 2, device)
            self.write_reg(REG_ADC_CONFIG, adc_config, 2, device)
            self.write_reg(REG_SHUNT_CAL, self.shunt_cal, 2, device)

    def read_current(self, device: str) -> float:
        raw24 = self.read_reg(REG_CURRENT, 3, device)
        raw20 = (raw24 >> 4) & 0xFFFFF
        signed = sign_extend(raw20, 20)
        return signed * self.current_lsb

# =====================
# CLEANUP
# =====================

    def cleanup(self):
        print("\nCleaning up...")
        try:
            if self.spi:
                self.spi.close()
        except:
            pass

        try:
            if self.pi:
                self.pi.write(CS_INA_IN, 1)
                self.pi.write(CS_INA_OUT, 1)
                self.pi.write(CS_ADC, 1)
                self.pi.stop()
        except:
            pass

    # =====================
    # LOOP 
    # =====================

    def run(self):
        def handler(sig, frame):
           self.running = False

        signal.signal(signal.SIGINT, handler)

        self.setup_gpio()
        self.setup_spi()

        self.reset_device()
        self.configure_device()

        print("\nStarting loop...\n")

        step = 0
        next_time = time.time()

        while self.running:
            try:
                current_in = self.read_current("ina_in")
                current_out = self.read_current("ina_out")

                vout = self.read_scaled_voltage(1)
                vin = self.read_scaled_voltage(0)

                print(
                    f"[{step:05d}] "
                    f"I_IN={current_in:+.6f} A | "
                    f"I_OUT={current_out:+.6f} A | "
                    f"Vin={vin:.3f} V | "
                    f"Vout={vout:.3f} V"
                )
                
                step += 1
                
                next_time += READ_PERIOD_S
                sleep = next_time - time.time()
                if sleep > 0:
                    time.sleep(sleep)
                else:
                    next_time = time.time()
            except Exception as e:
                print("[ERROR]", e)
                time.sleep(0.5)

        self.cleanup()



# =========================
# ENTRY
# =========================

if __name__ == "__main__":
    app = SystemTest()
    try:
        app.run()
    except Exception as e:
        print("[FATAL]", e)
        app.cleanup()
        sys.exit(1)
