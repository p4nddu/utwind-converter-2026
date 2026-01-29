import spidev
import RPi.GPIO as GPIO
import bisect

# Global handles
_spi = None
_pwm = None

# ADC setup and read
def init_adc(bus=0, device=0, max_speed_hz=100000):
    """
    Initialize SPI for MCP3208.
    bus, device: SPI bus and chip select
    max_speed_hz: SPI clock speed
    """
    global _spi
    _spi = spidev.SpiDev()
    _spi.open(bus, device)
    _spi.max_speed_hz = max_speed_hz


def _read_channel(channel):
    """
    Low-level read from MCP3208 channel (0-7).
    Returns raw ADC code (0-4095).
    """
    # Command bits: start=1, single-ended=1, channel bits
    cmd = 0x06 | ((channel & 4) >> 2)
    msb = ((channel & 3) << 6)
    adc = _spi.xfer([cmd, msb, 0])
    data = ((adc[1] & 15) << 8) | adc[2]
    return data



def read_voltage(channel, vref=3.3):
    adc_value = _read_channel(channel)
    voltage = (adc_value / 4095.0) * vref
    return voltage 


# PWM setup and control
def init_pwm(pin, freq_hz):
    """
    Initialize a PWM output on given BCM pin at freq_hz.
    """
    global _pwm
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(pin, GPIO.OUT)
    _pwm = GPIO.PWM(pin, freq_hz)
    _pwm.start(0)  # start with 0% duty


def set_duty_cycle(duty):
    """
    Update PWM duty cycle (0.0 to 1.0).
    """
    if _pwm is None:
        raise RuntimeError("PWM not initialized")
    # Convert to percentage
    percent = max(0.0, min(duty, 1.0)) * 100.0
    _pwm.ChangeDutyCycle(percent)



def shutdown():
    """
    Clean up SPI and GPIO.
    """
    global _spi, _pwm
    if _pwm:
        _pwm.stop()
    GPIO.cleanup()
    if _spi:
        _spi.close()




