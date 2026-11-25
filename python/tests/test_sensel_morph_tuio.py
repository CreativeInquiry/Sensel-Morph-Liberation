from __future__ import annotations

import math
import pathlib
import struct
import sys
import unittest
from unittest import mock

from pythonosc.osc_bundle import OscBundle


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import decode_pressure as dp  # noqa: E402
import sensel_morph_tuio as smt  # noqa: E402


def contact(
    contact_id: int = 0,
    state: int = smt.CONTACT_STATE_MOVE,
    x_mm: float = 115.0,
    y_mm: float = 65.0,
    force: float = 100.0,
    area: float = 1200.0,
    orientation_deg: float | None = 0.0,
    major_axis_mm: float | None = 23.0,
    minor_axis_mm: float | None = 13.0,
) -> dp.DecodedContact:
    return dp.DecodedContact(
        id=contact_id,
        state=state,
        x_mm=x_mm,
        y_mm=y_mm,
        force=force,
        area=area,
        orientation_deg=orientation_deg,
        major_axis_mm=major_axis_mm,
        minor_axis_mm=minor_axis_mm,
    )


def osc_type_tag(dgram: bytes) -> str:
    address_end = dgram.index(b"\x00")
    type_start = (address_end + 4) & ~3
    type_end = dgram.index(b"\x00", type_start)
    return dgram[type_start:type_end].decode("ascii")


def parsed_bundle(bundle: object) -> list[tuple[str, list[object]]]:
    parsed = OscBundle(bundle.dgram)
    return [(message.address, message.params) for message in parsed]


class SenselMorphTuioTest(unittest.TestCase):
    def test_parser_defaults_are_tuio_reference_defaults(self) -> None:
        args = smt.build_arg_parser().parse_args([])

        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 3333)
        self.assertFalse(args.blob)
        self.assertEqual(args.update_mode, "full")
        self.assertEqual(args.scan_detail, "high")
        self.assertEqual(args.source, "sensel_morph")
        self.assertFalse(args.no_source)
        self.assertEqual(args.min_contact_force, 10.0)
        self.assertEqual(args.finger_size, 20.0)

    def test_parser_exposes_blob_update_mode_scan_detail_and_inversion(self) -> None:
        args = smt.build_arg_parser().parse_args(
            [
                "--blob",
                "--update-mode",
                "changed",
                "--scan-detail",
                "low",
                "--min-contact-force",
                "12.5",
                "--finger-size",
                "18.0",
                "--invert-x",
                "--invert-y",
                "--invert-angle",
            ]
        )

        self.assertTrue(args.blob)
        self.assertEqual(args.update_mode, "changed")
        self.assertEqual(args.scan_detail, "low")
        self.assertEqual(args.min_contact_force, 12.5)
        self.assertEqual(args.finger_size, 18.0)
        self.assertTrue(args.invert_x)
        self.assertTrue(args.invert_y)
        self.assertTrue(args.invert_angle)
        self.assertEqual(smt.SCAN_DETAILS["low"], 2)

    def test_cursor_bundle_wire_format_is_profile_specific_and_ordered(self) -> None:
        server = smt.SenselMorphTuioServer()
        server.set_source_name("test-source")
        tracker = smt.TuioContactTracker()
        update = tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)
        cursor = smt.cursor_from_state(update.active[0])

        bundle = server.build_profile_bundle(smt.TUIO_CURSOR, [cursor], [cursor], 3)
        messages = parsed_bundle(bundle)

        self.assertEqual([address for address, _ in messages], [smt.TUIO_CURSOR] * 4)
        self.assertEqual(messages[0][1], ["source", "test-source"])
        self.assertEqual(messages[1][1], ["alive", 0])
        self.assertEqual(messages[2][1][0], "set")
        self.assertEqual(messages[2][1][1], 0)
        self.assertEqual(messages[3][1], ["fseq", 3])
        self.assertNotIn(smt.TUIO_BLOB, [address for address, _ in messages])
        self.assertNotIn("/tuio/2Dobj", [address for address, _ in messages])

        set_values = messages[2][1]
        self.assertEqual(len(set_values), 7)
        self.assertIsInstance(set_values[1], int)
        for value in set_values[2:]:
            self.assertIsInstance(value, float)

    def test_source_message_is_optional(self) -> None:
        server = smt.SenselMorphTuioServer()
        bundle = server.build_profile_bundle(smt.TUIO_CURSOR, [], [], 0)
        messages = parsed_bundle(bundle)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0][1], ["alive"])
        self.assertEqual(messages[1][1], ["fseq", 0])

    def test_blob_set_wire_order_and_firmware_geometry_mapping(self) -> None:
        tracker = smt.TuioContactTracker()
        update = tracker.update([contact(state=smt.CONTACT_STATE_START, area=194.25)], 1000)
        blob = smt.blob_from_state(update.active[0])
        server = smt.SenselMorphTuioServer()
        bundle = server.build_profile_bundle(smt.TUIO_BLOB, [blob], [blob], 1)
        messages = parsed_bundle(bundle)

        self.assertEqual([address for address, _ in messages], [smt.TUIO_BLOB] * 3)
        set_values = messages[1][1]
        self.assertEqual(set_values[0], "set")
        self.assertEqual(set_values[1], 0)
        self.assertEqual(len(set_values), 13)
        self.assertAlmostEqual(set_values[2], 0.5)
        self.assertAlmostEqual(set_values[3], 0.5)
        self.assertAlmostEqual(set_values[4], math.pi / 2)
        self.assertAlmostEqual(set_values[5], 0.1)
        self.assertAlmostEqual(set_values[6], 0.1)
        self.assertAlmostEqual(set_values[7], 0.01)
        self.assertAlmostEqual(set_values[8], 0.0)
        self.assertAlmostEqual(set_values[9], 0.0)
        self.assertAlmostEqual(set_values[10], 0.0)
        self.assertAlmostEqual(set_values[11], 0.0)
        self.assertAlmostEqual(set_values[12], 0.0)

    def test_sensel_orientation_zero_maps_to_tuio_vertical_major_axis(self) -> None:
        tracker = smt.TuioContactTracker()
        zero = tracker.update([contact(orientation_deg=0.0)], 1000).active[0]
        self.assertAlmostEqual(zero.angle, math.pi / 2)

        tracker = smt.TuioContactTracker()
        minus_ninety = tracker.update([contact(orientation_deg=-90.0)], 1000).active[0]
        self.assertAlmostEqual(minus_ninety.angle, 0.0)

    def test_area_normalizes_against_sensor_elements_and_clamps(self) -> None:
        tracker = smt.TuioContactTracker()

        normal = tracker.update([contact(area=194.25)], 1000).active[0]
        self.assertAlmostEqual(normal.area, 0.01)

        tracker = smt.TuioContactTracker()
        over_range = tracker.update([contact(area=19425.0 * 2.0)], 1000).active[0]
        self.assertEqual(over_range.area, 1.0)

    def test_area_shift_register_is_honored(self) -> None:
        geometry = smt.DeviceGeometry(area_value_scale=2.0)
        tracker = smt.TuioContactTracker(geometry=geometry)

        state = tracker.update([contact(area=388.5)], 1000).active[0]

        self.assertAlmostEqual(state.area, 0.01)

    def test_zero_firmware_axes_are_transmitted_as_zero(self) -> None:
        tracker = smt.TuioContactTracker()
        state = tracker.update([contact(major_axis_mm=0.0, minor_axis_mm=0.0)], 1000).active[0]

        self.assertEqual(state.width, 0.0)
        self.assertEqual(state.height, 0.0)

    def test_linear_motion_matches_tuio_cpp_reference(self) -> None:
        tracker = smt.TuioContactTracker()
        tracker.update([contact(state=smt.CONTACT_STATE_START, x_mm=46.0, y_mm=26.0)], 1000)

        moved = tracker.update([contact(x_mm=69.0, y_mm=39.0)], 1010).active[0]
        self.assertAlmostEqual(moved.x_speed, 10.0)
        self.assertAlmostEqual(moved.y_speed, 10.0)
        self.assertAlmostEqual(moved.motion_speed, math.sqrt(200.0))
        self.assertAlmostEqual(moved.motion_accel, math.sqrt(200.0) / 0.01)

        stationary = tracker.update([contact(x_mm=69.0, y_mm=39.0)], 1020).active[0]
        self.assertAlmostEqual(stationary.x_speed, 0.0)
        self.assertAlmostEqual(stationary.y_speed, 0.0)
        self.assertAlmostEqual(stationary.motion_speed, 0.0)
        self.assertAlmostEqual(stationary.motion_accel, -math.sqrt(200.0) / 0.01)

    def test_zero_dt_updates_position_but_retains_motion_values(self) -> None:
        tracker = smt.TuioContactTracker()
        tracker.update([contact(state=smt.CONTACT_STATE_START, x_mm=46.0, y_mm=26.0)], 1000)

        state = tracker.update([contact(x_mm=69.0, y_mm=39.0)], 1000).active[0]

        self.assertAlmostEqual(state.x, 0.3)
        self.assertAlmostEqual(state.y, 0.3)
        self.assertEqual(state.x_speed, 0.0)
        self.assertEqual(state.y_speed, 0.0)
        self.assertEqual(state.motion_accel, 0.0)

    def test_rotation_uses_wrapped_full_turns_per_second(self) -> None:
        tracker = smt.TuioContactTracker()
        tracker.update([contact(state=smt.CONTACT_STATE_START, orientation_deg=260.0)], 1000)

        state = tracker.update([contact(orientation_deg=280.0)], 1010).active[0]

        expected_turns_per_second = (20.0 / 360.0) / 0.01
        self.assertAlmostEqual(state.rotation_speed, expected_turns_per_second)
        self.assertAlmostEqual(state.rotation_accel, expected_turns_per_second / 0.01)

    def test_inversion_flags_affect_output_not_internal_difference_space(self) -> None:
        tracker = smt.TuioContactTracker(invert_x=True, invert_y=True, invert_angle=True)
        tracker.update([contact(state=smt.CONTACT_STATE_START, x_mm=46.0, y_mm=26.0, orientation_deg=260.0)], 1000)
        state = tracker.update([contact(x_mm=69.0, y_mm=39.0, orientation_deg=280.0)], 1010).active[0]

        session_id, x, y, x_speed, y_speed, _motion_accel = state.cursor_values
        self.assertEqual(session_id, 0)
        self.assertAlmostEqual(x, 0.7)
        self.assertAlmostEqual(y, 0.7)
        self.assertAlmostEqual(x_speed, -10.0)
        self.assertAlmostEqual(y_speed, -10.0)
        self.assertAlmostEqual(state.blob_values[3], (math.tau - math.radians(10.0)) % math.tau)
        self.assertAlmostEqual(state.blob_values[9], -(20.0 / 360.0) / 0.01)

    def test_end_removes_tracked_contact_immediately(self) -> None:
        tracker = smt.TuioContactTracker()
        tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)
        ended = tracker.update([contact(state=smt.CONTACT_STATE_END)], 1010)

        self.assertEqual(len(ended.active), 0)
        self.assertEqual(len(ended.changed_cursors), 0)
        self.assertEqual(len(ended.changed_blobs), 0)
        self.assertTrue(ended.cursor_lifecycle_changed)
        self.assertTrue(ended.blob_lifecycle_changed)

        following = tracker.update([], 1020)
        self.assertEqual(len(following.active), 0)
        self.assertFalse(following.cursor_lifecycle_changed)
        self.assertFalse(following.blob_lifecycle_changed)

    def test_unknown_or_repeated_end_never_creates_one_frame_ghost(self) -> None:
        tracker = smt.TuioContactTracker()

        first = tracker.update([contact(contact_id=4, state=smt.CONTACT_STATE_END)], 1000)
        second = tracker.update([contact(contact_id=4, state=smt.CONTACT_STATE_END)], 1010)

        for update in (first, second):
            self.assertEqual(len(update.active), 0)
            self.assertEqual(len(update.changed_cursors), 0)
            self.assertEqual(len(update.changed_blobs), 0)
            self.assertFalse(update.cursor_lifecycle_changed)
            self.assertFalse(update.blob_lifecycle_changed)
        self.assertEqual(tracker.next_session_id, 0)

    def test_disappearing_contact_causes_immediate_lifecycle_update(self) -> None:
        tracker = smt.TuioContactTracker()
        tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)

        update = tracker.update([], 1010)

        self.assertEqual(len(update.active), 0)
        self.assertTrue(update.cursor_lifecycle_changed or update.blob_lifecycle_changed)

    def test_reused_morph_contact_id_gets_new_tuio_session_id(self) -> None:
        tracker = smt.TuioContactTracker()
        first = tracker.update([contact(contact_id=4, state=smt.CONTACT_STATE_START)], 1000).active[0]
        tracker.update([contact(contact_id=4, state=smt.CONTACT_STATE_END)], 1010)
        tracker.update([], 1020)
        second = tracker.update([contact(contact_id=4, state=smt.CONTACT_STATE_START)], 1030).active[0]

        self.assertEqual(first.session_id, 0)
        self.assertEqual(second.session_id, 1)

    def test_new_contact_activates_immediately_by_default(self) -> None:
        tracker = smt.TuioContactTracker()

        update = tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)

        self.assertEqual(len(update.active), 1)
        self.assertEqual(update.active[0].session_id, 0)

    def test_min_contact_force_blocks_low_force_activation(self) -> None:
        tracker = smt.TuioContactTracker(min_contact_force=20.0)

        low = tracker.update([contact(state=smt.CONTACT_STATE_START, force=19.9)], 1000)
        self.assertEqual(len(low.active), 0)
        self.assertEqual(tracker.next_session_id, 0)

        high = tracker.update([contact(force=20.0)], 1010)
        self.assertEqual(len(high.active), 1)
        self.assertEqual(high.active[0].session_id, 0)

    def test_min_contact_force_applies_only_during_activation(self) -> None:
        tracker = smt.TuioContactTracker(min_contact_force=20.0)
        tracker.update([contact(state=smt.CONTACT_STATE_START, force=20.0)], 1000)

        dipped = tracker.update([contact(force=1.0)], 1010)

        self.assertEqual(len(dipped.active), 1)
        self.assertEqual(dipped.active[0].session_id, 0)

    def test_low_force_contact_activates_once_threshold_is_reached(self) -> None:
        tracker = smt.TuioContactTracker(min_contact_force=20.0)

        tracker.update([contact(state=smt.CONTACT_STATE_START, force=1.0)], 1000)
        reached = tracker.update([contact(force=20.0)], 1010)

        self.assertEqual(len(reached.active), 1)
        self.assertEqual(reached.active[0].session_id, 0)

    def test_min_contact_force_honors_force_unit_shift(self) -> None:
        geometry = smt.DeviceGeometry(force_value_scale=16.0)
        tracker = smt.TuioContactTracker(geometry=geometry, min_contact_force=10.0)

        actual_ten_grams = tracker.update([contact(force=20.0)], 1000)

        self.assertEqual(len(actual_ten_grams.active), 1)

    def test_min_contact_force_must_be_nonnegative(self) -> None:
        with self.assertRaises(ValueError):
            smt.TuioContactTracker(min_contact_force=-0.1)
        with self.assertRaises(SystemExit):
            smt.main(["--min-contact-force", "-0.1"])

    def test_finger_size_blocks_oversized_contact_from_cursor_output(self) -> None:
        tracker = smt.TuioContactTracker()

        update = tracker.update([contact(state=smt.CONTACT_STATE_START, major_axis_mm=40.0, minor_axis_mm=30.0)], 1000)

        self.assertEqual(len(update.active), 1)
        self.assertIsNone(update.active[0].cursor_session_id)
        self.assertEqual(len(update.changed_cursors), 0)
        self.assertEqual(len(update.changed_blobs), 1)
        self.assertFalse(update.cursor_lifecycle_changed)
        self.assertTrue(update.blob_lifecycle_changed)

    def test_long_narrow_finger_ellipse_remains_cursor(self) -> None:
        tracker = smt.TuioContactTracker()

        update = tracker.update(
            [contact(state=smt.CONTACT_STATE_START, force=1000.0, area=1700.0, major_axis_mm=60.0, minor_axis_mm=13.0)],
            1000,
        )

        self.assertEqual(len(update.active), 1)
        self.assertEqual(update.active[0].cursor_session_id, update.active[0].session_id)
        self.assertEqual(len(update.changed_cursors), 1)

    def test_contact_shape_features_include_force_density(self) -> None:
        geometry = smt.DeviceGeometry()
        features = smt.contact_shape_features(contact(force=200.0, area=1200.0), geometry)

        expected_area = 1200.0 * (230.0 / 185.0) * (130.0 / 105.0)
        self.assertAlmostEqual(features.width_mm or 0.0, 13.0)
        self.assertAlmostEqual(features.length_mm or 0.0, 23.0)
        self.assertAlmostEqual(features.force_density_g_mm2 or 0.0, 200.0 / expected_area)

    def test_elongated_contact_requires_plausible_force_density(self) -> None:
        low_density = smt.TuioContactTracker().update(
            [contact(state=smt.CONTACT_STATE_START, force=100.0, area=1700.0, major_axis_mm=60.0, minor_axis_mm=13.0)],
            1000,
        )
        high_density = smt.TuioContactTracker().update(
            [contact(state=smt.CONTACT_STATE_START, force=1000.0, area=1700.0, major_axis_mm=60.0, minor_axis_mm=13.0)],
            1000,
        )

        self.assertIsNone(low_density.active[0].cursor_session_id)
        self.assertEqual(high_density.active[0].cursor_session_id, high_density.active[0].session_id)

    def test_close_contact_cluster_is_blob_only(self) -> None:
        tracker = smt.TuioContactTracker()
        clustered = [
            contact(contact_id=0, state=smt.CONTACT_STATE_START, x_mm=100.0, y_mm=60.0),
            contact(contact_id=1, state=smt.CONTACT_STATE_START, x_mm=106.0, y_mm=62.0),
            contact(contact_id=2, state=smt.CONTACT_STATE_START, x_mm=101.0, y_mm=67.0),
        ]

        update = tracker.update(clustered, 1000)

        self.assertEqual(len(update.active), 3)
        self.assertTrue(all(state.cursor_session_id is None for state in update.active))
        self.assertEqual(len(update.changed_blobs), 3)

    def test_spaced_multi_finger_contacts_remain_cursors(self) -> None:
        tracker = smt.TuioContactTracker()
        spaced = [
            contact(contact_id=0, state=smt.CONTACT_STATE_START, x_mm=80.0, y_mm=60.0),
            contact(contact_id=1, state=smt.CONTACT_STATE_START, x_mm=105.0, y_mm=60.0),
            contact(contact_id=2, state=smt.CONTACT_STATE_START, x_mm=130.0, y_mm=60.0),
        ]

        update = tracker.update(spaced, 1000)

        self.assertEqual(len(update.active), 3)
        self.assertEqual(len(update.changed_cursors), 3)
        self.assertTrue(all(state.cursor_session_id is not None for state in update.active))

    def test_missing_ellipse_data_keeps_permissive_cursor_fallback(self) -> None:
        tracker = smt.TuioContactTracker()

        update = tracker.update(
            [contact(state=smt.CONTACT_STATE_START, orientation_deg=None, major_axis_mm=None, minor_axis_mm=None)],
            1000,
        )

        self.assertEqual(update.active[0].cursor_session_id, update.active[0].session_id)

    def test_cursor_is_removed_and_readded_when_contact_crosses_finger_size(self) -> None:
        tracker = smt.TuioContactTracker()
        initial = tracker.update([contact(state=smt.CONTACT_STATE_START, minor_axis_mm=13.0)], 1000)
        self.assertEqual(initial.active[0].session_id, 0)
        self.assertEqual(initial.active[0].cursor_session_id, 0)

        oversized = tracker.update([contact(major_axis_mm=40.0, minor_axis_mm=30.0)], 1010)
        self.assertEqual(len(oversized.active), 1)
        self.assertIsNone(oversized.active[0].cursor_session_id)
        self.assertEqual(len(oversized.changed_cursors), 0)
        self.assertEqual(len(oversized.changed_blobs), 1)
        self.assertTrue(oversized.cursor_lifecycle_changed)

        small_again = tracker.update([contact(minor_axis_mm=13.0)], 1020)
        self.assertEqual(small_again.active[0].session_id, 0)
        self.assertEqual(small_again.active[0].cursor_session_id, 1)
        self.assertEqual(len(small_again.changed_cursors), 1)
        self.assertTrue(small_again.cursor_lifecycle_changed)

    def test_send_update_omits_oversized_contact_from_cursor_but_keeps_blob(self) -> None:
        args = smt.build_arg_parser().parse_args(["--blob"])
        sender = smt.SenselMorphTuio(args)
        sent: list[tuple[str, object, object, int]] = []
        sender.server.send_profile_frame = lambda address, active, selected, fseq: sent.append((address, active, selected, fseq))  # type: ignore[method-assign]
        tracker = smt.TuioContactTracker()
        update = tracker.update([contact(state=smt.CONTACT_STATE_START, major_axis_mm=40.0, minor_axis_mm=30.0)], 1000)

        self.assertEqual(sender.send_update(update), 2)

        self.assertEqual([item[0] for item in sent], [smt.TUIO_CURSOR, smt.TUIO_BLOB])
        self.assertEqual(len(sent[0][1]), 0)
        self.assertEqual(len(sent[1][1]), 1)

    def test_finger_size_must_be_nonnegative(self) -> None:
        with self.assertRaises(ValueError):
            smt.TuioContactTracker(finger_size_mm=-0.1)
        with self.assertRaises(SystemExit):
            smt.main(["--finger-size", "-0.1"])

    def test_fseq_wraps_within_nonnegative_int32_range(self) -> None:
        server = smt.SenselMorphTuioServer()
        server._fseq = (2**31) - 2

        self.assertEqual(server.next_fseq(), (2**31) - 2)
        self.assertEqual(server.next_fseq(), (2**31) - 1)
        self.assertEqual(server.next_fseq(), 0)

    def test_fseq_starts_at_zero(self) -> None:
        server = smt.SenselMorphTuioServer()

        self.assertEqual(server.next_fseq(), 0)

    def test_empty_bundles_include_blob_only_when_enabled(self) -> None:
        server = smt.SenselMorphTuioServer()
        sent: list[object] = []
        server.send = sent.append  # type: ignore[method-assign]

        server.send_empty_bundles(include_blob=True)

        self.assertEqual(len(sent), 2)
        self.assertEqual(parsed_bundle(sent[0])[-1][1], ["fseq", -1])
        self.assertEqual(parsed_bundle(sent[1])[-1][1], ["fseq", -1])

    def test_changed_mode_suppresses_stationary_updates_but_full_mode_sends(self) -> None:
        parser = smt.build_arg_parser()

        full_args = parser.parse_args(["--update-mode", "full"])
        full_sender = smt.SenselMorphTuio(full_args)
        full_sent: list[tuple[str, object, object, int]] = []
        full_sender.server.send_profile_frame = lambda address, active, selected, fseq: full_sent.append((address, active, selected, fseq))  # type: ignore[method-assign]
        tracker = smt.TuioContactTracker()
        update = tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)
        self.assertEqual(full_sender.send_update(update), 1)
        update = tracker.update([contact()], 1010)
        self.assertEqual(full_sender.send_update(update), 1)
        self.assertEqual(len(full_sent), 2)

        changed_args = parser.parse_args(["--update-mode", "changed", "--refresh-interval", "1000"])
        changed_sender = smt.SenselMorphTuio(changed_args)
        changed_sender.last_cursor_refresh = 1000.0
        changed_sent: list[tuple[str, object, object, int]] = []
        changed_sender.server.send_profile_frame = lambda address, active, selected, fseq: changed_sent.append((address, active, selected, fseq))  # type: ignore[method-assign]
        tracker = smt.TuioContactTracker()
        update = tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)
        self.assertEqual(changed_sender.send_update(update), 1)
        update = tracker.update([contact()], 1010)
        self.assertEqual(changed_sender.send_update(update), 0)
        self.assertEqual(len(changed_sent), 1)

    def test_changed_mode_periodic_refresh_sends_full_active_state(self) -> None:
        args = smt.build_arg_parser().parse_args(["--update-mode", "changed", "--refresh-interval", "0.001"])
        sender = smt.SenselMorphTuio(args)
        sent: list[tuple[str, object, object, int]] = []
        sender.server.send_profile_frame = lambda address, active, selected, fseq: sent.append((address, active, selected, fseq))  # type: ignore[method-assign]
        tracker = smt.TuioContactTracker()
        update = tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)

        self.assertEqual(sender.send_update(update), 1)

        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0][1]), 1)
        self.assertEqual(len(sent[0][2]), 1)

    def test_cursor_and_blob_share_session_id_and_fseq_when_enabled(self) -> None:
        args = smt.build_arg_parser().parse_args(["--blob"])
        sender = smt.SenselMorphTuio(args)
        sent: list[tuple[str, object, object, int]] = []
        sender.server.send_profile_frame = lambda address, active, selected, fseq: sent.append((address, active, selected, fseq))  # type: ignore[method-assign]
        tracker = smt.TuioContactTracker()
        update = tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)

        self.assertEqual(sender.send_update(update), 2)

        self.assertEqual([item[0] for item in sent], [smt.TUIO_CURSOR, smt.TUIO_BLOB])
        self.assertEqual(sent[0][3], sent[1][3])
        self.assertEqual(sent[0][1][0].session_id, sent[1][1][0].session_id)



    def test_source_and_no_source_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            smt.build_arg_parser().parse_args(["--source", "x", "--no-source"])

    def test_contact_mask_selection_is_minimal(self) -> None:
        parser = smt.build_arg_parser()

        self.assertEqual(smt.contact_mask_for_args(parser.parse_args([])), 0x01)
        self.assertEqual(smt.contact_mask_for_args(parser.parse_args(["--finger-size", "0"])), 0x00)
        self.assertEqual(smt.contact_mask_for_args(parser.parse_args(["--blob"])), 0x01)

    def test_positions_clamp_to_normalized_surface(self) -> None:
        tracker = smt.TuioContactTracker()
        state = tracker.update([contact(x_mm=-1.0, y_mm=131.0)], 1000).active[0]

        self.assertEqual(state.cursor_values[1], 0.0)
        self.assertEqual(state.cursor_values[2], 1.0)

    def test_cursor_and_blob_set_type_tags_match_tuio_reference(self) -> None:
        tracker = smt.TuioContactTracker()
        update = tracker.update([contact(state=smt.CONTACT_STATE_START)], 1000)
        cursor = smt.cursor_from_state(update.active[0])
        blob = smt.blob_from_state(update.active[0])
        server = smt.SenselMorphTuioServer()

        cursor_bundle = OscBundle(server.build_profile_bundle(smt.TUIO_CURSOR, [cursor], [cursor], 0).dgram)
        blob_bundle = OscBundle(server.build_profile_bundle(smt.TUIO_BLOB, [blob], [blob], 0).dgram)
        cursor_set = list(cursor_bundle)[1]
        blob_set = list(blob_bundle)[1]

        self.assertEqual(osc_type_tag(cursor_set.dgram), ",sifffff")
        self.assertEqual(osc_type_tag(blob_set.dgram), ",sifffffffffff")

    def test_zero_dt_retains_rotational_motion_values(self) -> None:
        tracker = smt.TuioContactTracker()
        tracker.update([contact(state=smt.CONTACT_STATE_START, orientation_deg=260.0)], 1000)
        tracker.update([contact(orientation_deg=280.0)], 1010)
        before = tracker.states[0].rotation_speed

        state = tracker.update([contact(orientation_deg=300.0)], 1010).active[0]

        self.assertAlmostEqual(state.angle, math.radians(30.0))
        self.assertAlmostEqual(state.rotation_speed, before)

    def test_decode_contact_frame_core_and_ellipse_fields(self) -> None:
        core = struct.pack(
            "<BBHHHH",
            2,
            smt.CONTACT_STATE_MOVE,
            int(115.0 * 256),
            int(65.0 * 256),
            int(100.0 * 8),
            200,
        )
        ellipse = struct.pack("<hHH", -90 * 16, int(23.0 * 256), int(13.0 * 256))
        payload = bytes([0x04, 0, 0, 0, 0, 0, 0x01, 0x01]) + core + ellipse
        raw = {"payload_hex": payload.hex()}

        decoded = smt.decode_contact_frame(raw)

        self.assertEqual(decoded.contact_mask, 0x01)
        self.assertEqual(len(decoded.contacts), 1)
        decoded_contact = decoded.contacts[0]
        self.assertEqual(decoded_contact.id, 2)
        self.assertEqual(decoded_contact.state, smt.CONTACT_STATE_MOVE)
        self.assertAlmostEqual(decoded_contact.x_mm, 115.0)
        self.assertAlmostEqual(decoded_contact.y_mm, 65.0)
        self.assertAlmostEqual(decoded_contact.force, 100.0)
        self.assertEqual(decoded_contact.area, 200.0)
        self.assertEqual(decoded_contact.orientation_deg, -90.0)
        self.assertAlmostEqual(decoded_contact.major_axis_mm, 23.0)
        self.assertAlmostEqual(decoded_contact.minor_axis_mm, 13.0)

    def test_decode_contact_frame_rejects_non_contact_content(self) -> None:
        payload = bytes([0x01, 0, 0, 0, 0, 0])
        with self.assertRaises(dp.DecodeError):
            smt.decode_contact_frame({"payload_hex": payload.hex()})

    def test_read_device_geometry_uses_firmware_units_and_area_shift(self) -> None:
        responses = {
            smt.REG_SENSOR_NUM_COLS: (185).to_bytes(2, "little"),
            smt.REG_SENSOR_NUM_ROWS: (105).to_bytes(2, "little"),
            smt.REG_SENSOR_ACTIVE_AREA_WIDTH_UM: (230000).to_bytes(4, "little"),
            smt.REG_SENSOR_ACTIVE_AREA_HEIGHT_UM: (130000).to_bytes(4, "little"),
            smt.REG_UNIT_SHIFT_FORCE: b"\x03",
            smt.REG_UNIT_SHIFT_AREA: b"\x01",
        }

        def fake_read_reg(_fd: int, reg: int, size: int) -> bytes:
            value = responses[reg]
            self.assertEqual(len(value), size)
            return value

        with mock.patch.object(smt.mc, "read_reg", side_effect=fake_read_reg):
            geometry = smt.read_device_geometry(0)

        self.assertEqual(geometry.sensor_cols, 185)
        self.assertEqual(geometry.sensor_rows, 105)
        self.assertEqual(geometry.active_width_mm, 230.0)
        self.assertEqual(geometry.active_height_mm, 130.0)
        self.assertEqual(geometry.area_value_scale, 2.0)
        self.assertEqual(geometry.force_value_scale, 8.0)

    def test_read_device_geometry_falls_back_when_unit_registers_are_unavailable(self) -> None:
        responses = {
            smt.REG_SENSOR_NUM_COLS: (185).to_bytes(2, "little"),
            smt.REG_SENSOR_NUM_ROWS: (105).to_bytes(2, "little"),
            smt.REG_SENSOR_ACTIVE_AREA_WIDTH_UM: (230000).to_bytes(4, "little"),
            smt.REG_SENSOR_ACTIVE_AREA_HEIGHT_UM: (130000).to_bytes(4, "little"),
        }

        def fake_read_reg(_fd: int, reg: int, size: int) -> bytes:
            if reg not in responses:
                raise RuntimeError("unsupported")
            return responses[reg]

        with mock.patch.object(smt.mc, "read_reg", side_effect=fake_read_reg):
            geometry = smt.read_device_geometry(0)

        self.assertEqual(geometry.area_value_scale, 1.0)


if __name__ == "__main__":
    unittest.main()
