from __future__ import annotations

import json
import pathlib
import struct
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import decode_pressure as dp  # noqa: E402
import sensel_morph_osc as smo  # noqa: E402


class SenselMorphOscTest(unittest.TestCase):
    def test_pressure_output_resolution_names(self) -> None:
        self.assertEqual(smo.PRESSURE_SIZES["high"], (185, 105))
        self.assertEqual(smo.PRESSURE_SIZES["med"], (93, 53))
        self.assertEqual(smo.PRESSURE_SIZES["low"], (47, 27))

    def test_pressure_resolution_selects_device_scan_detail(self) -> None:
        self.assertEqual(smo.scan_detail_value_for_pressure_res("high"), 0)
        self.assertEqual(smo.scan_detail_value_for_pressure_res("med"), 1)
        self.assertEqual(smo.scan_detail_value_for_pressure_res("low"), 1)

    def test_scan_detail_argument_is_not_public(self) -> None:
        parser = smo.build_arg_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--scan-detail", "medium"])

    def test_default_chunk_size_is_udp_safe(self) -> None:
        parser = smo.build_arg_parser()
        args = parser.parse_args(["--pressure"])
        self.assertEqual(args.chunk_size, 4096)
        self.assertLess(args.chunk_size, 185 * 105)

    def test_accelerometer_is_always_requested(self) -> None:
        parser = smo.build_arg_parser()
        self.assertEqual(smo.frame_content_mask(parser.parse_args([])), 0x08)
        self.assertEqual(smo.frame_content_mask(parser.parse_args(["--pressure", "--contacts"])), 0x0D)

    def test_led_spatial_pressure_mode_requests_pressure_without_broadcasting_it(self) -> None:
        parser = smo.build_arg_parser()
        args = parser.parse_args(["--led-mode", "pulse"])

        self.assertEqual(args.led_mode_name, "pulse")
        self.assertFalse(args.pressure)
        self.assertEqual(smo.frame_content_mask(args), 0x09)

    def test_led_total_force_modes_use_contacts_without_requesting_pressure(self) -> None:
        parser = smo.build_arg_parser()

        for mode in ("glow", "kitt", "meter", "twinkle"):
            with self.subTest(mode=mode):
                args = parser.parse_args(["--contacts", "--led-mode", mode])
                self.assertEqual(smo.frame_content_mask(args), 0x0C)

        args = parser.parse_args(["--contacts", "--led-mode", "columns"])
        self.assertEqual(smo.frame_content_mask(args), 0x0D)

        args = parser.parse_args(["--led-mode", "meter"])
        self.assertEqual(smo.frame_content_mask(args), 0x09)

    def test_led_all_mode_does_not_request_pressure(self) -> None:
        parser = smo.build_arg_parser()
        args = parser.parse_args(["--led-mode", "all", "123"])

        self.assertEqual(args.led_mode_name, "all")
        self.assertEqual(args.led_all_brightness, 123)
        self.assertEqual(smo.frame_content_mask(args), 0x08)

    def test_led_prefixed_arguments_map_to_led_mode_namespace(self) -> None:
        parser = smo.build_arg_parser()
        args = parser.parse_args(
            [
                "--led-mode",
                "pulse",
                "--led-pressure-floor",
                "70",
                "--led-frame-interval",
                "3",
                "--led-pulse-max-step",
                "1.4",
                "--led-seed",
                "9",
            ]
        )
        led_args = smo.led_args_for_osc(args)

        self.assertEqual(led_args.mode_name, "pulse")
        self.assertEqual(led_args.pressure_floor, 70)
        self.assertEqual(led_args.pulse_max_step, 1.4)
        self.assertEqual(led_args.seed, 9)
        self.assertEqual(args.led_frame_interval, 3)

    def test_led_frame_interval_defaults_to_every_fourth_frame(self) -> None:
        parser = smo.build_arg_parser()
        args = parser.parse_args(["--led-mode", "columns"])

        self.assertTrue(smo.led_frame_due(args, 0))
        self.assertFalse(smo.led_frame_due(args, 1))
        self.assertFalse(smo.led_frame_due(args, 2))
        self.assertFalse(smo.led_frame_due(args, 3))
        self.assertTrue(smo.led_frame_due(args, 4))

    def test_led_mode_all_requires_brightness(self) -> None:
        parser = smo.build_arg_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--led-mode", "all"])

    def test_total_force_led_values_can_use_contact_force_without_pressure(self) -> None:
        args = smo.build_arg_parser().parse_args(["--contacts", "--led-mode", "meter"])
        sender = smo.SenselMorphOsc(args)
        sender.led_info = smo.ledctl.LedInfo(count=4, reg_size=1, max_brightness=100, values=[0, 0, 0, 0])
        frame = smo.LiveFrame(
            frame_id=1,
            timestamp=0,
            content_mask=0x0C,
            contacts=dp.DecodedContacts(
                contacts=[
                    dp.DecodedContact(id=0, state=2, x_mm=0, y_mm=0, force=5000, area=1),
                    dp.DecodedContact(id=1, state=2, x_mm=0, y_mm=0, force=5000, area=1),
                ],
                bytes_used=0,
                contact_mask=0x0F,
            ),
        )

        values = sender.led_values_for_frame(frame)

        self.assertIsNotNone(values)
        self.assertGreater(sum(values or []), 0)

    def test_uint8_pressure_defaults_to_absolute_clamped_blob(self) -> None:
        blob, max_value = smo.pack_pressure([0, 5, 10], "uint8", force_scale=1.0)
        self.assertEqual(blob, bytes([0, 5, 10]))
        self.assertEqual(max_value, 10)

    def test_uint8_pressure_normalize_is_explicit(self) -> None:
        blob, max_value = smo.pack_pressure([0, 5, 10], "uint8", force_scale=1.0, normalize=True)
        self.assertEqual(blob, bytes([0, 128, 255]))
        self.assertEqual(max_value, 10)

    def test_uint16_pressure_is_little_endian_plain_blob(self) -> None:
        blob, max_value = smo.pack_pressure([0, 513, 70000], "uint16", force_scale=1.0)
        self.assertEqual(blob, struct.pack("<HHH", 0, 513, 65535))
        self.assertEqual(max_value, 70000)

    def test_pressure_calibration_subtracts_dark_and_applies_gain(self) -> None:
        calibration = smo.PressureCalibration(
            path=pathlib.Path("calibration_ABC.json"),
            serial_number="ABC",
            width=2,
            height=2,
            dark=smo.np.asarray([1, 2, 30, 4], dtype=smo.np.float64),
            gain=smo.np.asarray([2.0, 0.5, 9.0, 1.0], dtype=smo.np.float64),
            gain_key="gain",
        )

        self.assertEqual(smo.apply_pressure_calibration([3, 6, 10, 4], calibration), [4.0, 2.0, 0.0, 0.0])

    def test_kernel_fit_calibration_applies_source_gain_before_expansion(self) -> None:
        grid = dp.Grid("tiny", 2, 2, 1, 1)
        decoded = dp.DecodedFrame(
            grid=grid,
            base=0,
            values=[10, 10, 10, 10],
            bytes_used=0,
            varints=0,
            zero_runs=0,
            nonzero_values=4,
        )
        calibration = smo.PressureCalibration(
            path=pathlib.Path("calibration_ABC.json"),
            serial_number="ABC",
            width=2,
            height=2,
            dark=smo.np.asarray([0, 0, 0, 0], dtype=smo.np.float64),
            gain=smo.np.asarray([1, 1, 1, 1], dtype=smo.np.float64),
            gain_key="gain",
            coverage=smo.np.asarray([1, 1, 1, 1], dtype=smo.np.float64),
            light=smo.np.asarray([10, 20, 10, 20], dtype=smo.np.float64),
            target=10,
            min_gain=0.5,
            max_gain=2.0,
            source_maps={},
        )

        values, width, height = smo.expand_calibrated_pressure(decoded, calibration)
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(len(values), 4)
        self.assertEqual(len(calibration.source_maps), 1)
        source = next(iter(calibration.source_maps.values()))
        self.assertEqual(source.gain.shape, (2, 2))
        self.assertLess(source.gain[0, 1], source.gain[0, 0])
        self.assertEqual(source.gain[0, 1], 0.5)

    def test_load_calibration_uses_gain_and_checks_serial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "calibration_2044B8374E33.json"
            cells = 185 * 105
            path.write_text(
                json.dumps(
                    {
                        "device_serial": "2044B8374E33",
                        "width": 185,
                        "height": 105,
                        "dark": [1] * cells,
                        "light": [4] * cells,
                        "gain": [2.0] * cells,
                        "coverage": [1] * cells,
                        "target": 4,
                    }
                )
            )

            calibration = smo.load_pressure_calibration(str(path), "2044B8374E33")
            self.assertEqual(calibration.gain_key, "gain")
            self.assertEqual(calibration.gain[0], 2.0)
            self.assertEqual(calibration.light[0], 4)
            self.assertEqual(calibration.target, 4)

            with self.assertRaises(SystemExit):
                smo.load_pressure_calibration(str(path), "DIFFERENT")

    def test_load_calibration_rejects_filename_serial_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "calibration_OTHER.json"
            cells = 185 * 105
            path.write_text(
                json.dumps(
                    {
                        "device_serial": "2044B8374E33",
                        "width": 185,
                        "height": 105,
                        "dark": [0] * cells,
                        "gain": [1.0] * cells,
                    }
                )
            )

            with self.assertRaises(SystemExit):
                smo.load_pressure_calibration(str(path), "2044B8374E33")

    def test_load_calibration_directory_uses_serial_named_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cells = 185 * 105
            path = pathlib.Path(tmp) / "calibration_2044B8374E33.json"
            path.write_text(
                json.dumps(
                    {
                        "device_serial": "2044B8374E33",
                        "width": 185,
                        "height": 105,
                        "dark": [0] * cells,
                        "gain": [1.25] * cells,
                    }
                )
            )

            calibration = smo.load_pressure_calibration(tmp, "2044B8374E33")
            self.assertEqual(calibration.path, path.resolve())
            self.assertEqual(calibration.gain[0], 1.25)

    def test_status_message_includes_usb_serial(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.messages = []

            def send_message(self, address, args) -> None:
                self.messages.append((address, args))

        args = smo.build_arg_parser().parse_args(["--pressure"])
        sender = smo.SenselMorphOsc(args)
        sender.client = Client()
        sender.device_serial = "2044B8374E33"
        sender.send_status("/dev/cu.usbmodem2044B8374E331", 0x09)

        self.assertEqual(sender.client.messages[0][0], "/sensel_morph/status")
        self.assertEqual(sender.client.messages[0][1][5], "2044B8374E33")
        self.assertEqual(sender.client.messages[0][1][6], 0)

    def test_status_message_marks_calibrated_output(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.messages = []

            def send_message(self, address, args) -> None:
                self.messages.append((address, args))

        args = smo.build_arg_parser().parse_args(["--pressure"])
        sender = smo.SenselMorphOsc(args)
        sender.client = Client()
        sender.device_serial = "2044B8374E33"
        sender.calibration = smo.PressureCalibration(
            path=pathlib.Path("calibration_2044B8374E33.json"),
            serial_number="2044B8374E33",
            width=185,
            height=105,
            dark=smo.np.zeros(185 * 105),
            gain=smo.np.ones(185 * 105),
            gain_key="gain",
        )
        sender.send_status("/dev/cu.usbmodem2044B8374E331", 0x09)

        self.assertEqual(sender.client.messages[0][0], "/sensel_morph/status")
        self.assertEqual(sender.client.messages[0][1][6], 1)

    def test_rle_encode_uses_count_value_pairs(self) -> None:
        self.assertEqual(smo.rle_encode(bytes([7] * 300)), bytes([255, 7, 45, 7]))
        self.assertEqual(smo.rle_encode(bytes([1, 2, 1, 2])), bytes([1, 1, 1, 2, 1, 1, 1, 2]))

    def test_rle_mode_always_uses_rle_address(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.messages = []

            def send_message(self, address, args) -> None:
                self.messages.append((address, args))

        args = smo.build_arg_parser().parse_args(["--pressure", "--rle"])
        sender = smo.SenselMorphOsc(args)
        sender.client = Client()
        sender.send_raster_blob("/sensel_morph/pressure", [12, 2, 1, 8, 2.0], bytes([1, 2]))

        self.assertEqual(sender.client.messages[0][0], "/sensel_morph/pressure_rle")
        self.assertEqual(sender.client.messages[0][1][:6], [12, 2, 1, 8, 2.0, 2])
        self.assertEqual(sender.client.messages[0][1][6], bytes([1, 1, 1, 2]))

    def test_med_pressure_from_high_grid_uses_decoded_grid_values(self) -> None:
        grid = dp.Grid("high", 93, 53, 2, 2)
        values = list(range(grid.cells))
        decoded = dp.DecodedFrame(
            grid=grid,
            base=0,
            values=values,
            bytes_used=0,
            varints=0,
            zero_runs=0,
            nonzero_values=0,
        )
        out, width, height = smo.pressure_values_for_output(decoded, "med")
        self.assertEqual((width, height), (93, 53))
        self.assertEqual(out, values)

    def test_med_pressure_from_medium_grid_uses_kernel_expansion_not_nearest_duplication(self) -> None:
        grid = dp.Grid("medium", 47, 27, 4, 4)
        values = list(range(grid.cells))
        decoded = dp.DecodedFrame(
            grid=grid,
            base=0,
            values=values,
            bytes_used=0,
            varints=0,
            zero_runs=0,
            nonzero_values=0,
        )
        out, width, height = smo.pressure_values_for_output(decoded, "med")

        self.assertEqual((width, height), (93, 53))
        self.assertNotEqual(out[0], out[1])
        self.assertNotEqual(out[1], out[2])

    def test_resize_nearest_samples_destination_cell_centers(self) -> None:
        values = list(range(10))
        self.assertEqual(
            smo.resize_values_nearest(values, src_w=10, src_h=1, dst_w=5, dst_h=1),
            [1, 3, 5, 7, 9],
        )

    def test_raster_peaks_use_pressure_cell_centers(self) -> None:
        peaks = smo.raster_peaks_by_label(
            pressure_values=[0, 4, 0, 8, 0, 1],
            pressure_w=3,
            pressure_h=2,
            label_blob=bytes([255, 7, 255, 7, 7, 255]),
            label_w=3,
            label_h=2,
        )

        self.assertIn(7, peaks)
        self.assertAlmostEqual(peaks[7].x_mm, (0.5 / 3) * smo.ACTIVE_W_MM)
        self.assertAlmostEqual(peaks[7].y_mm, (1.5 / 2) * smo.ACTIVE_H_MM)
        self.assertEqual(peaks[7].force, 8)

    def test_raster_peaks_search_before_uint8_clipping(self) -> None:
        peaks = smo.raster_peaks_by_label(
            pressure_values=[0, 256, 257, 0],
            pressure_w=4,
            pressure_h=1,
            label_blob=bytes([255, 2, 2, 255]),
            label_w=4,
            label_h=1,
        )

        self.assertAlmostEqual(peaks[2].x_mm, (2.5 / 4) * smo.ACTIVE_W_MM)
        self.assertEqual(peaks[2].force, 257)

    def test_raster_peak_force_uses_pressure_force_scale(self) -> None:
        peaks = smo.raster_peaks_by_label(
            pressure_values=[0, 80],
            pressure_w=2,
            pressure_h=1,
            label_blob=bytes([255, 1]),
            label_w=2,
            label_h=1,
            force_scale=8.0,
        )

        self.assertEqual(peaks[1].force, 10)

    def test_raster_ellipses_use_pressure_weighted_raw_moments(self) -> None:
        grid = dp.Grid("test", 3, 3, 1, 1)
        pressure = dp.DecodedFrame(
            grid=grid,
            base=0,
            values=[
                0, 1, 0,
                0, 1, 0,
                0, 1, 0,
            ],
            bytes_used=0,
            varints=0,
            zero_runs=0,
            nonzero_values=3,
        )
        labels = dp.DecodedLabels(
            grid=grid,
            values=[
                255, 4, 255,
                255, 4, 255,
                255, 4, 255,
            ],
            bytes_used=0,
        )

        ellipses = smo.raster_ellipses_by_label(pressure, labels)

        self.assertIn(4, ellipses)
        self.assertAlmostEqual(ellipses[4].x_mm, 0.5 * smo.ACTIVE_W_MM)
        self.assertAlmostEqual(ellipses[4].y_mm, 0.5 * smo.ACTIVE_H_MM)
        self.assertAlmostEqual(ellipses[4].orientation_deg, 0.0)
        self.assertAlmostEqual(ellipses[4].major_axis_mm, 4.0 * ((2 * (smo.ACTIVE_H_MM / 3) ** 2 / 3) ** 0.5))
        self.assertAlmostEqual(ellipses[4].minor_axis_mm, 0.0)

    def test_send_contacts_prefers_raster_ellipse_geometry(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.messages = []

            def send_message(self, address, args) -> None:
                self.messages.append((address, args))

        contact = dp.DecodedContact(
            id=4,
            state=2,
            x_mm=1.0,
            y_mm=2.0,
            force=3.0,
            area=9.0,
            orientation_deg=90.0,
            major_axis_mm=99.0,
            minor_axis_mm=88.0,
        )
        frame = smo.LiveFrame(
            frame_id=12,
            timestamp=0,
            content_mask=0x04,
            contacts=dp.DecodedContacts(contacts=[contact], bytes_used=0, contact_mask=0x01),
        )
        args = smo.build_arg_parser().parse_args(["--contacts"])
        sender = smo.SenselMorphOsc(args)
        sender.client = Client()
        sender.send_contacts(
            frame,
            raster_ellipses={
                4: smo.RasterEllipse(
                    x_mm=10.0,
                    y_mm=20.0,
                    orientation_deg=30.0,
                    major_axis_mm=40.0,
                    minor_axis_mm=5.0,
                    area_cells=3,
                )
            },
        )

        message = next(item for item in sender.client.messages if item[0] == "/sensel_morph/contact")
        self.assertEqual(message[1][3:10], [10.0, 20.0, 3.0, 9, 30.0, 40.0, 5.0])

    def test_contact_summary_uses_raster_geometry(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.messages = []

            def send_message(self, address, args) -> None:
                self.messages.append((address, args))

        contacts = [
            dp.DecodedContact(id=0, state=2, x_mm=0.0, y_mm=0.0, force=1.0, area=10.0),
            dp.DecodedContact(id=1, state=2, x_mm=0.0, y_mm=0.0, force=3.0, area=20.0),
        ]
        args = smo.build_arg_parser().parse_args(["--contacts"])
        sender = smo.SenselMorphOsc(args)
        sender.client = Client()
        sender.send_contact_summary(
            99,
            contacts,
            {
                0: smo.RasterEllipse(10.0, 10.0, 0.0, 2.0, 1.0, 3),
                1: smo.RasterEllipse(30.0, 10.0, 0.0, 2.0, 1.0, 3),
            },
        )

        message = sender.client.messages[0]
        self.assertEqual(message[0], "/sensel_morph/contact_summary")
        self.assertEqual(message[1][:2], [99, 2])
        self.assertAlmostEqual(message[1][2], 20.0 / smo.ACTIVE_W_MM)
        self.assertAlmostEqual(message[1][3], 10.0 / smo.ACTIVE_H_MM)
        self.assertAlmostEqual(message[1][4], 25.0 / smo.ACTIVE_W_MM)
        self.assertAlmostEqual(message[1][5], 10.0 / smo.ACTIVE_H_MM)
        self.assertEqual(message[1][6:9], [4.0, 2.0, 15.0])
        self.assertEqual(message[1][9:11], [10.0, 10.0])

    def test_compat_parser_accepts_comma_separated_modes(self) -> None:
        self.assertEqual(smo.parse_compat(["morphosc,senselosc"]), ["morphosc", "senselosc"])
        self.assertEqual(smo.parse_compat(["none"]), [])

if __name__ == "__main__":
    unittest.main()
