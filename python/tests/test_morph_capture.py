from __future__ import annotations

import pathlib
import sys
import json
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import morph_capture as mc  # noqa: E402
import morph_capture_session as mcs  # noqa: E402


class MorphCaptureProtocolTest(unittest.TestCase):
    def test_write_vs_pipelined_writes_header_and_payload_before_ack_drain(self) -> None:
        writes: list[tuple[int, bytes]] = []
        reads: list[tuple[int, int, float]] = []
        original_write = mc.os.write
        original_read_exact = mc.read_exact

        def fake_write(fd: int, data: bytes) -> int:
            writes.append((fd, data))
            return len(data)

        def fake_read_exact(fd: int, n: int, timeout: float = 2.0) -> bytes:
            reads.append((fd, n, timeout))
            return b"\x07\x80\x07"

        try:
            mc.os.write = fake_write
            mc.read_exact = fake_read_exact
            result = mc.write_vs_pipelined(22, 0x80, b"\x01\x02", timeout=0.5)
        finally:
            mc.os.write = original_write
            mc.read_exact = original_read_exact

        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0], 22)
        self.assertEqual(
            writes[0][1],
            bytes.fromhex("0180000402000000020200010203"),
        )
        self.assertEqual(reads, [(22, 3, 0.5)])
        self.assertEqual(result["ack"], "078007")
        self.assertEqual(result["packets"], 1)

    def test_capture_session_jsonl_writer_matches_processing_shape(self) -> None:
        record = {
            "label": "unit",
            "path": "/dev/cu.usbmodemTEST",
            "serial_number": "SERIAL",
            "started_at": "2026-07-12T00:00:00-0400",
            "duration": 1.5,
            "requested": {
                "scan_detail": 0,
                "frame_content": 15,
                "contacts_mask": 15,
            },
            "initial": {
                "scan_detail": "01",
                "frame_content": "08",
            },
            "compression_metadata": {
                "header": "031c000600",
                "size": 6,
                "data_hex": "001122334455",
                "checksum": 255,
            },
            "frames": [
                {
                    "reg": 38,
                    "header": 0,
                    "payload_size": 6,
                    "payload_hex": "0f0104030201",
                    "checksum": 33,
                    "checksum_ok": True,
                    "content_mask": 15,
                    "rolling_counter": 1,
                    "timestamp_le": 16909060,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sensel_recording_test.jsonl"
            mcs.write_jsonl_record(record, path)
            lines = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual(lines[0]["type"], "header")
        self.assertEqual(lines[0]["format"], "sensel_morph_raw_jsonl")
        self.assertEqual(lines[0]["requested"]["pressure_res"], "high")
        self.assertEqual(lines[0]["compression_metadata"]["data_hex"], "001122334455")
        self.assertEqual(lines[1]["type"], "frame")
        self.assertEqual(lines[1]["payload_hex"], "0f0104030201")


if __name__ == "__main__":
    unittest.main()
