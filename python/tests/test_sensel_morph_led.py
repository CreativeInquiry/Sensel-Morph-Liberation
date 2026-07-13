from __future__ import annotations

import pathlib
import random
import math
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import decode_pressure as dp  # noqa: E402
import sensel_morph_led as led  # noqa: E402


class SenselMorphLedTest(unittest.TestCase):
    def test_log_pressure_response_keeps_light_touches_visible(self) -> None:
        self.assertEqual(led.log_pressure_response(0), 0)
        self.assertGreater(led.log_pressure_response(50), 0.10)
        self.assertGreater(led.log_pressure_response(200), led.log_pressure_response(50))
        self.assertGreater(led.log_pressure_response(2500), led.log_pressure_response(200))
        self.assertEqual(led.log_pressure_response(100000), 1.0)

    def test_meter_values_are_left_to_right_level(self) -> None:
        values = led.meter_values(4, 100, total_pressure=15000, pressure_ref=15000, pressure_floor=50)
        self.assertEqual(values, [100, 100, 100, 100])
        values = led.meter_values(4, 100, total_pressure=0, pressure_ref=15000, pressure_floor=50)
        self.assertEqual(values, [0, 0, 0, 0])

    def test_meter_values_use_gamma_to_reduce_low_pressure(self) -> None:
        linear_norm = led.log_pressure_response(500, pressure_ref=15000, pressure_floor=50)
        linear_level = linear_norm * 8 * 100
        shaped = led.meter_values(8, 100, total_pressure=500, pressure_ref=15000, pressure_floor=50)
        self.assertLess(sum(shaped), linear_level)
        self.assertEqual(led.METER_GAMMA, 2.5)

    def test_glow_values_have_ten_percent_floor(self) -> None:
        values = led.glow_values(4, 100, total_pressure=0, pressure_ref=15000, pressure_floor=50)
        self.assertEqual(values, [10, 10, 10, 10])

    def test_column_values_group_pressure_by_x_position(self) -> None:
        grid = dp.Grid("tiny", 4, 1, 1, 1)
        decoded = dp.DecodedFrame(
            grid=grid,
            base=0,
            values=[0, 1000, 0, 0],
            bytes_used=0,
            varints=0,
            zero_runs=0,
            nonzero_values=1,
        )
        values = led.column_values(decoded, led_count=4, max_brightness=100, pressure_ref=15000, pressure_floor=50)
        self.assertEqual(values[0], 0)
        self.assertGreater(values[1], 0)
        self.assertEqual(values[2], 0)
        self.assertEqual(values[3], 0)

    def test_column_values_gate_low_noise(self) -> None:
        grid = dp.Grid("tiny", 4, 1, 1, 1)
        decoded = dp.DecodedFrame(
            grid=grid,
            base=0,
            values=[0, 20, 0, 0],
            bytes_used=0,
            varints=0,
            zero_runs=0,
            nonzero_values=1,
        )
        values = led.column_values(
            decoded, led_count=4, max_brightness=100, pressure_ref=15000, pressure_floor=50, threshold=50
        )
        self.assertEqual(values, [0, 0, 0, 0])

    def test_pulse_values_never_enter_bottom_luminance_range(self) -> None:
        values = led.pulse_values(100, [3.0 * math.pi / 2.0, 0.0])
        self.assertEqual(values[0], 10)
        self.assertGreater(values[1], values[0])

    def test_pulse_default_top_speed_is_fast(self) -> None:
        args = led.build_arg_parser().parse_args(["mode", "pulse"])
        self.assertEqual(args.pulse_max_step, 1.25)
        self.assertEqual(args.pulse_response_gamma, 2.5)
        self.assertFalse(hasattr(args, "meter_gamma"))

    def test_pulse_response_gamma_reduces_modest_pressure_speed(self) -> None:
        args = led.build_arg_parser().parse_args(["mode", "pulse"])
        modest_response = 0.5
        linear_step = args.pulse_min_step + modest_response * (args.pulse_max_step - args.pulse_min_step)
        shaped_step = args.pulse_min_step + (modest_response ** args.pulse_response_gamma) * (args.pulse_max_step - args.pulse_min_step)
        self.assertLess(shaped_step, linear_step)

    def test_total_force_modes_do_not_need_pressure_grid(self) -> None:
        args = led.build_arg_parser().parse_args(["mode", "kitt"])
        info = led.LedInfo(count=5, reg_size=1, max_brightness=100, values=[0, 0, 0, 0, 0])
        state: dict[str, object] = {}

        values = led.mode_values_for_total_force(args, info, 5000, state)

        self.assertEqual(len(values), 5)
        self.assertGreater(sum(values), 0)
        self.assertIn("phase", state)

    def test_twinkle_uses_global_pressure_not_columns(self) -> None:
        rng_a = random.Random(4)
        rng_b = random.Random(4)
        rng_c = random.Random(4)
        dark = led.twinkle_step([0.0] * 4, 100, 0.0, [0.0, 0.0, 0.0, 0.0], rng_a)
        local_only = led.twinkle_step([0.0] * 4, 100, 0.0, [0.0, 1.0, 0.0, 0.0], rng_b)
        global_pressure = led.twinkle_step([0.0] * 4, 100, 1.0, [0.0, 0.0, 0.0, 0.0], rng_c)
        self.assertEqual(dark[1], 0)
        self.assertEqual(local_only[1], 0)
        self.assertEqual(global_pressure[1], 100)
        self.assertEqual(led.TWINKLE_IDLE_PROBABILITY, 0.010)
        self.assertEqual(led.TWINKLE_PRESSURE_PROBABILITY, 0.120)

    def test_one_shot_all_and_set_commands_are_removed(self) -> None:
        parser = led.build_arg_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["all", "64"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["set", "3", "100"])

    def test_encode_decode_one_byte_led_values(self) -> None:
        encoded = led.encode_led_values([0, 5, 300], reg_size=1, max_brightness=255)
        self.assertEqual(encoded, bytes([0, 5, 255]))
        self.assertEqual(led.decode_led_values(encoded, count=3, reg_size=1), [0, 5, 255])

    def test_led_write_uses_pipelined_encoded_led_payload(self) -> None:
        calls = []
        original = led.mc.write_vs_pipelined

        def fake_write_vs_pipelined(fd, reg, data, timeout=2.0):
            calls.append((fd, reg, data, timeout))

        try:
            led.mc.write_vs_pipelined = fake_write_vs_pipelined
            info = led.LedInfo(count=3, reg_size=1, max_brightness=100, values=[0, 0, 0])
            led.write_led_values(12, info, [0, 50, 300], timeout=0.25)
        finally:
            led.mc.write_vs_pipelined = original

        self.assertEqual(calls, [(12, led.REG_LED_BRIGHTNESS, bytes([0, 50, 100]), 0.25)])

    def test_kitt_values_has_bright_moving_center(self) -> None:
        values = led.kitt_values(5, 100, phase=2.0)
        self.assertEqual(values[2], 100)
        self.assertGreater(values[1], values[0])
        self.assertGreater(values[3], values[4])


if __name__ == "__main__":
    unittest.main()
