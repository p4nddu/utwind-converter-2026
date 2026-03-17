#!/usr/bin/env python3
"""
INA229 direct bench test
-------------------------

Wiring:
- Pi 3V3  -> INA229 VS
- Pi GND  -> INA229 GND
- Pi SCLK -> INA229 SCLK
- Pi MOSI -> INA229 SDI
- Pi MISO -> INA229 SDO
- Pi pin  CS (manual chip select in this test)

Notes:
- We're setting the CS manually
"""

from __future__ import annotations

import time
import math
import signal
import sys

try:
    import spidev
except ImportError:
    print("ERROR: cant find spidev module. try: sudo apt install python3-spidev and ls /dev/spidev*")
    sys.exit(1)

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("ERROR: cant find rpi.GPIO module. try: sudo apt install python3-rpi.gpio")
    sys.exit(1)

SPI_BUS = 0
SPI_DEVICE = 0 
CS_BCM = 25              # physical pin 22
SPI_MAX_SPEED_HZ = 1_000_000
SPI_MODE = 1            # try mode 0 before giving up if test doesn't work
READ_PERIOD_S = 0.2

RSHUNT_OHMS = 0.010     # 0.7 ohmreal value apparently, idk
MAX_EXPECTED_CURRENT_A = 0.5   # choose whatever brah

# True if shunt voltage drop wil stay < 40.96 mV
USE_LOW_SHUNT_RANGE = True

# more config - pretty sure half of these dont really matter
AVG_CODE = 0b010        # 16 samples
VSHCT_CODE = 0b101      # 1052 us
VBUSCT_CODE = 0b101     # 1052 us
VTCT_CODE = 0b101       # 1052 us
MODE_CONTINUOUS_SHUNT_ONLY = 0xA

# INA229 register map
# ----------------------------
REG_CONFIG           = 0x00
REG_ADC_CONFIG       = 0x01
REG_SHUNT_CAL        = 0x02
REG_VSHUNT           = 0x04
REG_VBUS             = 0x05
REG_DIETEMP          = 0x06
REG_CURRENT          = 0x07
REG_POWER            = 0x08
REG_DIAG_ALRT        = 0x0B
REG_MANUFACTURER_ID  = 0x3E
REG_DEVICE_ID        = 0x3F

# helpers
# -----------------------------
def sign_extend(value: int, bits: int) -> int:
    # sign extend 2's comp
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit


def fmt_hex(value: int, width_bytes: int) -> str:
    return f"0x{value:0{width_bytes * 2}X}"


class INA229DirectTest:
    def __init__(self) -> None:
        self.spi = None
        self.current_lsb = None
        self.shunt_cal = None
        self.running = True

    def setup_gpio(self) -> None:
        print("[GPIO] Setting GPIO mode to BCM...")
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        print(f"[GPIO] Configuring CS pin BCM{CS_BCM} as output...")
        GPIO.setup(CS_BCM, GPIO.OUT)

        print(f"[GPIO] Setting CS pin BCM{CS_BCM} HIGH initially...")
        GPIO.output(CS_BCM, GPIO.HIGH)

    def setup_spi(self) -> None:
        print(f"[SPI] Opening SPI bus={SPI_BUS}, device={SPI_DEVICE} ...")
        self.spi = spidev.SpiDev()
        self.spi.open(SPI_BUS, SPI_DEVICE)

        print("[SPI] Configuring no_cs=True so script controls CS manually...")
        self.spi.no_cs = True

        print(f"[SPI] Setting mode={SPI_MODE} ...")
        self.spi.mode = SPI_MODE

        print(f"[SPI] Setting max_speed_hz={SPI_MAX_SPEED_HZ} ...")
        self.spi.max_speed_hz = SPI_MAX_SPEED_HZ

        print("[SPI] Setting bits_per_word=8 ...")
        self.spi.bits_per_word = 8

        print("[SPI] SPI setup complete.")

    def cleanup(self) -> None:
        print("\n[CLEANUP] Stopping test and cleaning up resources...")
        try:
            if self.spi is not None:
                print("[CLEANUP] Closing SPI...")
                self.spi.close()
        except Exception as e:
            print(f"[CLEANUP] SPI close warning: {e}")

        try:
            print("[CLEANUP] Driving CS HIGH...")
            GPIO.output(CS_BCM, GPIO.HIGH)
        except Exception:
            pass

        try:
            print("[CLEANUP] Cleaning up GPIO...")
            GPIO.cleanup()
        except Exception as e:
            print(f"[CLEANUP] GPIO cleanup warning: {e}")

        print("[CLEANUP] Done.")

    def cs_low(self) -> None:
        GPIO.output(CS_BCM, GPIO.LOW)

    def cs_high(self) -> None:
        GPIO.output(CS_BCM, GPIO.HIGH)

    def transfer(self, tx: list[int]) -> list[int]:
        self.cs_low()
        try:
            rx = self.spi.xfer2(tx)
        finally:
            self.cs_high()
        return rx

    def read_reg(self, reg_addr: int, num_bytes: int) -> int:
        """
        command byte:
            [A5 A4 A3 A2 A1 A0 0 R/W]
        read = R/W = 1
        """
        cmd = ((reg_addr & 0x3F) << 2) | 0x01
        tx = [cmd] + [0x00] * num_bytes
        rx = self.transfer(tx)

        data = 0
        for b in rx[1:]:
            data = (data << 8) | b
        return data

    def write_reg(self, reg_addr: int, value: int, num_bytes: int) -> None:
        """
        write = R/W = 0
        """
        cmd = ((reg_addr & 0x3F) << 2) | 0x00
        tx = [cmd]
        for shift in range(8 * (num_bytes - 1), -1, -8):
            tx.append((value >> shift) & 0xFF)

        self.transfer(tx)

    def reset_device(self) -> None:
        print("[INA229] resetting...")
        # CONFIG register is 16-bit, bit 15 = RST
        self.write_reg(REG_CONFIG, 0x8000, 2)
        time.sleep(0.010)

    def compute_current_lsb_and_cal(self) -> tuple[float, int]:
        """
        datasheet:
            CURRENT_LSB = MaxExpectedCurrent / 2^19
            SHUNT_CAL = 13107.2e6 * CURRENT_LSB * RSHUNT
            multiply SHUNT_CAL by 4 if ADCRANGE = 1
        """
        current_lsb = MAX_EXPECTED_CURRENT_A / (2 ** 19)
        shunt_cal = 13107.2e6 * current_lsb * RSHUNT_OHMS

        if USE_LOW_SHUNT_RANGE:
            shunt_cal *= 4.0

        shunt_cal_int = int(round(shunt_cal))
        shunt_cal_int &= 0x7FFF

        return current_lsb, shunt_cal_int

    def configure_device(self) -> None:
        print("[INA229] Computing CURRENT_LSB and SHUNT_CAL...")
        self.current_lsb, self.shunt_cal = self.compute_current_lsb_and_cal()
        print(f"[INA229] CURRENT_LSB = {self.current_lsb:.12e} A/LSB")
        print(f"[INA229] SHUNT_CAL   = {self.shunt_cal} ({fmt_hex(self.shunt_cal, 2)})")

        # CONFIG register:
        # bit 4 = ADCRANGE
        config = 0x0010 if USE_LOW_SHUNT_RANGE else 0x0000
        print(f"[INA229] Writing CONFIG = {fmt_hex(config, 2)} ...")
        self.write_reg(REG_CONFIG, config, 2)

        # ADC_CONFIG register fields:
        # [15:12] MODE
        # [11:9]  VBUSCT
        # [8:6]   VSHCT
        # [5:3]   VTCT
        # [2:0]   AVG
        adc_config = (
            (MODE_CONTINUOUS_SHUNT_ONLY << 12) |
            (VBUSCT_CODE << 9) |
            (VSHCT_CODE << 6) |
            (VTCT_CODE << 3) |
            AVG_CODE
        )

        print(f"[INA229] Writing ADC_CONFIG = {fmt_hex(adc_config, 2)} ...")
        self.write_reg(REG_ADC_CONFIG, adc_config, 2)

        print(f"[INA229] Writing SHUNT_CAL = {fmt_hex(self.shunt_cal, 2)} ...")
        self.write_reg(REG_SHUNT_CAL, self.shunt_cal, 2)

        time.sleep(0.050)
        print("[INA229] Configuration complete.")

    def read_ids(self) -> None:
        print("[INA229] Reading Manufacturer ID...")
        man_id = self.read_reg(REG_MANUFACTURER_ID, 2)
        print(f"[INA229] MANUFACTURER_ID = {fmt_hex(man_id, 2)}")

        print("[INA229] Reading Device ID...")
        dev_id = self.read_reg(REG_DEVICE_ID, 2)
        print(f"[INA229] DEVICE_ID       = {fmt_hex(dev_id, 2)}")

        expected_man = 0x5449
        expected_dev = 0x2291

        if man_id != expected_man:
            print(f"[WARN] Manufacturer ID mismatch. Expected {fmt_hex(expected_man, 2)}")
        else:
            print("[OK] Manufacturer ID matches expected TI value.")

        if dev_id != expected_dev:
            print(f"[WARN] Device ID mismatch. Expected {fmt_hex(expected_dev, 2)}")
        else:
            print("[OK] Device ID matches expected INA229 value.")

    def read_current_amps(self) -> float:
        raw24 = self.read_reg(REG_CURRENT, 3)

        # current value is stored in bits 23:4, 2's comp
        # so we gotta extract the 20 bits, and manipulate the bits
        raw20 = (raw24 >> 4) & 0xFFFFF
        raw_signed = sign_extend(raw20, 20)

        current_a = raw_signed * self.current_lsb
        return current_a

    def read_vshunt_volts(self) -> float:
        raw24 = self.read_reg(REG_VSHUNT, 3)
        raw20 = (raw24 >> 4) & 0xFFFFF
        raw_signed = sign_extend(raw20, 20)

        lsb = 78.125e-9 if USE_LOW_SHUNT_RANGE else 312.5e-9
        return raw_signed * lsb

    def run(self) -> None:
        def _handle_signal(signum, frame):
            self.running = False

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        print("==================================================")
        print(" INA229 Direct SPI Bench Test (thank you chatGPT for structuring this)")
        print("==================================================")
        print(f"RSHUNT                : {RSHUNT_OHMS} ohm")
        print(f"MAX_EXPECTED_CURRENT  : {MAX_EXPECTED_CURRENT_A} A")
        print(f"ADCRANGE low (±40.96mV): {USE_LOW_SHUNT_RANGE}")
        print(f"Read period           : {READ_PERIOD_S} s")
        print("==================================================")

        self.setup_gpio()
        self.setup_spi()
        self.reset_device()
        self.read_ids()
        self.configure_device()

        print("\n[LOOP] Starting current read loop. Press Ctrl+C to stop.\n")

        sample_idx = 0
        while self.running:
            try:
                current_a = self.read_current_amps()
                vshunt_v = self.read_vshunt_volts()

                print(
                    f"[{sample_idx:05d}] "
                    f"Current = {current_a:+.6f} A | "
                    f"Vshunt = {vshunt_v*1e3:+.3f} mV"
                )

                sample_idx += 1
                time.sleep(READ_PERIOD_S)

            except Exception as e:
                print(f"[ERROR] Read loop exception: {e}")
                time.sleep(0.5)

        self.cleanup()


if __name__ == "__main__":
    tester = INA229DirectTest()
    try:
        tester.run()
    except Exception as exc:
        print(f"[FATAL] {exc}")
        tester.cleanup()
        sys.exit(1)