from __future__ import annotations

import json
import pathlib
import sys
import unittest


PYTHON_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parent
sys.path.insert(0, str(PYTHON_ROOT / "tools"))

import decode_pressure as dp  # noqa: E402


def load_first_decoded(capture_name: str) -> tuple[dp.DecodedFrame, dict[str, float | int | None]]:
    record = json.loads((REPO_ROOT / "captures" / "pressure" / capture_name).read_text())
    grids = dp.grids_for_record(record)
    for frame in record["frames"]:
        body = dp.frame_pressure_body(frame)
        if body is None:
            continue
        decoded, _ = dp.infer_frame(body, grids)
        if decoded is not None:
            return decoded, dp.metrics(decoded)
    raise AssertionError(f"no decoded frames in {capture_name}")


def centroid(values: list[float], width: int) -> tuple[float, float]:
    total = sum(values)
    if total == 0:
        raise AssertionError("zero-valued frame has no centroid")
    sx = 0.0
    sy = 0.0
    for index, value in enumerate(values):
        y, x = divmod(index, width)
        sx += x * value
        sy += y * value
    return sx / total, sy / total


class PressureDecodeTest(unittest.TestCase):
    def test_baseline_zero_run_decodes_medium_grid(self) -> None:
        decoded, metrics = load_first_decoded("20260703_230552_baseline_no_touch.json")
        self.assertEqual((decoded.grid.cols, decoded.grid.rows), (47, 27))
        self.assertEqual(decoded.base, 3)
        self.assertEqual(decoded.bytes_used, 3)
        self.assertEqual(metrics["sum"], 0)
        self.assertEqual(metrics["nonzero"], 0)

    def test_corner_holds_land_in_expected_quadrants(self) -> None:
        cases = [
            ("20260703_231245_single_finger_top_left_inset_firm_hold.json", (0, 20), (0, 15)),
            ("20260703_231317_single_finger_top_right_inset_firm_hold.json", (75, 93), (0, 15)),
            ("20260703_231347_single_finger_bottom_left_inset_firm_hold.json", (0, 20), (38, 53)),
            ("20260703_231414_single_finger_bottom_right_inset_firm_hold.json", (75, 93), (38, 53)),
        ]
        for capture_name, x_range, y_range in cases:
            with self.subTest(capture_name=capture_name):
                decoded, metrics = load_first_decoded(capture_name)
                self.assertEqual((decoded.grid.cols, decoded.grid.rows), (93, 53))
                cx = metrics["centroid_x"]
                cy = metrics["centroid_y"]
                self.assertIsNotNone(cx)
                self.assertIsNotNone(cy)
                self.assertGreaterEqual(float(cx), x_range[0])
                self.assertLessEqual(float(cx), x_range[1])
                self.assertGreaterEqual(float(cy), y_range[0])
                self.assertLessEqual(float(cy), y_range[1])

    def test_high_detail_expands_to_sensor_dimensions(self) -> None:
        decoded, _ = load_first_decoded("20260703_231317_single_finger_top_right_inset_firm_hold.json")
        expanded, width, height = dp.expand_pressure(decoded)
        self.assertEqual((width, height), (185, 105))
        cx, cy = centroid(expanded, width)
        self.assertGreater(cx, 150)
        self.assertLess(cy, 20)

    def test_numpy_expansion_matches_python_expansion(self) -> None:
        grid = dp.Grid("tiny", 4, 3, 2, 2)
        decoded = dp.DecodedFrame(
            grid=grid,
            base=0,
            values=[0, 3, 0, 1, 2, 5, 7, 0, 0, 0, 4, 8],
            bytes_used=0,
            varints=0,
            zero_runs=0,
            nonzero_values=6,
        )
        numpy_values, width, height = dp.expand_pressure_scaled_numpy(decoded)
        python_values, python_width, python_height = dp.expand_pressure_scaled_python(decoded)
        self.assertEqual((width, height), (python_width, python_height))
        self.assertEqual(len(numpy_values), len(python_values))
        for actual, expected in zip(numpy_values, python_values):
            self.assertAlmostEqual(actual, expected, places=9)

    def test_label_rle_decodes_null_and_contact_runs(self) -> None:
        grid = dp.Grid("tiny", 5, 1, 1, 1)
        labels = dp.decode_label_body(bytes([2, 2, 0x01, 1]), grid)
        self.assertEqual(labels.values, [255, 255, 1, 1, 255])
        self.assertEqual(labels.bytes_used, 4)

    def test_pressure_labels_stress_capture_decodes_all_label_frames(self) -> None:
        record = json.loads(
            (
                REPO_ROOT
                / "captures"
                / "pressure_and_labels"
                / "20260704_011728_stress_test_palms_fingertips_merge_split_cross_pressure_labels_25s.json"
            ).read_text()
        )
        grids = dp.grids_for_record(record)
        self.assertEqual((grids[0].cols, grids[0].rows), (93, 53))

        decoded = 0
        for frame in record["frames"]:
            content_and_body = dp.frame_content_and_body(frame)
            self.assertIsNotNone(content_and_body)
            content_mask, body = content_and_body
            self.assertTrue(content_mask & 0x02)
            pressure, errors = dp.infer_frame(body, grids, require_all=False)
            self.assertIsNotNone(pressure, errors)
            assert pressure is not None
            labels = dp.decode_label_body(body[pressure.bytes_used :], pressure.grid)
            self.assertEqual(len(labels.values), pressure.grid.cells)
            decoded += 1

        self.assertEqual(decoded, 1029)

    def test_contact_section_is_parsed_before_pressure_labels(self) -> None:
        record = json.loads((REPO_ROOT / "captures" / "probes" / "frame_probe_20260703_225436.json").read_text())
        sections = dp.frame_sections(record["frame"])
        self.assertIsNotNone(sections)
        assert sections is not None
        self.assertEqual(sections.content_mask, 0x07)
        self.assertIsNotNone(sections.contacts)
        assert sections.contacts is not None
        self.assertEqual(sections.contacts.contact_mask, 0x01)
        self.assertEqual(len(sections.contacts.contacts), 1)
        self.assertEqual(sections.contacts.bytes_used, 18)
        self.assertEqual(len(sections.pressure_label_body), 30)

        contact = sections.contacts.contacts[0]
        self.assertEqual(contact.id, 0)
        self.assertEqual(contact.state, 1)
        self.assertAlmostEqual(contact.x_mm, 102.44140625)
        self.assertAlmostEqual(contact.y_mm, 62.54296875)
        self.assertAlmostEqual(contact.force, 70.125)
        self.assertEqual(contact.area, 67)
        self.assertAlmostEqual(contact.orientation_deg or 0, -41.0)
        self.assertAlmostEqual(contact.major_axis_mm or 0, 9.77734375)
        self.assertAlmostEqual(contact.minor_axis_mm or 0, 7.90234375)

        decoded, errors = dp.infer_frame(sections.pressure_label_body, dp.grids_for_record(record), require_all=False)
        self.assertIsNotNone(decoded, errors)
        assert decoded is not None
        labels = dp.decode_label_body(sections.pressure_label_body[decoded.bytes_used :], decoded.grid)
        self.assertEqual(len(labels.values), decoded.grid.cells)

    def test_full_contact_optional_fields_decode(self) -> None:
        contact_packet = bytes(
            [
                0x0F,
                0x01,
                0x03,
                0x02,
                *int(10.5 * 256).to_bytes(2, "little"),
                *int(20.25 * 256).to_bytes(2, "little"),
                *int(42.0 * 8).to_bytes(2, "little"),
                *int(17).to_bytes(2, "little"),
                *int(-12.5 * 16).to_bytes(2, "little", signed=True),
                *int(8.0 * 256).to_bytes(2, "little"),
                *int(4.0 * 256).to_bytes(2, "little"),
                *int(-1.5 * 256).to_bytes(2, "little", signed=True),
                *int(2.0 * 256).to_bytes(2, "little", signed=True),
                *int(-3.0 * 8).to_bytes(2, "little", signed=True),
                *int(-4).to_bytes(2, "little", signed=True),
                *int(5.0 * 256).to_bytes(2, "little"),
                *int(6.0 * 256).to_bytes(2, "little"),
                *int(15.0 * 256).to_bytes(2, "little"),
                *int(16.0 * 256).to_bytes(2, "little"),
                *int(11.0).to_bytes(2, "little"),
                *int(12.0).to_bytes(2, "little"),
                *int(99.0 * 8).to_bytes(2, "little"),
            ]
        )
        contacts = dp.parse_contacts(contact_packet, 0)
        self.assertEqual(contacts.contact_mask, 0x0F)
        self.assertEqual(contacts.bytes_used, len(contact_packet))
        contact = contacts.contacts[0]
        self.assertEqual(contact.id, 3)
        self.assertEqual(contact.state, 2)
        self.assertAlmostEqual(contact.x_mm, 10.5)
        self.assertAlmostEqual(contact.y_mm, 20.25)
        self.assertAlmostEqual(contact.force, 42.0)
        self.assertEqual(contact.area, 17)
        self.assertAlmostEqual(contact.orientation_deg or 0, -12.5)
        self.assertAlmostEqual(contact.major_axis_mm or 0, 8.0)
        self.assertAlmostEqual(contact.minor_axis_mm or 0, 4.0)
        self.assertAlmostEqual(contact.delta_x_mm or 0, -1.5)
        self.assertAlmostEqual(contact.delta_y_mm or 0, 2.0)
        self.assertAlmostEqual(contact.delta_force or 0, -3.0)
        self.assertAlmostEqual(contact.delta_area or 0, -4.0)
        self.assertAlmostEqual(contact.min_x_mm or 0, 5.0)
        self.assertAlmostEqual(contact.min_y_mm or 0, 6.0)
        self.assertAlmostEqual(contact.max_x_mm or 0, 15.0)
        self.assertAlmostEqual(contact.max_y_mm or 0, 16.0)
        self.assertAlmostEqual(contact.peak_x_mm or 0, 11.0)
        self.assertAlmostEqual(contact.peak_y_mm or 0, 12.0)
        self.assertAlmostEqual(contact.peak_force or 0, 99.0)


if __name__ == "__main__":
    unittest.main()
