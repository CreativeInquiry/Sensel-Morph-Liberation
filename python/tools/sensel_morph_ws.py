#!/usr/bin/env python3
"""Broadcast live Sensel Morph data over WebSocket.

The browser receives binary raster messages:

    32-byte little-endian header + uint8 payload

Header format:

    magic        4s   b"SMPR" pressure, b"SMLB" labels
    version      u8   1
    kind         u8   1 = pressure, 2 = labels
    header_size  u16  32
    frame_id     u32
    timestamp    u32  Morph frame timestamp
    width        u16
    height       u16
    bit_depth    u8   8
    flags        u8   bit0 calibrated, bit1 normalized, bit2 RLE
    reserved     u16  0
    payload_len  u32
    max_value    f32  maximum pre-clamped pressure value

The same socket also sends JSON text status, accelerometer, and optional
contact messages. Browser code can distinguish binary raster frames from JSON
using ``typeof event.data`` or ``event.data instanceof ArrayBuffer``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from collections.abc import Iterable, Sequence
from typing import TextIO

import decode_pressure as dp
import morph_capture as mc
import sensel_morph_osc as smo


DEFAULT_WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 1561
STATUS_INTERVAL_SEC = 1.0
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_READ_LIMIT = 8192
WS_PRESSURE_MAGIC = b"SMPR"
WS_LABELS_MAGIC = b"SMLB"
WS_VERSION = 1
WS_KIND_PRESSURE = 1
WS_KIND_LABELS = 2
WS_FLAG_CALIBRATED = 0x01
WS_FLAG_NORMALIZED = 0x02
WS_FLAG_RLE = 0x04
WS_RASTER_HEADER = struct.Struct("<4sBBHIIHHBBHIf")
WS_PRESSURE_HEADER = WS_RASTER_HEADER
CONTACT_MESSAGE_ADDRESS = "/sensel_morph/contact"
CONTACTS_MESSAGE_ADDRESS = "/sensel_morph/contacts"
CONTACT_SUMMARY_ADDRESS = "/sensel_morph/contact_summary"
ACCELEROMETER_ADDRESS = "/sensel_morph/accelerometer"


@dataclass(frozen=True)
class RasterBoundingBox:
    """Bounding box in Morph active-area millimeters."""

    min_x_mm: float
    min_y_mm: float
    max_x_mm: float
    max_y_mm: float


class WebSocketBroadcaster:
    """Tiny dependency-free WebSocket broadcaster for browser clients."""

    def __init__(self, host: str, port: int, *, stderr: TextIO = sys.stderr) -> None:
        self.host = host
        self.port = port
        self.stderr = stderr
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen()
        server.settimeout(0.25)
        self._server = server
        self._running.set()
        self._thread = threading.Thread(target=self._accept_loop, name="sensel-morph-ws-server", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._running.clear()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None

        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            close_socket(client)

        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None

    def broadcast_text(self, text: str) -> None:
        self._broadcast_frame(websocket_frame(text.encode("utf-8"), opcode=0x1))

    def broadcast_binary(self, payload: bytes) -> None:
        self._broadcast_frame(websocket_frame(payload, opcode=0x2))

    def _broadcast_frame(self, frame: bytes) -> None:
        with self._clients_lock:
            clients = list(self._clients)

        dead: list[socket.socket] = []
        for client in clients:
            try:
                client.sendall(frame)
            except OSError:
                dead.append(client)

        if dead:
            with self._clients_lock:
                self._clients = [client for client in self._clients if client not in dead]
            for client in dead:
                close_socket(client)

    def _accept_loop(self) -> None:
        while self._running.is_set():
            server = self._server
            if server is None:
                return
            try:
                client, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return

            try:
                client.settimeout(2.0)
                request = read_http_request(client)
                client.sendall(websocket_handshake_response(request))
                client.settimeout(None)
            except Exception as exc:
                print(f"sensel_morph_ws handshake error: {exc}", file=self.stderr)
                close_socket(client)
                continue

            with self._clients_lock:
                self._clients.append(client)


class SenselMorphWs:
    """Live Morph-to-WebSocket transmitter for browser-based receivers."""

    def __init__(self, args: argparse.Namespace, *, stderr: TextIO = sys.stderr) -> None:
        self.args = args
        self.stderr = stderr
        self.running = True
        self.broadcaster = WebSocketBroadcaster(args.host, args.port, stderr=stderr)
        self.device_serial = ""
        self.calibration: smo.PressureCalibration | None = None
        self.last_status_time = 0.0
        self.last_send_time = 0.0

    def stop(self, *_args: object) -> None:
        self.running = False

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        self.broadcaster.start()
        if not self.args.quiet:
            print(f"sensel_morph_ws WebSocket: ws://{self.args.host}:{self.args.port}", file=self.stderr)

        device = self.args.device or mc.find_device()
        usb_info = mc.usb_info_for_device(device)
        self.device_serial = str(usb_info.get("serial_number") or "")
        if self.args.calibration:
            self.calibration = smo.load_pressure_calibration(self.args.calibration, detected_serial=self.device_serial)

        fd = mc.open_serial(device)
        initial: dict[str, str] = {}
        frame_count = 0
        start = time.monotonic()
        try:
            initial = {
                "scan_detail": mc.read_reg(fd, mc.REG_SCAN_DETAIL, 1).hex(),
                "frame_content": mc.read_reg(fd, mc.REG_FRAME_CONTENT, 1).hex(),
                "scan_enabled": mc.read_reg(fd, mc.REG_SCAN_ENABLED, 1).hex(),
                "contacts_mask": mc.read_reg(fd, smo.REG_CONTACTS_MASK, 1).hex(),
            }
            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0)
            mc.write_reg(fd, mc.REG_SCAN_DETAIL, smo.scan_detail_value_for_pressure_res(scan_detail_resolution(self.args)))
            mc.write_reg(fd, mc.REG_FRAME_CONTENT, frame_content_mask(self.args))
            if self.args.contacts:
                mc.write_reg(fd, smo.REG_CONTACTS_MASK, smo.CONTACT_MASK_ALL)
            compression_metadata = mc.read_vs(fd, mc.REG_COMPRESSION_METADATA)
            grids = dp.grids_for_record({"compression_metadata": compression_metadata})

            self.send_status(device, announce=True)
            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 1)

            while self.running:
                if self.args.max_frames is not None and frame_count >= self.args.max_frames:
                    break
                if self.args.fps_limit:
                    wait = (1.0 / self.args.fps_limit) - (time.monotonic() - self.last_send_time)
                    if wait > 0:
                        time.sleep(wait)

                try:
                    raw_frame = mc.read_frame(fd, timeout=self.args.read_timeout)
                    if not raw_frame.get("checksum_ok"):
                        continue
                    live = smo.decode_live_frame(raw_frame, grids)
                    packets = self.raster_packets(live)
                    messages = self.json_messages(live)
                    if not packets and not messages:
                        continue
                except Exception as exc:
                    if not self.args.quiet:
                        print(f"frame error: {exc}", file=self.stderr)
                    time.sleep(0.005)
                    continue

                for packet in packets:
                    self.broadcaster.broadcast_binary(packet)
                for message in messages:
                    self.broadcaster.broadcast_text(message)
                self.last_send_time = time.monotonic()
                self.send_periodic_status(device)
                frame_count += 1
        finally:
            self.restore_device(fd, initial)
            self.broadcaster.close()

        elapsed = max(0.001, time.monotonic() - start)
        if not self.args.quiet:
            print(f"sent {frame_count} frames in {elapsed:.2f}s ({frame_count / elapsed:.1f} fps)", file=self.stderr)
        return 0

    def raster_packets(self, frame: smo.LiveFrame) -> list[bytes]:
        """Build binary raster packets for whichever raster streams are enabled."""
        packets: list[bytes] = []
        if self.args.pressure and frame.pressure is not None:
            packets.append(self.pressure_packet(frame))
        if self.args.labels and frame.labels is not None:
            packets.append(self.labels_packet(frame))
        return packets

    def json_messages(self, frame: smo.LiveFrame) -> list[str]:
        """Build JSON messages for accelerometer and optional contact data."""
        messages: list[str] = []
        accel = self.accelerometer_message(frame)
        if accel is not None:
            messages.append(accel)
        messages.extend(self.contact_messages(frame))
        return messages

    def pressure_packet(self, frame: smo.LiveFrame) -> bytes:
        """Pack one uint8 pressure raster with the shared 32-byte WS header."""
        assert frame.pressure is not None
        values, width, height = smo.pressure_values_for_output(frame.pressure, self.args.pressure_res, self.calibration)
        blob, max_value = smo.pack_pressure(
            values,
            "uint8",
            self.args.force_scale,
            normalize=self.args.pressure_normalize,
        )
        flags = 0
        if self.calibration is not None:
            flags |= WS_FLAG_CALIBRATED
        if self.args.pressure_normalize:
            flags |= WS_FLAG_NORMALIZED
        if self.args.rle:
            blob = smo.rle_encode(blob)
            flags |= WS_FLAG_RLE
        header = WS_PRESSURE_HEADER.pack(
            WS_PRESSURE_MAGIC,
            WS_VERSION,
            WS_KIND_PRESSURE,
            WS_PRESSURE_HEADER.size,
            int(frame.frame_id) & 0xFFFFFFFF,
            int(frame.timestamp) & 0xFFFFFFFF,
            int(width),
            int(height),
            8,
            flags,
            0,
            len(blob),
            float(max_value),
        )
        return header + blob

    def labels_packet(self, frame: smo.LiveFrame) -> bytes:
        """Pack one uint8 label raster with the shared 32-byte WS header."""
        assert frame.labels is not None
        blob, width, height = smo.label_values_for_output(frame.labels, self.args.label_res or self.args.pressure_res)
        flags = 0
        if self.args.rle:
            blob = smo.rle_encode(blob)
            flags |= WS_FLAG_RLE
        header = WS_RASTER_HEADER.pack(
            WS_LABELS_MAGIC,
            WS_VERSION,
            WS_KIND_LABELS,
            WS_RASTER_HEADER.size,
            int(frame.frame_id) & 0xFFFFFFFF,
            int(frame.timestamp) & 0xFFFFFFFF,
            int(width),
            int(height),
            8,
            flags,
            0,
            len(blob),
            0.0,
        )
        return header + blob

    def accelerometer_message(self, frame: smo.LiveFrame) -> str | None:
        """Return accelerometer JSON with raw counts and g-scaled values."""
        if frame.accel is None:
            return None
        x, y, z = frame.accel
        scale = self.args.accel_counts_per_g
        return json_message(
            ACCELEROMETER_ADDRESS,
            {
                "frame_id": frame.frame_id,
                "x": x,
                "y": y,
                "z": z,
                "x_g": x / scale,
                "y_g": y / scale,
                "z_g": z / scale,
            },
        )

    def send_status(self, device: str, *, announce: bool) -> None:
        """Broadcast a status snapshot so late browser clients can initialize."""
        message = {
            "type": "status",
            "device": device,
            "serial_number": self.device_serial or "unknown",
            "pressure": bool(self.args.pressure),
            "labels": bool(self.args.labels),
            "contacts": bool(self.args.contacts),
            "accelerometer": True,
            "pressure_res": self.args.pressure_res,
            "label_res": self.args.label_res or self.args.pressure_res,
            "pressure_type": "uint8",
            "calibrated": self.calibration is not None,
            "pressure_normalize": bool(self.args.pressure_normalize),
            "rle": bool(self.args.rle),
            "force_scale": self.args.force_scale,
            "clients": self.broadcaster.client_count,
        }
        if announce and not self.args.quiet:
            calibration = "none" if self.calibration is None else str(self.calibration.path)
            streams = ",".join(
                name
                for name, enabled in (
                    ("pressure", self.args.pressure),
                    ("labels", self.args.labels),
                    ("contacts", self.args.contacts),
                    ("accelerometer", True),
                )
                if enabled
            )
            print(
                f"sensel_morph_ws device={device} host={self.args.host} port={self.args.port} "
                f"streams={streams} pressure={self.args.pressure_res}/uint8 rle={'on' if self.args.rle else 'off'} "
                f"labels={self.args.label_res or self.args.pressure_res} serial={self.device_serial or 'unknown'} "
                f"calibration={calibration}",
                file=self.stderr,
            )
        self.broadcaster.broadcast_text(json.dumps(message, separators=(",", ":")))
        self.last_status_time = time.monotonic()

    def send_periodic_status(self, device: str) -> None:
        if time.monotonic() - self.last_status_time >= STATUS_INTERVAL_SEC:
            self.send_status(device, announce=False)

    def restore_device(self, fd: int, initial: dict[str, str]) -> None:
        try:
            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0)
        except Exception as exc:
            print(f"warning: failed to stop scanning: {exc}", file=self.stderr)
        mc.clear_leds_safely(fd)
        if initial:
            for key, reg in (
                ("scan_detail", mc.REG_SCAN_DETAIL),
                ("frame_content", mc.REG_FRAME_CONTENT),
                ("contacts_mask", smo.REG_CONTACTS_MASK),
            ):
                try:
                    mc.write_reg(fd, reg, int(initial[key], 16))
                except Exception as exc:
                    print(f"warning: failed to restore {key}: {exc}", file=self.stderr)
        os.close(fd)

    def contact_messages(self, frame: smo.LiveFrame) -> list[str]:
        """Return contact JSON, using fresh raster geometry only when available.

        Contacts-only mode intentionally stays lightweight: it receives firmware
        contact geometry and does not force pressure/label rasters. If pressure
        and labels are present too, raster-derived bboxes, peaks, and ellipses
        replace the firmware estimates.
        """
        if not self.args.contacts or frame.contacts is None:
            return []

        pressure_values = None
        pressure_width = 0
        pressure_height = 0
        if frame.pressure is not None:
            pressure_values, pressure_width, pressure_height = smo.pressure_values_for_output(
                frame.pressure,
                self.args.pressure_res,
                self.calibration,
            )
        label_blob = None
        label_width = 0
        label_height = 0
        raster_ellipses: dict[int, smo.RasterEllipse] = {}
        if frame.pressure is not None and frame.labels is not None:
            label_blob, label_width, label_height = smo.label_values_for_output(frame.labels, self.args.label_res or self.args.pressure_res)
            raster_ellipses = smo.raster_ellipses_by_label(frame.pressure, frame.labels)
        raster_bboxes = raster_bboxes_by_label(label_blob, label_width, label_height)
        raster_peaks = smo.raster_peaks_by_label(
            pressure_values,
            pressure_width,
            pressure_height,
            label_blob,
            label_width,
            label_height,
            force_scale=self.args.force_scale,
        )

        contacts = frame.contacts.contacts
        messages = [
            json_message(
                CONTACTS_MESSAGE_ADDRESS,
                {
                    "frame_id": frame.frame_id,
                    "count": len(contacts),
                },
            )
        ]
        stats = smo.contact_stats(contacts, raster_ellipses)
        messages.append(
            json_message(
                CONTACT_SUMMARY_ADDRESS,
                {
                    "frame_id": frame.frame_id,
                    "count": len(contacts),
                    "x_avg": stats["x"] / smo.ACTIVE_W_MM,
                    "y_avg": stats["y"] / smo.ACTIVE_H_MM,
                    "x_force_avg": stats["x_w"] / smo.ACTIVE_W_MM,
                    "y_force_avg": stats["y_w"] / smo.ACTIVE_H_MM,
                    "force_total": stats["total_force"],
                    "force_avg": stats["avg_force"],
                    "area_avg": stats["area"],
                    "spread": stats["avg_dist"],
                    "weighted_spread": stats["avg_wdist"],
                },
            )
        )
        for contact in contacts:
            peak = smo.contact_peak(contact, raster_peaks)
            ellipse = smo.contact_ellipse(contact, raster_ellipses)
            bbox = contact_bbox(contact, raster_bboxes)
            messages.append(
                json_message(
                    CONTACT_MESSAGE_ADDRESS,
                    {
                        "frame_id": frame.frame_id,
                        "id": contact.id,
                        "state": contact.state,
                        "x_mm": ellipse.x_mm,
                        "y_mm": ellipse.y_mm,
                        "x_norm": ellipse.x_mm / smo.ACTIVE_W_MM,
                        "y_norm": ellipse.y_mm / smo.ACTIVE_H_MM,
                        "force": contact.force,
                        "area": int(contact.area),
                        "orientation_deg": ellipse.orientation_deg,
                        "major_axis_mm": ellipse.major_axis_mm,
                        "minor_axis_mm": ellipse.minor_axis_mm,
                        "delta_x_mm": contact.delta_x_mm or 0.0,
                        "delta_y_mm": contact.delta_y_mm or 0.0,
                        "delta_force": contact.delta_force or 0.0,
                        "delta_area": contact.delta_area or 0.0,
                        "min_x_mm": bbox.min_x_mm,
                        "min_y_mm": bbox.min_y_mm,
                        "max_x_mm": bbox.max_x_mm,
                        "max_y_mm": bbox.max_y_mm,
                        "peak_x_mm": peak.x_mm,
                        "peak_y_mm": peak.y_mm,
                        "peak_x_norm": peak.x_mm / smo.ACTIVE_W_MM,
                        "peak_y_norm": peak.y_mm / smo.ACTIVE_H_MM,
                        "peak_force": peak.force,
                    },
                )
            )
        return messages


def raster_bboxes_by_label(label_blob: bytes | None, label_w: int, label_h: int) -> dict[int, RasterBoundingBox]:
    """Compute pixel-edge bboxes for each non-background label ID."""
    if label_blob is None or label_w <= 0 or label_h <= 0:
        return {}
    if len(label_blob) != label_w * label_h:
        return {}

    raw: dict[int, list[int]] = {}
    for index, label in enumerate(label_blob):
        if label == 255:
            continue
        x = index % label_w
        y = index // label_w
        bounds = raw.get(label)
        if bounds is None:
            raw[label] = [x, y, x, y]
        else:
            if x < bounds[0]:
                bounds[0] = x
            if y < bounds[1]:
                bounds[1] = y
            if x > bounds[2]:
                bounds[2] = x
            if y > bounds[3]:
                bounds[3] = y

    out: dict[int, RasterBoundingBox] = {}
    for label, (min_x, min_y, max_x, max_y) in raw.items():
        out[label] = RasterBoundingBox(
            min_x_mm=min_x * smo.ACTIVE_W_MM / label_w,
            min_y_mm=min_y * smo.ACTIVE_H_MM / label_h,
            max_x_mm=(max_x + 1) * smo.ACTIVE_W_MM / label_w,
            max_y_mm=(max_y + 1) * smo.ACTIVE_H_MM / label_h,
        )
    return out


def contact_bbox(contact: dp.DecodedContact, raster_bboxes: dict[int, RasterBoundingBox] | None = None) -> RasterBoundingBox:
    """Prefer fresh label-derived bboxes, falling back to firmware bboxes."""
    if raster_bboxes is not None and contact.id in raster_bboxes:
        return raster_bboxes[contact.id]
    return RasterBoundingBox(
        min_x_mm=contact.min_x_mm or 0.0,
        min_y_mm=contact.min_y_mm or 0.0,
        max_x_mm=contact.max_x_mm or 0.0,
        max_y_mm=contact.max_y_mm or 0.0,
    )


def read_http_request(client: socket.socket) -> bytes:
    """Read a minimal HTTP Upgrade request from a browser WebSocket client."""
    chunks: list[bytes] = []
    total = 0
    while total < WS_READ_LIMIT:
        chunk = client.recv(1024)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        request = b"".join(chunks)
        if b"\r\n\r\n" in request:
            return request
    raise ValueError("incomplete WebSocket upgrade request")


def websocket_handshake_response(request: bytes) -> bytes:
    """Build the RFC 6455 server handshake response."""
    headers = parse_http_headers(request)
    key = headers.get("sec-websocket-key")
    if not key:
        raise ValueError("missing Sec-WebSocket-Key")
    accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode("ascii")


def parse_http_headers(request: bytes) -> dict[str, str]:
    """Parse HTTP headers into lowercase names for the WebSocket handshake."""
    try:
        text = request.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid HTTP request encoding") from exc

    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if not line:
            break
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return headers


def websocket_frame(payload: bytes, *, opcode: int) -> bytes:
    """Wrap a text or binary payload in an unmasked server WebSocket frame."""
    if opcode not in (0x1, 0x2):
        raise ValueError("opcode must be text or binary")
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(length)
    elif length <= 0xFFFF:
        header.extend((126, (length >> 8) & 0xFF, length & 0xFF))
    else:
        header.append(127)
        header.extend(length.to_bytes(8, "big"))
    return bytes(header) + payload


def close_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def frame_content_mask(args: argparse.Namespace) -> int:
    """Return the Morph content bits requested by public WS stream flags."""
    mask = smo.CONTENT_BITS["accelerometer"]
    if args.pressure:
        mask |= smo.CONTENT_BITS["pressure"]
    if args.labels:
        mask |= smo.CONTENT_BITS["labels"]
    if args.contacts:
        mask |= smo.CONTENT_BITS["contacts"]
    return mask


def scan_detail_resolution(args: argparse.Namespace) -> str:
    """Choose scan detail from the active raster stream, if any."""
    if args.pressure:
        return args.pressure_res
    return args.label_res or args.pressure_res


def json_message(address: str, data: dict[str, object]) -> str:
    """Wrap a native /sensel_morph address and fields in compact JSON."""
    return json.dumps(
        {
            "address": address,
            **data,
        },
        separators=(",", ":"),
    )


def normalize_stream_args(args: argparse.Namespace) -> argparse.Namespace:
    """Apply convenience defaults after argparse has parsed explicit flags."""
    if args.pressure is None:
        args.pressure = not (args.labels or args.contacts)
    if args.label_res is None:
        args.label_res = args.pressure_res
    return args


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Broadcast live Sensel Morph raster and contact data over WebSocket for browser sketches.",
    )
    parser.add_argument("--host", default=DEFAULT_WS_HOST, help="WebSocket bind host. Default 127.0.0.1.")
    parser.add_argument("--port", type=int, default=DEFAULT_WS_PORT, help="WebSocket TCP port. Default 1561.")
    parser.add_argument("--device", default=None, help="Serial device path. Defaults to first /dev/cu.usbmodem*.")
    parser.add_argument(
        "--pressure",
        action="store_true",
        default=None,
        help="Send a binary uint8 pressure raster stream. If no streams are selected, pressure is enabled for convenience.",
    )
    parser.add_argument("--labels", action="store_true", help="Send a binary uint8 label-ID raster stream.")
    parser.add_argument("--pressure-res", choices=sorted(smo.PRESSURE_SIZES), default="high")
    parser.add_argument("--label-res", choices=sorted(smo.PRESSURE_SIZES), default=None, help="Label raster resolution. Defaults to pressure-res.")
    parser.add_argument("--contacts", action="store_true", help="Also send contact JSON messages.")
    parser.add_argument(
        "--accelerometer",
        action="store_true",
        help="Accepted for API symmetry; accelerometer JSON is always requested and sent when present.",
    )
    parser.add_argument("--accel-counts-per-g", type=float, default=15600.0, help="Accelerometer counts per g for *_g JSON fields.")
    parser.add_argument(
        "--pressure-normalize",
        action="store_true",
        help="Scale each uint8 frame so its maximum pressure becomes 255. Default is absolute clamp.",
    )
    parser.add_argument(
        "--rle",
        action="store_true",
        help="Send pressure and label raster payloads as byte-RLE [count,value] pairs. All raster packets are compressed when enabled.",
    )
    parser.add_argument("--force-scale", type=float, default=1.0, help="Divide pressure values before uint8 packing.")
    parser.add_argument(
        "--calibration",
        default=None,
        help=(
            "Path to calibration JSON or calibrator output directory. "
            "The calibration serial must match the connected Morph."
        ),
    )
    parser.add_argument("--fps-limit", type=float, default=0.0, help="Limit sent frame rate; 0 means unbounded.")
    parser.add_argument("--max-frames", type=int, default=None, help="Stop after this many decoded frames.")
    parser.add_argument("--read-timeout", type=float, default=1.0)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = normalize_stream_args(parser.parse_args(argv))
    return SenselMorphWs(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
