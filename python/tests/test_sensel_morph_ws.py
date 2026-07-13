from __future__ import annotations

import pathlib
import struct
import sys
import unittest
import json


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import decode_pressure as dp  # noqa: E402
import sensel_morph_osc as smo  # noqa: E402
import sensel_morph_ws as smw  # noqa: E402


class SenselMorphWsTest(unittest.TestCase):
    def test_parser_defaults_to_localhost_1561_high_uint8(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args([]))

        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 1561)
        self.assertEqual(args.pressure_res, "high")
        self.assertFalse(args.pressure_normalize)
        self.assertFalse(args.contacts)
        self.assertTrue(args.pressure)
        self.assertFalse(args.labels)

    def test_contacts_request_contacts_and_accelerometer_only(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--contacts"]))

        self.assertEqual(smw.frame_content_mask(args), 0x0C)
        self.assertFalse(args.pressure)

    def test_labels_request_labels_and_accelerometer(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--labels"]))

        self.assertEqual(smw.frame_content_mask(args), 0x0A)
        self.assertFalse(args.pressure)
        self.assertTrue(args.labels)

    def test_labels_only_scan_detail_follows_label_resolution(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--labels", "--label-res", "low"]))

        self.assertEqual(smw.scan_detail_resolution(args), "low")

    def test_websocket_binary_frame_uses_binary_opcode(self) -> None:
        frame = smw.websocket_frame(b"abc", opcode=0x2)

        self.assertEqual(frame, b"\x82\x03abc")

    def test_pressure_packet_header_and_payload(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--pressure-res", "low"]))
        sender = smw.SenselMorphWs(args)
        live = smo.LiveFrame(
            frame_id=513,
            timestamp=123456,
            content_mask=0x09,
            pressure=dp.DecodedFrame(
                grid=dp.Grid("low", 47, 27, 4, 4),
                base=0,
                values=[7] * (47 * 27),
                bytes_used=0,
                varints=0,
                zero_runs=0,
                nonzero_values=47 * 27,
            ),
        )

        packet = sender.pressure_packet(live)
        header = packet[: smw.WS_PRESSURE_HEADER.size]
        payload = packet[smw.WS_PRESSURE_HEADER.size :]
        unpacked = smw.WS_PRESSURE_HEADER.unpack(header)

        self.assertEqual(unpacked[0], b"SMPR")
        self.assertEqual(unpacked[1], 1)
        self.assertEqual(unpacked[2], smw.WS_KIND_PRESSURE)
        self.assertEqual(unpacked[3], smw.WS_PRESSURE_HEADER.size)
        self.assertEqual(unpacked[4], 513)
        self.assertEqual(unpacked[5], 123456)
        self.assertEqual(unpacked[6:9], (47, 27, 8))
        self.assertEqual(unpacked[9], 0)
        self.assertEqual(unpacked[11], 47 * 27)
        self.assertEqual(unpacked[12], 7.0)
        self.assertEqual(payload, bytes([7]) * (47 * 27))

    def test_pressure_packet_sets_normalize_flag(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--pressure-res", "low", "--pressure-normalize"]))
        sender = smw.SenselMorphWs(args)
        live = smo.LiveFrame(
            frame_id=1,
            timestamp=0,
            content_mask=0x09,
            pressure=dp.DecodedFrame(
                grid=dp.Grid("low", 2, 1, 1, 1),
                base=0,
                values=[0, 10],
                bytes_used=0,
                varints=0,
                zero_runs=0,
                nonzero_values=2,
            ),
        )

        packet = sender.pressure_packet(live)
        flags = packet[21]

        self.assertEqual(flags & 0x02, 0x02)

    def test_pressure_packet_rle_compresses_payload_and_sets_flag(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--pressure-res", "low", "--rle"]))
        sender = smw.SenselMorphWs(args)
        live = smo.LiveFrame(
            frame_id=1,
            timestamp=0,
            content_mask=0x09,
            pressure=dp.DecodedFrame(
                grid=dp.Grid("low", 47, 27, 4, 4),
                base=0,
                values=[7] * (47 * 27),
                bytes_used=0,
                varints=0,
                zero_runs=0,
                nonzero_values=47 * 27,
            ),
        )

        packet = sender.pressure_packet(live)
        header = packet[: smw.WS_RASTER_HEADER.size]
        payload = packet[smw.WS_RASTER_HEADER.size :]
        unpacked = smw.WS_RASTER_HEADER.unpack(header)

        self.assertEqual(unpacked[9] & smw.WS_FLAG_RLE, smw.WS_FLAG_RLE)
        self.assertEqual(unpacked[11], len(payload))
        self.assertEqual(payload, bytes([255, 7, 255, 7, 255, 7, 255, 7, 249, 7]))

    def test_labels_packet_header_and_payload(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--labels", "--label-res", "low"]))
        sender = smw.SenselMorphWs(args)
        live = smo.LiveFrame(
            frame_id=7,
            timestamp=8,
            content_mask=0x0A,
            labels=dp.DecodedLabels(
                grid=dp.Grid("low", 47, 27, 4, 4),
                values=[255] * (47 * 27),
                bytes_used=0,
            ),
        )

        packet = sender.labels_packet(live)
        header = packet[: smw.WS_RASTER_HEADER.size]
        payload = packet[smw.WS_RASTER_HEADER.size :]
        unpacked = smw.WS_RASTER_HEADER.unpack(header)

        self.assertEqual(unpacked[0], b"SMLB")
        self.assertEqual(unpacked[1], 1)
        self.assertEqual(unpacked[2], smw.WS_KIND_LABELS)
        self.assertEqual(unpacked[3], smw.WS_RASTER_HEADER.size)
        self.assertEqual(unpacked[4], 7)
        self.assertEqual(unpacked[5], 8)
        self.assertEqual(unpacked[6:9], (47, 27, 8))
        self.assertEqual(unpacked[11], 47 * 27)
        self.assertEqual(payload, bytes([255]) * (47 * 27))

    def test_labels_packet_rle_compresses_payload_and_sets_flag(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--labels", "--label-res", "low", "--rle"]))
        sender = smw.SenselMorphWs(args)
        live = smo.LiveFrame(
            frame_id=7,
            timestamp=8,
            content_mask=0x0A,
            labels=dp.DecodedLabels(
                grid=dp.Grid("low", 47, 27, 4, 4),
                values=[255] * (47 * 27),
                bytes_used=0,
            ),
        )

        packet = sender.labels_packet(live)
        header = packet[: smw.WS_RASTER_HEADER.size]
        payload = packet[smw.WS_RASTER_HEADER.size :]
        unpacked = smw.WS_RASTER_HEADER.unpack(header)

        self.assertEqual(unpacked[9] & smw.WS_FLAG_RLE, smw.WS_FLAG_RLE)
        self.assertEqual(unpacked[11], len(payload))
        self.assertEqual(payload, bytes([255, 255, 255, 255, 255, 255, 255, 255, 249, 255]))

    def test_accelerometer_message_uses_counts_and_g(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args([]))
        sender = smw.SenselMorphWs(args)
        live = smo.LiveFrame(frame_id=3, timestamp=0, content_mask=0x08, accel=(15600, -7800, 0))

        message = json.loads(sender.accelerometer_message(live) or "{}")

        self.assertEqual(message["address"], "/sensel_morph/accelerometer")
        self.assertEqual(message["x"], 15600)
        self.assertEqual(message["y"], -7800)
        self.assertEqual(message["z"], 0)
        self.assertAlmostEqual(message["x_g"], 1.0)
        self.assertAlmostEqual(message["y_g"], -0.5)

    def test_contact_messages_include_count_summary_and_contact_json(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--contacts", "--pressure-res", "low"]))
        sender = smw.SenselMorphWs(args)
        live = smo.LiveFrame(
            frame_id=9,
            timestamp=0,
            content_mask=0x0F,
            pressure=dp.DecodedFrame(
                grid=dp.Grid("low", 47, 27, 4, 4),
                base=0,
                values=[0] * (47 * 27),
                bytes_used=0,
                varints=0,
                zero_runs=0,
                nonzero_values=0,
            ),
            contacts=dp.DecodedContacts(
                contacts=[
                    dp.DecodedContact(
                        id=3,
                        state=2,
                        x_mm=115,
                        y_mm=65,
                        force=123,
                        area=4,
                        orientation_deg=15,
                        major_axis_mm=20,
                        minor_axis_mm=10,
                        peak_x_mm=120,
                        peak_y_mm=70,
                        peak_force=99,
                    )
                ],
                bytes_used=0,
                contact_mask=0x0F,
            ),
        )

        messages = [json.loads(message) for message in sender.contact_messages(live)]

        self.assertEqual(messages[0]["address"], "/sensel_morph/contacts")
        self.assertEqual(messages[0]["count"], 1)
        self.assertEqual(messages[1]["address"], "/sensel_morph/contact_summary")
        self.assertEqual(messages[2]["address"], "/sensel_morph/contact")
        self.assertEqual(messages[2]["id"], 3)
        self.assertAlmostEqual(messages[2]["x_norm"], 0.5)
        self.assertAlmostEqual(messages[2]["y_norm"], 0.5)
        self.assertAlmostEqual(messages[2]["peak_x_norm"], 120 / 230)
        self.assertAlmostEqual(messages[2]["peak_y_norm"], 70 / 130)

    def test_contact_messages_work_without_pressure_or_labels(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--contacts"]))
        sender = smw.SenselMorphWs(args)
        live = smo.LiveFrame(
            frame_id=11,
            timestamp=0,
            content_mask=0x0C,
            contacts=dp.DecodedContacts(
                contacts=[
                    dp.DecodedContact(
                        id=2,
                        state=2,
                        x_mm=46,
                        y_mm=26,
                        force=77,
                        area=3,
                        orientation_deg=12,
                        major_axis_mm=9,
                        minor_axis_mm=5,
                        min_x_mm=40,
                        min_y_mm=20,
                        max_x_mm=50,
                        max_y_mm=30,
                        peak_x_mm=45,
                        peak_y_mm=25,
                        peak_force=70,
                    )
                ],
                bytes_used=0,
                contact_mask=0x0F,
            ),
        )

        messages = [json.loads(message) for message in sender.contact_messages(live)]

        self.assertEqual(messages[2]["x_mm"], 46)
        self.assertEqual(messages[2]["orientation_deg"], 12)
        self.assertEqual(messages[2]["min_x_mm"], 40)
        self.assertEqual(messages[2]["peak_x_mm"], 45)

    def test_contact_messages_use_label_derived_bboxes(self) -> None:
        args = smw.normalize_stream_args(smw.build_arg_parser().parse_args(["--contacts", "--pressure-res", "low"]))
        sender = smw.SenselMorphWs(args)
        labels = [255] * (47 * 27)
        labels[2 * 47 + 1] = 3
        labels[4 * 47 + 3] = 3
        live = smo.LiveFrame(
            frame_id=10,
            timestamp=0,
            content_mask=0x0F,
            pressure=dp.DecodedFrame(
                grid=dp.Grid("low", 47, 27, 4, 4),
                base=0,
                values=[0] * (47 * 27),
                bytes_used=0,
                varints=0,
                zero_runs=0,
                nonzero_values=0,
            ),
            labels=dp.DecodedLabels(
                grid=dp.Grid("low", 47, 27, 4, 4),
                values=labels,
                bytes_used=0,
            ),
            contacts=dp.DecodedContacts(
                contacts=[
                    dp.DecodedContact(
                        id=3,
                        state=2,
                        x_mm=115,
                        y_mm=65,
                        force=123,
                        area=4,
                        min_x_mm=0,
                        min_y_mm=0,
                        max_x_mm=1,
                        max_y_mm=1,
                    )
                ],
                bytes_used=0,
                contact_mask=0x0F,
            ),
        )

        contact = json.loads(sender.contact_messages(live)[2])

        self.assertAlmostEqual(contact["min_x_mm"], 1 * smo.ACTIVE_W_MM / 47)
        self.assertAlmostEqual(contact["min_y_mm"], 2 * smo.ACTIVE_H_MM / 27)
        self.assertAlmostEqual(contact["max_x_mm"], 4 * smo.ACTIVE_W_MM / 47)
        self.assertAlmostEqual(contact["max_y_mm"], 5 * smo.ACTIVE_H_MM / 27)


if __name__ == "__main__":
    unittest.main()
