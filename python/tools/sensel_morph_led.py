#!/usr/bin/env python3
"""Control the Sensel Morph white LED strip."""

from __future__ import annotations

import argparse
import json
import math
import random
import signal
import sys
import time
from dataclasses import dataclass
from typing import Sequence

import decode_pressure as dp
import morph_capture as mc


REG_LED_BRIGHTNESS = 0x80
REG_LED_BRIGHTNESS_SIZE = 0x81
REG_LED_BRIGHTNESS_MAX = 0x82
REG_LED_COUNT = 0x84
DEFAULT_PRESSURE_FLOOR = 50.0
DEFAULT_PRESSURE_REF = 15000.0
METER_GAMMA = 2.5
TWINKLE_IDLE_PROBABILITY = 0.010
TWINKLE_PRESSURE_PROBABILITY = 0.120


@dataclass(frozen=True)
class LedInfo:
    """LED-strip register metadata plus the current brightness values."""

    count: int
    reg_size: int
    max_brightness: int
    values: list[int]


class LedRunner:
    """Signal-aware run flag for standalone animation modes."""

    def __init__(self) -> None:
        self.running = True

    def stop(self, *_args: object) -> None:
        self.running = False


def clamp_int(value: float, lo: int, hi: int) -> int:
    """Round and clamp a floating value to an integer register range."""
    return max(lo, min(hi, int(round(value))))


def log_pressure_response(total: float, pressure_ref: float = DEFAULT_PRESSURE_REF, pressure_floor: float = DEFAULT_PRESSURE_FLOOR) -> float:
    """Map total pressure to 0..1 with a log curve for brushes and hands."""
    total = max(0.0, float(total))
    pressure_ref = max(1.0, float(pressure_ref))
    pressure_floor = max(1.0, float(pressure_floor))
    return max(0.0, min(1.0, math.log1p(total / pressure_floor) / math.log1p(pressure_ref / pressure_floor)))


def brightness_from_norm(norm: float, max_brightness: int) -> int:
    """Convert normalized brightness to device register units."""
    return clamp_int(max(0.0, min(1.0, norm)) * max_brightness, 0, max_brightness)


def glow_values(count: int, max_brightness: int, total_pressure: float, pressure_ref: float, pressure_floor: float) -> list[int]:
    """Brighten the whole strip together while keeping a visible 10% floor."""
    response = log_pressure_response(total_pressure, pressure_ref, pressure_floor)
    value = brightness_from_norm(0.10 + 0.90 * response, max_brightness)
    return [value] * count


def meter_values(
    count: int,
    max_brightness: int,
    total_pressure: float,
    pressure_ref: float,
    pressure_floor: float,
) -> list[int]:
    """Render total pressure as a gamma-shaped left-to-right level meter."""
    norm = log_pressure_response(total_pressure, pressure_ref, pressure_floor) ** METER_GAMMA
    level = norm * count
    values = []
    for i in range(count):
        fill = max(0.0, min(1.0, level - i))
        values.append(brightness_from_norm(fill, max_brightness))
    return values


def pulse_values(max_brightness: int, phases: Sequence[float]) -> list[int]:
    """Convert per-LED oscillator phases into brightness values."""
    return [brightness_from_norm(0.10 + 0.90 * (0.5 + 0.5 * math.sin(phase)), max_brightness) for phase in phases]


def kitt_values(count: int, max_brightness: int, phase: float) -> list[int]:
    """Render a back-and-forth moving highlight across the strip."""
    if count <= 1:
        return [max_brightness] * count
    period = 2.0 * (count - 1)
    position = phase % period
    if position > count - 1:
        position = period - position
    values = []
    for i in range(count):
        distance = abs(i - position)
        level = max(0.0, 1.0 - distance / 3.0)
        values.append(brightness_from_norm(level * level, max_brightness))
    return values


def decay_twinkle(values: list[float], decay: float) -> list[float]:
    return [max(0.0, value * decay) for value in values]


def twinkle_step(
    values: list[float],
    max_brightness: int,
    response: float,
    _column_responses: Sequence[float],
    rng: random.Random,
) -> list[float]:
    """Advance the random twinkle envelope using total-pressure probability."""
    values = decay_twinkle(values, 0.82)
    probability = min(1.0, TWINKLE_IDLE_PROBABILITY + TWINKLE_PRESSURE_PROBABILITY * max(0.0, min(1.0, response)))
    for i in range(len(values)):
        if rng.random() < probability:
            values[i] = float(max_brightness)
    return values


def pressure_column_sums(decoded: dp.DecodedFrame, led_count: int) -> list[float]:
    """Sum source-grid pressure into columns aligned with the 24 LEDs."""
    sums = [0.0] * led_count
    if led_count <= 0:
        return sums
    grid = decoded.grid
    for y in range(grid.rows):
        row = y * grid.cols
        for x in range(grid.cols):
            led = min(led_count - 1, int(x * led_count / grid.cols))
            sums[led] += max(0, decoded.values[row + x])
    return sums


def pressure_column_responses(
    decoded: dp.DecodedFrame,
    led_count: int,
    pressure_ref: float,
    pressure_floor: float,
    threshold: float,
) -> list[float]:
    """Convert per-column pressure sums into thresholded log responses."""
    column_ref = max(pressure_floor, pressure_ref / 8.0)
    out = []
    for value in pressure_column_sums(decoded, led_count):
        out.append(0.0 if value < threshold else log_pressure_response(value, column_ref, pressure_floor))
    return out


def column_values(
    decoded: dp.DecodedFrame,
    led_count: int,
    max_brightness: int,
    pressure_ref: float,
    pressure_floor: float,
    threshold: float = DEFAULT_PRESSURE_FLOOR,
) -> list[int]:
    """Render per-column pressure as direct LED brightness."""
    return [
        brightness_from_norm(response, max_brightness)
        for response in pressure_column_responses(decoded, led_count, pressure_ref, pressure_floor, threshold)
    ]


def encode_led_values(values: Sequence[int], reg_size: int, max_brightness: int) -> bytes:
    """Encode brightness values for the LED variable-size register."""
    out = bytearray()
    for value in values:
        clamped = clamp_int(value, 0, max_brightness)
        if reg_size == 1:
            out.append(clamped & 0xFF)
        elif reg_size == 2:
            out.extend(clamped.to_bytes(2, "little"))
        else:
            raise ValueError(f"unsupported LED register size: {reg_size}")
    return bytes(out)


def decode_led_values(data: bytes, count: int, reg_size: int) -> list[int]:
    values = []
    for i in range(count):
        pos = i * reg_size
        if reg_size == 1:
            values.append(data[pos])
        elif reg_size == 2:
            values.append(int.from_bytes(data[pos : pos + 2], "little"))
        else:
            raise ValueError(f"unsupported LED register size: {reg_size}")
    return values


def read_led_info(fd: int) -> LedInfo:
    """Read LED count, register width, max brightness, and current values."""
    count = mc.read_reg(fd, REG_LED_COUNT, 1)[0]
    reg_size = mc.read_reg(fd, REG_LED_BRIGHTNESS_SIZE, 1)[0]
    max_brightness = int.from_bytes(mc.read_reg(fd, REG_LED_BRIGHTNESS_MAX, 2), "little")
    if count <= 0:
        raise SystemExit("Device reports no controllable LEDs.")
    if reg_size not in (1, 2):
        raise SystemExit(f"Unsupported LED brightness register size: {reg_size}")
    expected_bytes = count * reg_size
    raw = bytes.fromhex(str(mc.read_vs(fd, REG_LED_BRIGHTNESS)["data_hex"]))
    if len(raw) < expected_bytes:
        raise SystemExit(f"LED brightness array read returned {len(raw)} bytes, expected {expected_bytes}")
    values = decode_led_values(raw[: count * reg_size], count, reg_size)
    return LedInfo(count=count, reg_size=reg_size, max_brightness=max_brightness, values=values)


def write_led_values(fd: int, info: LedInfo, values: Sequence[int], timeout: float = 1.0) -> None:
    if len(values) != info.count:
        raise ValueError(f"expected {info.count} LED values, got {len(values)}")
    mc.write_vs_pipelined(fd, REG_LED_BRIGHTNESS, encode_led_values(values, info.reg_size, info.max_brightness), timeout=timeout)


def write_led_values_pipelined(fd: int, info: LedInfo, values: Sequence[int], timeout: float = 1.0) -> None:
    """Write LED values through the fast variable-size register path."""
    write_led_values(fd, info, values, timeout=timeout)


def print_info(device: str, info: LedInfo) -> None:
    print(
        json.dumps(
            {
                "device": device,
                "led_count": info.count,
                "brightness_register_size": info.reg_size,
                "max_brightness": info.max_brightness,
                "values": info.values,
            },
            indent=2,
        )
    )


def command_info(args: argparse.Namespace) -> int:
    device = args.device or mc.find_device()
    fd = mc.open_serial(device)
    try:
        print_info(device, read_led_info(fd))
    finally:
        mc.os.close(fd)
    return 0


def pressure_setup(fd: int) -> tuple[dict[str, str], list[dp.Grid]]:
    """Enable pressure-only scanning for standalone LED animation modes."""
    initial = {
        "scan_detail": mc.read_reg(fd, mc.REG_SCAN_DETAIL, 1).hex(),
        "frame_content": mc.read_reg(fd, mc.REG_FRAME_CONTENT, 1).hex(),
        "scan_enabled": mc.read_reg(fd, mc.REG_SCAN_ENABLED, 1).hex(),
    }
    mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0)
    mc.write_reg(fd, mc.REG_SCAN_DETAIL, 0)
    mc.write_reg(fd, mc.REG_FRAME_CONTENT, mc.FRAME_CONTENT_PRESSURE)
    metadata = mc.read_vs(fd, mc.REG_COMPRESSION_METADATA)
    grids = dp.grids_for_record({"compression_metadata": metadata})
    mc.write_reg(fd, mc.REG_SCAN_ENABLED, 1)
    return initial, grids


def pressure_restore(fd: int, initial: dict[str, str]) -> None:
    """Restore scan registers after a standalone LED animation run."""
    try:
        mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0)
    except Exception as exc:
        print(f"warning: failed to stop scanning: {exc}", file=sys.stderr)
    for key, reg in (
        ("scan_detail", mc.REG_SCAN_DETAIL),
        ("frame_content", mc.REG_FRAME_CONTENT),
    ):
        try:
            mc.write_reg(fd, reg, int(initial[key], 16))
        except Exception as exc:
            print(f"warning: failed to restore {key}: {exc}", file=sys.stderr)
    try:
        mc.write_reg(fd, mc.REG_SCAN_ENABLED, int(initial["scan_enabled"], 16))
    except Exception as exc:
        print(f"warning: failed to restore scan_enabled: {exc}", file=sys.stderr)


def read_pressure_frame(fd: int, grids: list[dp.Grid], timeout: float) -> dp.DecodedFrame | None:
    frame = mc.read_frame(fd, timeout=timeout)
    if not frame.get("checksum_ok"):
        return None
    body = dp.frame_pressure_body(frame)
    if body is None:
        return None
    decoded, _errors = dp.infer_frame(body, grids)
    return decoded


def total_pressure(decoded: dp.DecodedFrame) -> float:
    return float(sum(max(0, value) for value in decoded.values))


def mode_values_for_total_force(args: argparse.Namespace, info: LedInfo, total_force: float, state: dict[str, object]) -> list[int]:
    """Compute LED values for modes driven by one total-force scalar."""
    response = log_pressure_response(total_force, args.pressure_ref, args.pressure_floor)
    if args.mode_name == "glow":
        return glow_values(info.count, info.max_brightness, total_force, args.pressure_ref, args.pressure_floor)
    if args.mode_name == "meter":
        return meter_values(info.count, info.max_brightness, total_force, args.pressure_ref, args.pressure_floor)
    if args.mode_name == "kitt":
        phase = float(state.get("phase", 0.0))
        phase += args.kitt_min_step + response * (args.kitt_max_step - args.kitt_min_step)
        state["phase"] = phase
        return kitt_values(info.count, info.max_brightness, phase)
    if args.mode_name == "twinkle":
        rng = state.setdefault("rng", random.Random(args.seed))
        values = state.setdefault("twinkle", [0.0] * info.count)
        assert isinstance(rng, random.Random)
        assert isinstance(values, list)
        values = twinkle_step(values, info.max_brightness, response, (), rng)
        state["twinkle"] = values
        return [clamp_int(value, 0, info.max_brightness) for value in values]
    raise ValueError(f"mode {args.mode_name!r} cannot use total force only")


def mode_values(args: argparse.Namespace, info: LedInfo, decoded: dp.DecodedFrame, state: dict[str, object]) -> list[int]:
    """Compute LED values for modes that may need the full pressure frame."""
    total = total_pressure(decoded)
    if args.mode_name == "glow":
        return mode_values_for_total_force(args, info, total, state)
    if args.mode_name == "meter":
        return mode_values_for_total_force(args, info, total, state)
    if args.mode_name == "columns":
        return column_values(decoded, info.count, info.max_brightness, args.pressure_ref, args.pressure_floor, args.column_threshold)
    if args.mode_name == "pulse":
        phases = state.setdefault("phases", [0.0] * info.count)
        assert isinstance(phases, list)
        column_responses = pressure_column_responses(
            decoded, info.count, args.pressure_ref, args.pressure_floor, args.column_threshold
        )
        for i, column_response in enumerate(column_responses):
            shaped_response = column_response ** args.pulse_response_gamma
            phases[i] = float(phases[i]) + args.pulse_min_step + shaped_response * (args.pulse_max_step - args.pulse_min_step)
        state["phases"] = phases
        return pulse_values(info.max_brightness, phases)
    if args.mode_name == "kitt":
        return mode_values_for_total_force(args, info, total, state)
    if args.mode_name == "twinkle":
        return mode_values_for_total_force(args, info, total, state)
    raise SystemExit(f"unknown mode: {args.mode_name}")


def command_mode(args: argparse.Namespace) -> int:
    device = args.device or mc.find_device()
    runner = LedRunner()
    signal.signal(signal.SIGINT, runner.stop)
    signal.signal(signal.SIGTERM, runner.stop)

    fd = mc.open_serial(device)
    initial: dict[str, str] = {}
    try:
        info = read_led_info(fd)
        initial, grids = pressure_setup(fd)
        state: dict[str, object] = {}
        next_update = 0.0
        while runner.running:
            decoded = read_pressure_frame(fd, grids, timeout=args.read_timeout)
            if decoded is None:
                continue
            now = time.monotonic()
            if now < next_update:
                continue
            values = mode_values(args, info, decoded, state)
            write_led_values(fd, info, values)
            next_update = now + 1.0 / max(1.0, args.update_rate)
    finally:
        mc.clear_leds_safely(fd)
        if initial:
            pressure_restore(fd, initial)
        mc.os.close(fd)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control the Sensel Morph white LED strip.")
    parser.add_argument("--device", default=None, help="Serial device path. Defaults to first /dev/cu.usbmodem*.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="Read LED count, brightness range, and current values.").set_defaults(func=command_info)

    mode_parser = sub.add_parser("mode", help="Run a pressure-responsive LED mode until Ctrl-C.")
    mode_parser.add_argument("mode_name", choices=("glow", "pulse", "kitt", "twinkle", "columns", "meter"))
    mode_parser.add_argument("--pressure-ref", type=float, default=DEFAULT_PRESSURE_REF, help="Pressure total mapped near full-scale.")
    mode_parser.add_argument("--pressure-floor", type=float, default=DEFAULT_PRESSURE_FLOOR, help="Low pressure scale used by log response.")
    mode_parser.add_argument("--column-threshold", type=float, default=DEFAULT_PRESSURE_FLOOR, help="Per-column pressure sum below this is treated as zero.")
    mode_parser.add_argument("--update-rate", type=float, default=30.0, help="Maximum LED updates per second.")
    mode_parser.add_argument("--read-timeout", type=float, default=1.0)
    mode_parser.add_argument("--pulse-min-step", type=float, default=0.025)
    mode_parser.add_argument("--pulse-max-step", type=float, default=1.25)
    mode_parser.add_argument("--pulse-response-gamma", type=float, default=2.5, help="Higher values make modest pressure affect pulse speed less.")
    mode_parser.add_argument("--kitt-min-step", type=float, default=0.08)
    mode_parser.add_argument("--kitt-max-step", type=float, default=1.4)
    mode_parser.add_argument("--seed", type=int, default=None)
    mode_parser.set_defaults(func=command_mode)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
