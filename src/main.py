import signal
import sys
import time

from control.control import ConverterState
from control.converter import Converter, ConverterConfig


LOG_PERIOD_S = 0.250

running = True


def handle_signal(signum, frame) -> None:
    global running
    running = False


def format_status(status) -> str:
    return (
        f"state={status.state.name:8s} "
        f"mode={status.mode.name:10s} "
        f"vtarget={status.vtarget:7.3f} V "
        f"duty={status.duty:6.3f} "
        f"d1={status.duty1:6.3f} "
        f"d2={status.duty2:6.3f} "
        f"fault={status.fault_reason}"
    )


def main() -> int:
    global running

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # initialize your converter object, set the configs here
    converter = Converter(
        ConverterConfig(
            pwm_freq=300_000,
            pi_rate=30_000,
            po_rate=1_000,
        )
    )

    loop_period_s = 1.0 / converter.config.pi_rate
    next_tick_s = time.monotonic()
    next_log_s = next_tick_s

    try:
        # to ready the converter, enter standby state
        converter.enter_standby()

        # once in stand by, the converter just waits until cut in voltage is achieved

        while running:
            now_s = time.monotonic()
            # this is the main function you run: update_converter, this should be called in a loop at 30 kHz for as long as you want the converter
            # to chase mpp
            status = converter.update_converter()

            if now_s >= next_log_s:
                print(format_status(status))
                next_log_s += LOG_PERIOD_S

            # after calling update_converter, print error message in the case the converter faulted during the update
            if status.state == ConverterState.FAULT:
                print(f"converter faulted: {status.fault_reason}")
                return 1

            # feel free to ignore this, this is just the way i configured the converter to update at 30kHz, it will be different for you
            next_tick_s += loop_period_s
            sleep_s = next_tick_s - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_tick_s = time.monotonic()

        # call stop_converter to shut down the converter safely
        # after stop_converter, the converter should be in standby mode 
        print("stop requested")
        converter.stop_converter()

        while converter.get_status().state == ConverterState.STOPPING:
            status = converter.update_converter()

            if time.monotonic() >= next_log_s:
                print(format_status(status))
                next_log_s += LOG_PERIOD_S

            time.sleep(loop_period_s)

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    # final shutdown, essentially stop_converter and then force safe outputs for all output pins we are using for the buckboost
    finally:
        try:
            converter.stop_converter()
            converter.force_safe_outputs()
        except Exception:
            pass

        try:
            # deinit the converter after youre done
            converter.deinit()
        except Exception:
            pass

        print("shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
