#!/usr/bin/env python3

from __future__ import annotations

import sys
import time
import signal
from dataclasses import dataclass

try:
    import spidev
except ImportError:
    print("ERROR: spidev not found. Try: sudo apt install python3-spidev")
    sys.exit(1)

try:
    import pigpio
except ImportError:
    print("ERROR: pigpio not found. Try: sudo apt install pigpio python3-pigpio")
    sys.exit(1)


# ============================================================
# USER CONFIG
# ============================================================

SPI_BUS = 0
SPI_DEVICE = 0              # using /dev/spidev0.0 as SPI handle only
SPI_MAX_SPEED_HZ = 1_000_000

# Try 0 if INA229 IDs fail.
SPI_MODE = 1

READ_PERIOD_S = 0.5

# Manual CS lines, BCM numbering
CS_MCP3208_BCM = 23
CS_INA_IN_BCM = 24
CS_INA_OUT_BCM = 25

ALL_CS_PINS = [
    CS_MCP3208_BCM,
    CS_INA_IN_BCM,
    CS_INA_OUT_BCM,
]

# MCP3208 config
MCP3208_VREF = 3.3            
MCP3208_VREC_CH = 0            
MCP3208_VOUT_CH = 1            

VREC_DIVIDER_RATIO = 12.5       
VOUT_DIVIDER_RATIO = 1.0        


@dataclass
class INA229Config:
    name: str
    cs_bcm: int

    rshunt_ohms: float = 0.010          # shunt value in theory
    max_expected_current_a: float = 0.5 # TODO: fill expected max current

    use_low_shunt_range: bool = True    # TODO: confirm ADCRANGE setting

    avg_code: int = 0b010           
    vshct_code: int = 0b101             
    vbusct_code: int = 0b101            
    vtct_code: int = 0b101              
    mode_code: int = 0xA                # continuous shunt voltage only


INA_IN = INA229Config("INA_IN", CS_INA_IN_BCM)
INA_OUT = INA229Config("INA_OUT", CS_INA_OUT_BCM)


# ============================================================
# INA229 REGISTERS
# ============================================================

REG_CONFIG = 0x00
REG_ADC_CONFIG = 0x01
REG_SHUNT_CAL = 0x02
REG_VSHUNT = 0x04
REG_VBUS = 0x05
REG_CURRENT = 0x07
REG_MANUFACTURER_ID = 0x3E
REG_DEVICE_ID = 0x3F

EXPECTED_MANUFACTURER_ID = 0x5449
EXPECTED_DEVICE_ID = 0x2291


# ============================================================
# BASIC HELPERS
# ============================================================

def fmt_hex(value: int, width_bytes: int) -> str:
    return f"0x{value:0{width_bytes * 2}X}"


def sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit


# ============================================================
# GPIO INITIALIZATION
# ============================================================

def gpio_init() -> pigpio.pi:
    print("[GPIO] Initializing GPIO stuff")

    pi = pigpio.pi()

    if not pi.connected:
        raise RuntimeError("Could not connect to pigpio. have you ran sudo pigpiod?")

    for pin in ALL_CS_PINS:
        print(f"[GPIO] Setting CS BCM{pin} HIGH")
        pi.set_mode(pin, pigpio.OUTPUT)
        pi.write(pin, 1)

    print("[GPIO] GPIO initialization done")
    return pi


def all_cs_high(pi: pigpio.pi) -> None:
    for pin in ALL_CS_PINS:
        pi.write(pin, 1)


def select_device(pi: pigpio.pi, cs_bcm: int) -> None:
    all_cs_high(pi)
    time.sleep(0.000002)
    pi.write(cs_bcm, 0)
    time.sleep(0.000002)


def deselect_device(pi: pigpio.pi, cs_bcm: int) -> None:
    time.sleep(0.000002)
    pi.write(cs_bcm, 1)
    time.sleep(0.000002)


# ============================================================
# SPI INITIALIZATION
# ============================================================

def spi_init() -> spidev.SpiDev:
    print("[SPI] Opening SPI")

    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)

    spi.no_cs = True
    spi.mode = SPI_MODE
    spi.max_speed_hz = SPI_MAX_SPEED_HZ
    spi.bits_per_word = 8

    print(f"[SPI] bus={SPI_BUS}, device={SPI_DEVICE}")
    return spi


def spi_transfer_manual_cs(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cs_bcm: int,
    tx: list[int],
) -> list[int]:
    select_device(pi, cs_bcm)

    try:
        rx = spi.xfer2(tx)
    finally:
        deselect_device(pi, cs_bcm)

    return rx


# ============================================================
# MCP3208 READING FUNCTIONS
# ============================================================

def mcp3208_read_raw(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cs_bcm: int,
    channel: int,
) -> int:
    if not 0 <= channel <= 7:
        raise ValueError("MCP3208 channel must be 0-7")

    tx = [
        0x06 | ((channel & 0x04) >> 2),
        (channel & 0x03) << 6,
        0x00,
    ]

    rx = spi_transfer_manual_cs(spi, pi, cs_bcm, tx)

    raw = ((rx[1] & 0x0F) << 8) | rx[2]
    return raw


def mcp3208_read_adc_voltage(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cs_bcm: int,
    channel: int,
) -> float:
    raw = mcp3208_read_raw(spi, pi, cs_bcm, channel)
    return raw * MCP3208_VREF / 4095.0


def read_vrec(spi: spidev.SpiDev, pi: pigpio.pi) -> float:
    vadc = mcp3208_read_adc_voltage(spi, pi, CS_MCP3208_BCM, MCP3208_VREC_CH)
    return vadc * VREC_DIVIDER_RATIO


def read_vout(spi: spidev.SpiDev, pi: pigpio.pi) -> float:
    vadc = mcp3208_read_adc_voltage(spi, pi, CS_MCP3208_BCM, MCP3208_VOUT_CH)
    return vadc * VOUT_DIVIDER_RATIO


# ============================================================
# INA229 READING/WRITING FUNCTIONS
# ============================================================

def ina229_read_reg(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cfg: INA229Config,
    reg_addr: int,
    num_bytes: int,
) -> int:
    cmd = ((reg_addr & 0x3F) << 2) | 0x01
    tx = [cmd] + [0x00] * num_bytes

    rx = spi_transfer_manual_cs(spi, pi, cfg.cs_bcm, tx)

    data = 0
    for b in rx[1:]:
        data = (data << 8) | b

    return data


def ina229_write_reg(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cfg: INA229Config,
    reg_addr: int,
    value: int,
    num_bytes: int,
) -> None:
    cmd = ((reg_addr & 0x3F) << 2) | 0x00
    tx = [cmd]

    for shift in range(8 * (num_bytes - 1), -1, -8):
        tx.append((value >> shift) & 0xFF)

    spi_transfer_manual_cs(spi, pi, cfg.cs_bcm, tx)


def ina229_reset(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cfg: INA229Config,
) -> None:
    ina229_write_reg(spi, pi, cfg, REG_CONFIG, 0x8000, 2)
    time.sleep(0.010)


def ina229_compute_current_lsb_and_cal(cfg: INA229Config) -> tuple[float, int]:
    current_lsb = cfg.max_expected_current_a / (2 ** 19)

    shunt_cal = 13107.2e6 * current_lsb * cfg.rshunt_ohms

    if cfg.use_low_shunt_range:
        shunt_cal *= 4.0

    shunt_cal_int = int(round(shunt_cal)) & 0x7FFF
    return current_lsb, shunt_cal_int


def ina229_configure(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cfg: INA229Config,
) -> float:
    current_lsb, shunt_cal = ina229_compute_current_lsb_and_cal(cfg)

    config = 0x0010 if cfg.use_low_shunt_range else 0x0000

    adc_config = (
        (cfg.mode_code << 12)
        | (cfg.vbusct_code << 9)
        | (cfg.vshct_code << 6)
        | (cfg.vtct_code << 3)
        | cfg.avg_code
    )

    ina229_write_reg(spi, pi, cfg, REG_CONFIG, config, 2)
    ina229_write_reg(spi, pi, cfg, REG_ADC_CONFIG, adc_config, 2)
    ina229_write_reg(spi, pi, cfg, REG_SHUNT_CAL, shunt_cal, 2)

    time.sleep(0.050)

    print(f"[{cfg.name}] CONFIG      = {fmt_hex(config, 2)}")
    print(f"[{cfg.name}] ADC_CONFIG  = {fmt_hex(adc_config, 2)}")
    print(f"[{cfg.name}] SHUNT_CAL   = {shunt_cal} ({fmt_hex(shunt_cal, 2)})")
    print(f"[{cfg.name}] CURRENT_LSB = {current_lsb:.12e} A/LSB")

    return current_lsb


def ina229_read_ids(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cfg: INA229Config,
) -> tuple[int, int]:
    man_id = ina229_read_reg(spi, pi, cfg, REG_MANUFACTURER_ID, 2)
    dev_id = ina229_read_reg(spi, pi, cfg, REG_DEVICE_ID, 2)
    return man_id, dev_id


def ina229_read_current_amps(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cfg: INA229Config,
    current_lsb: float,
) -> float:
    raw24 = ina229_read_reg(spi, pi, cfg, REG_CURRENT, 3)

    raw20 = (raw24 >> 4) & 0xFFFFF
    raw_signed = sign_extend(raw20, 20)

    return raw_signed * current_lsb


def ina229_read_vshunt_volts(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cfg: INA229Config,
) -> float:
    raw24 = ina229_read_reg(spi, pi, cfg, REG_VSHUNT, 3)

    raw20 = (raw24 >> 4) & 0xFFFFF
    raw_signed = sign_extend(raw20, 20)

    lsb = 78.125e-9 if cfg.use_low_shunt_range else 312.5e-9
    return raw_signed * lsb


# ============================================================
# COMMUNICATION CHECKS
# ============================================================

def check_mcp3208(spi: spidev.SpiDev, pi: pigpio.pi) -> None:
    print("\n[MCP3208] Reading every channel")

    for ch in [MCP3208_VREC_CH, MCP3208_VOUT_CH]:
        raw = mcp3208_read_raw(spi, pi, CS_MCP3208_BCM, ch)
        vadc = raw * MCP3208_VREF / 4095.0

        print(f"[MCP3208] CH{ch}: raw={raw:04d}, adc_voltage={vadc:.4f} V")


def check_ina229(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    cfg: INA229Config,
) -> float:
    print(f"\n[{cfg.name}] Checking INA229...")

    ina229_reset(spi, pi, cfg)

    man_id, dev_id = ina229_read_ids(spi, pi, cfg)

    print(f"[{cfg.name}] MANUFACTURER_ID = {fmt_hex(man_id, 2)}")
    print(f"[{cfg.name}] DEVICE_ID       = {fmt_hex(dev_id, 2)}")

    if man_id == EXPECTED_MANUFACTURER_ID:
        print(f"[{cfg.name}] OK: manufacturer ID matches TI.")
    else:
        print(f"[{cfg.name}] WARN: expected {fmt_hex(EXPECTED_MANUFACTURER_ID, 2)}")

    if dev_id == EXPECTED_DEVICE_ID:
        print(f"[{cfg.name}] OK: device ID matches INA229.")
    else:
        print(f"[{cfg.name}] WARN: expected {fmt_hex(EXPECTED_DEVICE_ID, 2)}")

    current_lsb = ina229_configure(spi, pi, cfg)

    return current_lsb


# ============================================================
# TEST LOOP
# ============================================================

def test_loop(
    spi: spidev.SpiDev,
    pi: pigpio.pi,
    ina_in_lsb: float,
    ina_out_lsb: float,
) -> None:
    print("\n[LOOP] Starting read loop. Press Ctrl+C to stop.\n")

    sample = 0

    while running:
        try:
            vrec = read_vrec(spi, pi)
            vout = read_vout(spi, pi)

            i_in = ina229_read_current_amps(spi, pi, INA_IN, ina_in_lsb)
            i_out = ina229_read_current_amps(spi, pi, INA_OUT, ina_out_lsb)

            vshunt_in = ina229_read_vshunt_volts(spi, pi, INA_IN)
            vshunt_out = ina229_read_vshunt_volts(spi, pi, INA_OUT)

            print(
                f"[{sample:05d}] "
                f"VREC={vrec:.4f} V | "
                f"VOUT={vout:.4f} V | "
                f"INA_IN: I={i_in:+.6f} A, Vshunt={vshunt_in * 1e3:+.3f} mV | "
                f"INA_OUT: I={i_out:+.6f} A, Vshunt={vshunt_out * 1e3:+.3f} mV"
            )

            sample += 1
            time.sleep(READ_PERIOD_S)

        except Exception as exc:
            print(f"[ERROR] Loop exception: {exc}")
            time.sleep(0.5)


# ============================================================
# CLEANUP
# ============================================================

def cleanup(
    spi: spidev.SpiDev | None,
    pi: pigpio.pi | None,
) -> None:
    print("\n[CLEANUP] Cleaning up...")

    if pi is not None:
        try:
            all_cs_high(pi)
        except Exception:
            pass

    if spi is not None:
        try:
            spi.close()
        except Exception as exc:
            print(f"[CLEANUP] SPI close warning: {exc}")

    if pi is not None:
        try:
            pi.stop()
        except Exception as exc:
            print(f"[CLEANUP] pigpio stop warning: {exc}")

    print("[CLEANUP] Done.")


# ============================================================
# MAIN
# ============================================================

running = True


def handle_signal(signum, frame) -> None:
    global running
    running = False


def main() -> None:
    global running

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    pi = None
    spi = None

    try:
        print("==================================================")
        print(" PCB Tandem Sensor Test")
        print(" MCP3208 + INA229 IN + INA229 OUT")
        print(" Manual CS on BCM 23, 24, 25")
        print("==================================================")

        pi = gpio_init()
        spi = spi_init()

        check_mcp3208(spi, pi)

        ina_in_lsb = check_ina229(spi, pi, INA_IN)
        ina_out_lsb = check_ina229(spi, pi, INA_OUT)

        test_loop(spi, pi, ina_in_lsb, ina_out_lsb)

    except Exception as exc:
        print(f"[FATAL] {exc}")

    finally:
        cleanup(spi, pi)


if __name__ == "__main__":
    main()