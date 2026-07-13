#!/usr/bin/env python3
"""Broadcast live Sensel Morph data over OSC.

This tool reads frames from the Morph over the USB CDC serial register protocol,
decodes them locally, and sends OSC data. By default receivers get plain
rectangular rasters; --rle sends pressure and label raster blobs as simple
byte-RLE on dedicated OSC addresses.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import signal
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from pythonosc.udp_client import SimpleUDPClient

import decode_pressure as dp
import morph_capture as mc
import sensel_morph_led as ledctl


ACTIVE_W_MM = 230.0
ACTIVE_H_MM = 130.0
DEFAULT_OSC_PORT = 1560
DEFAULT_CHUNK_SIZE = 4096
STATUS_INTERVAL_SEC = 1.0
REG_CONTACTS_MASK = 0x4B
CONTACT_MASK_ALL = 0x0F
PRESSURE_SIZES = {
    "high": (185, 105),
    "med": (93, 53),
    "low": (47, 27),
}
SCAN_DETAILS = {
    "high": 0,
    "medium": 1,
}
SCAN_DETAIL_BY_PRESSURE_RES = {
    "high": "high",
    "med": "medium",
    "low": "medium",
}
CONTENT_BITS = {
    "pressure": 0x01,
    "labels": 0x02,
    "contacts": 0x04,
    "accelerometer": 0x08,
}
CONTACT_STATE_NAMES = {
    0: "invalid",
    1: "start",
    2: "move",
    3: "end",
}
LED_TOTAL_FORCE_MODES = ("glow", "kitt", "meter", "twinkle")
LED_SPATIAL_PRESSURE_MODES = ("columns", "pulse")
LED_PRESSURE_MODES = (*LED_TOTAL_FORCE_MODES, *LED_SPATIAL_PRESSURE_MODES)
LED_MODES = (*LED_PRESSURE_MODES, "all")


@dataclass
class LiveFrame:
    frame_id: int
    timestamp: int
    content_mask: int
    pressure: dp.DecodedFrame | None = None
    labels: dp.DecodedLabels | None = None
    contacts: dp.DecodedContacts | None = None
    accel: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class RasterPeak:
    x_mm: float
    y_mm: float
    force: float


@dataclass(frozen=True)
class RasterEllipse:
    x_mm: float
    y_mm: float
    orientation_deg: float
    major_axis_mm: float
    minor_axis_mm: float
    area_cells: int


@dataclass(frozen=True)
class SourcePressureCalibration:
    grid_key: tuple[int, int, int, int]
    dark: np.ndarray
    gain: np.ndarray
    support: np.ndarray


@dataclass(frozen=True)
class PressureCalibration:
    path: pathlib.Path
    serial_number: str
    width: int
    height: int
    dark: np.ndarray
    gain: np.ndarray
    gain_key: str
    coverage: np.ndarray | None = None
    light: np.ndarray | None = None
    target: float = 0.0
    min_gain: float = 0.5
    max_gain: float = 2.0
    source_maps: dict[tuple[int, int, int, int], SourcePressureCalibration] | None = None


class SenselMorphOsc:
    """Live Morph-to-OSC transmitter with optional LED feedback."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.client = SimpleUDPClient(args.host, args.port)
        self.running = True
        self.last_contact_count: int | None = None
        self.last_send_time = 0.0
        self.profile_totals: dict[str, float] = {}
        self.profile_counts: dict[str, int] = {}
        self.device_serial = ""
        self.status_device = ""
        self.status_content_mask = 0
        self.last_status_time = 0.0
        self.calibration: PressureCalibration | None = None
        self.led_info: ledctl.LedInfo | None = None
        self.led_state: dict[str, object] = {}

    def stop(self, *_args: object) -> None:
        self.running = False

    def run(self) -> int:
        requested_content = frame_content_mask(self.args)

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        device = self.args.device or mc.find_device()
        usb_info = mc.usb_info_for_device(device)
        self.device_serial = str(usb_info.get("serial_number") or "")
        if self.args.calibration:
            self.calibration = load_pressure_calibration(
                self.args.calibration,
                detected_serial=self.device_serial,
            )
        fd = mc.open_serial(device)
        initial: dict[str, str] = {}
        try:
            initial = {
                "scan_detail": mc.read_reg(fd, mc.REG_SCAN_DETAIL, 1).hex(),
                "frame_content": mc.read_reg(fd, mc.REG_FRAME_CONTENT, 1).hex(),
                "scan_enabled": mc.read_reg(fd, mc.REG_SCAN_ENABLED, 1).hex(),
                "contacts_mask": mc.read_reg(fd, REG_CONTACTS_MASK, 1).hex(),
            }
            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0)
            mc.write_reg(fd, mc.REG_SCAN_DETAIL, scan_detail_value_for_pressure_res(self.args.pressure_res))
            mc.write_reg(fd, mc.REG_FRAME_CONTENT, requested_content)
            if self.args.contacts:
                mc.write_reg(fd, REG_CONTACTS_MASK, CONTACT_MASK_ALL)
            compression_metadata = mc.read_vs(fd, mc.REG_COMPRESSION_METADATA)
            grids = dp.grids_for_record({"compression_metadata": compression_metadata})
            self.setup_leds(fd)

            self.status_device = device
            self.status_content_mask = requested_content
            self.send_status(device, requested_content)
            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 1)
            return self.loop(fd, grids)
        finally:
            self.restore_device(fd, initial)

    def loop(self, fd: int, grids: list[dp.Grid]) -> int:
        frame_count = 0
        start = time.monotonic()
        while self.running:
            if self.args.max_frames is not None and frame_count >= self.args.max_frames:
                break

            frame_t0 = time.perf_counter()
            if self.args.fps_limit:
                wait = (1.0 / self.args.fps_limit) - (time.monotonic() - self.last_send_time)
                if wait > 0:
                    time.sleep(wait)

            try:
                read_t0 = time.perf_counter()
                raw_frame = mc.read_frame(fd, timeout=self.args.read_timeout)
                read_t1 = time.perf_counter()
                if not raw_frame.get("checksum_ok"):
                    continue
                decode_t0 = time.perf_counter()
                live = decode_live_frame(raw_frame, grids)
                decode_t1 = time.perf_counter()
            except Exception as exc:
                if not self.args.quiet:
                    print(f"frame error: {exc}", file=sys.stderr)
                time.sleep(0.005)
                continue

            self.profile_add("read_frame", read_t1 - read_t0)
            self.profile_add("decode_frame", decode_t1 - decode_t0)
            self.send_live_frame(live)
            self.send_periodic_status()
            self.last_send_time = time.monotonic()
            self.update_leds(fd, live, frame_count)
            self.profile_add("total_success_loop", time.perf_counter() - frame_t0)
            frame_count += 1

        elapsed = max(0.001, time.monotonic() - start)
        if not self.args.quiet:
            print(f"sent {frame_count} frames in {elapsed:.2f}s ({frame_count / elapsed:.1f} fps)", file=sys.stderr)
        if self.args.profile:
            self.print_profile(frame_count)
        return 0

    def restore_device(self, fd: int, initial: dict[str, str]) -> None:
        try:
            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0)
        except Exception as exc:
            print(f"warning: failed to stop scanning: {exc}", file=sys.stderr)
        mc.clear_leds_safely(fd)
        if initial:
            for key, reg in (
                ("scan_detail", mc.REG_SCAN_DETAIL),
                ("frame_content", mc.REG_FRAME_CONTENT),
                ("contacts_mask", REG_CONTACTS_MASK),
            ):
                try:
                    mc.write_reg(fd, reg, int(initial[key], 16))
                except Exception as exc:
                    print(f"warning: failed to restore {key}: {exc}", file=sys.stderr)
        os.close(fd)

    def setup_leds(self, fd: int) -> None:
        self.led_info = ledctl.read_led_info(fd)
        if self.args.led_mode_name is None:
            ledctl.write_led_values(fd, self.led_info, [0] * self.led_info.count)
            return
        if self.args.led_mode_name == "all":
            brightness = ledctl.clamp_int(self.args.led_all_brightness or 0, 0, self.led_info.max_brightness)
            ledctl.write_led_values(fd, self.led_info, [brightness] * self.led_info.count)

    def update_leds(self, fd: int, frame: LiveFrame, frame_count: int) -> None:
        if self.led_info is None:
            return
        if self.args.led_mode_name is None or self.args.led_mode_name == "all":
            return
        if not led_frame_due(self.args, frame_count):
            return
        values = self.led_values_for_frame(frame)
        if values is None:
            return
        led_t0 = time.perf_counter()
        ledctl.write_led_values(fd, self.led_info, values, timeout=self.args.led_write_timeout)
        self.profile_add("led_update", time.perf_counter() - led_t0)

    def led_values_for_frame(self, frame: LiveFrame) -> list[int] | None:
        if self.led_info is None:
            return None
        led_args = led_args_for_osc(self.args)
        if frame.pressure is not None:
            return ledctl.mode_values(led_args, self.led_info, frame.pressure, self.led_state)
        if self.args.led_mode_name in LED_TOTAL_FORCE_MODES and frame.contacts is not None:
            total_force = sum(max(0.0, float(contact.force)) for contact in frame.contacts.contacts)
            return ledctl.mode_values_for_total_force(led_args, self.led_info, total_force, self.led_state)
        return None

    def send_status(self, device: str, content_mask: int, announce: bool = True) -> None:
        if announce and not self.args.quiet:
            streams = ",".join(name for name, bit in CONTENT_BITS.items() if content_mask & bit)
            calibration = "none"
            if self.calibration is not None:
                mode = "kernel-fit" if self.calibration.light is not None else "expanded"
                calibration = f"{self.calibration.path} ({mode})"
            print(
                f"sensel_morph_osc device={device} host={self.args.host} port={self.args.port} "
                f"streams={streams} pressure={self.args.pressure_res}/{self.args.pressure_type} "
                f"rle={'on' if self.args.rle else 'off'} serial={self.device_serial or 'unknown'} "
                f"calibration={calibration}",
                file=sys.stderr,
            )
        self.client.send_message(
            "/sensel_morph/status",
            [
                device,
                int(content_mask),
                self.args.pressure_res,
                self.args.pressure_type,
                int(self.args.rle),
                self.device_serial,
                int(self.calibration is not None),
            ],
        )
        self.last_status_time = time.monotonic()

    def send_periodic_status(self) -> None:
        if not self.status_device:
            return
        if time.monotonic() - self.last_status_time >= STATUS_INTERVAL_SEC:
            self.send_status(self.status_device, self.status_content_mask, announce=False)

    def send_live_frame(self, frame: LiveFrame) -> None:
        send_frame_t0 = time.perf_counter()
        self.client.send_message(
            "/sensel_morph/frame",
            [frame.frame_id, frame.timestamp, frame.content_mask],
        )
        self.profile_add("send_frame_msg", time.perf_counter() - send_frame_t0)

        pressure_values: Sequence[int | float] | None = None
        pressure_width = 0
        pressure_height = 0
        label_blob: bytes | None = None
        label_width = 0
        label_height = 0

        if frame.pressure is not None and self.args.pressure:
            pressure_values_t0 = time.perf_counter()
            values, width, height = pressure_values_for_output(frame.pressure, self.args.pressure_res, self.calibration)
            pressure_values = values
            pressure_width = width
            pressure_height = height
            self.profile_add("pressure_values", time.perf_counter() - pressure_values_t0)
            pressure_pack_t0 = time.perf_counter()
            blob, max_value = pack_pressure(
                values,
                self.args.pressure_type,
                self.args.force_scale,
                normalize=self.args.pressure_normalize,
            )
            self.profile_add("pressure_pack", time.perf_counter() - pressure_pack_t0)
            self.send_raster_blob(
                "/sensel_morph/pressure",
                [frame.frame_id, width, height, bit_depth(self.args.pressure_type), float(max_value)],
                blob,
            )

        if frame.labels is not None and self.args.labels:
            labels_values_t0 = time.perf_counter()
            label_blob, width, height = label_values_for_output(frame.labels, self.args.label_res or self.args.pressure_res)
            label_width = width
            label_height = height
            self.profile_add("labels_values", time.perf_counter() - labels_values_t0)
            self.send_raster_blob("/sensel_morph/labels", [frame.frame_id, width, height], label_blob)

        if frame.contacts is not None and self.args.contacts:
            contacts_t0 = time.perf_counter()
            ellipse_t0 = time.perf_counter()
            raster_ellipses = (
                raster_ellipses_by_label(frame.pressure, frame.labels)
                if frame.pressure is not None and frame.labels is not None and self.args.pressure and self.args.labels
                else {}
            )
            self.profile_add("contact_ellipses", time.perf_counter() - ellipse_t0)
            raster_peaks = raster_peaks_by_label(
                pressure_values,
                pressure_width,
                pressure_height,
                label_blob,
                label_width,
                label_height,
                force_scale=self.args.force_scale,
            )
            self.send_contacts(frame, raster_peaks, raster_ellipses)
            self.profile_add("contacts_send", time.perf_counter() - contacts_t0)

        if frame.accel is not None:
            accel_t0 = time.perf_counter()
            x, y, z = frame.accel
            self.client.send_message(
                "/sensel_morph/accelerometer",
                [frame.frame_id, x, y, z, x / self.args.accel_counts_per_g, y / self.args.accel_counts_per_g, z / self.args.accel_counts_per_g],
            )
            self.profile_add("accel_send", time.perf_counter() - accel_t0)

        sync_t0 = time.perf_counter()
        self.client.send_message("/sensel_morph/sync", [frame.frame_id])
        self.profile_add("send_sync_msg", time.perf_counter() - sync_t0)

    def send_raster_blob(self, address: str, header: list[int | float], blob: bytes) -> None:
        name = "pressure" if address.endswith("/pressure") else "labels"
        self.profile_add(f"{name}_raw_bytes", float(len(blob)))
        if self.args.rle:
            rle_t0 = time.perf_counter()
            encoded = rle_encode(blob)
            self.profile_add(f"{name}_rle_encode", time.perf_counter() - rle_t0)
            self.profile_add(f"{name}_sent_bytes", float(len(encoded)))
            send_t0 = time.perf_counter()
            self.send_blob(f"{address}_rle", [*header, len(blob)], encoded)
            self.profile_add(f"{name}_send_blob", time.perf_counter() - send_t0)
            return
        self.profile_add(f"{name}_sent_bytes", float(len(blob)))
        send_t0 = time.perf_counter()
        self.send_blob(address, header, blob)
        self.profile_add(f"{name}_send_blob", time.perf_counter() - send_t0)

    def send_blob(self, address: str, header: list[int | float], blob: bytes) -> None:
        if self.args.chunk_size <= 0 or len(blob) <= self.args.chunk_size:
            self.client.send_message(address, [*header, blob])
            return

        chunk_count = math.ceil(len(blob) / self.args.chunk_size)
        self.client.send_message(f"{address}/start", [*header, len(blob), chunk_count])
        frame_id = int(header[0])
        for chunk_index in range(chunk_count):
            start = chunk_index * self.args.chunk_size
            chunk = blob[start : start + self.args.chunk_size]
            self.client.send_message(f"{address}/chunk", [frame_id, chunk_index, chunk_count, chunk])

    def profile_add(self, name: str, value: float) -> None:
        if not self.args.profile:
            return
        self.profile_totals[name] = self.profile_totals.get(name, 0.0) + value
        self.profile_counts[name] = self.profile_counts.get(name, 0) + 1

    def print_profile(self, frame_count: int) -> None:
        if frame_count <= 0:
            return
        order = [
            "read_frame",
            "decode_frame",
            "pressure_values",
            "pressure_pack",
            "pressure_rle_encode",
            "pressure_send_blob",
            "labels_values",
            "labels_pack",
            "labels_rle_encode",
            "labels_send_blob",
            "contact_ellipses",
            "contacts_send",
            "send_frame_msg",
            "send_sync_msg",
            "led_update",
            "total_success_loop",
            "pressure_raw_bytes",
            "pressure_sent_bytes",
            "labels_raw_bytes",
            "labels_sent_bytes",
        ]
        print("profile averages:", file=sys.stderr)
        for name in order:
            if name not in self.profile_totals:
                continue
            count = max(1, self.profile_counts.get(name, frame_count))
            average = self.profile_totals[name] / count
            if name.endswith("_bytes"):
                print(f"  {name}: {average:.1f} bytes/event", file=sys.stderr)
            else:
                print(f"  {name}: {average * 1000.0:.3f} ms/event", file=sys.stderr)

    def send_contacts(
        self,
        frame: LiveFrame,
        raster_peaks: dict[int, RasterPeak] | None = None,
        raster_ellipses: dict[int, RasterEllipse] | None = None,
    ) -> None:
        assert frame.contacts is not None
        contacts = frame.contacts.contacts
        self.client.send_message("/sensel_morph/contacts", [frame.frame_id, len(contacts)])
        self.send_contact_summary(frame.frame_id, contacts, raster_ellipses)
        for contact in contacts:
            peak = contact_peak(contact, raster_peaks)
            ellipse = contact_ellipse(contact, raster_ellipses)
            self.client.send_message(
                "/sensel_morph/contact",
                [
                    frame.frame_id,
                    contact.id,
                    contact.state,
                    ellipse.x_mm,
                    ellipse.y_mm,
                    contact.force,
                    int(contact.area),
                    ellipse.orientation_deg,
                    ellipse.major_axis_mm,
                    ellipse.minor_axis_mm,
                    contact.delta_x_mm or 0.0,
                    contact.delta_y_mm or 0.0,
                    contact.delta_force or 0.0,
                    contact.delta_area or 0.0,
                    contact.min_x_mm or 0.0,
                    contact.min_y_mm or 0.0,
                    contact.max_x_mm or 0.0,
                    contact.max_y_mm or 0.0,
                    peak.x_mm,
                    peak.y_mm,
                    peak.force,
                ],
            )

        compat = set(self.args.compat)
        if "morphosc" in compat:
            self.send_morphosc_contacts(contacts, raster_ellipses)
        if "senselosc" in compat:
            self.send_senselosc_contacts(frame.frame_id, contacts, raster_peaks, raster_ellipses)

    def send_contact_summary(
        self,
        frame_id: int,
        contacts: Sequence[dp.DecodedContact],
        raster_ellipses: dict[int, RasterEllipse] | None = None,
    ) -> None:
        stats = contact_stats(contacts, raster_ellipses)
        self.client.send_message(
            "/sensel_morph/contact_summary",
            [
                frame_id,
                len(contacts),
                stats["x"] / ACTIVE_W_MM,
                stats["y"] / ACTIVE_H_MM,
                stats["x_w"] / ACTIVE_W_MM,
                stats["y_w"] / ACTIVE_H_MM,
                stats["total_force"],
                stats["avg_force"],
                stats["area"],
                stats["avg_dist"],
                stats["avg_wdist"],
            ],
        )

    def send_morphosc_contacts(
        self,
        contacts: Sequence[dp.DecodedContact],
        raster_ellipses: dict[int, RasterEllipse] | None = None,
    ) -> None:
        if self.last_contact_count != len(contacts):
            self.client.send_message("/num_contacts", [len(contacts)])
            self.last_contact_count = len(contacts)
        if not contacts:
            return

        self.client.send_message("/spread", [average_contact_distance(contacts, raster_ellipses)])
        self.client.send_message("/total_force", [sum(contact.force for contact in contacts)])
        for contact in contacts:
            ellipse = contact_ellipse(contact, raster_ellipses)
            self.client.send_message("/lifecycle", [contact.id, CONTACT_STATE_NAMES.get(contact.state, str(contact.state))])
            self.client.send_message("/x_position", [contact.id, ellipse.x_mm])
            self.client.send_message("/y_position", [contact.id, ellipse.y_mm])
            self.client.send_message("/force", [contact.id, contact.force])

    def send_senselosc_contacts(
        self,
        frame_id: int,
        contacts: Sequence[dp.DecodedContact],
        raster_peaks: dict[int, RasterPeak] | None = None,
        raster_ellipses: dict[int, RasterEllipse] | None = None,
    ) -> None:
        stats = contact_stats(contacts, raster_ellipses)
        self.client.send_message(
            "/contactAvg",
            [
                0,
                len(contacts),
                stats["x"],
                stats["y"],
                stats["avg_force"],
                stats["avg_dist"],
                int(stats["area"]),
                stats["x_w"],
                stats["y_w"],
                stats["total_force"],
                stats["avg_wdist"],
            ],
        )
        updated = [0] * 16
        for index, contact in enumerate(contacts):
            if 0 <= contact.id < len(updated):
                updated[contact.id] = 1
            ellipse = contact_ellipse(contact, raster_ellipses)
            dist = distance(ellipse.x_mm, ellipse.y_mm, stats["x"], stats["y"])
            wdist = distance(ellipse.x_mm, ellipse.y_mm, stats["x_w"], stats["y_w"])
            self.client.send_message(
                "/contact",
                [
                    0,
                    contact.id,
                    contact.state,
                    ellipse.x_mm,
                    ellipse.y_mm,
                    contact.force,
                    int(contact.area),
                    dist,
                    wdist,
                    ellipse.orientation_deg,
                    ellipse.major_axis_mm,
                    ellipse.minor_axis_mm,
                ],
            )
            self.client.send_message(
                "/contactDelta",
                [
                    0,
                    contact.id,
                    contact.state,
                    contact.delta_x_mm or 0.0,
                    contact.delta_y_mm or 0.0,
                    contact.delta_force or 0.0,
                    int(contact.delta_area or 0),
                ],
            )
            self.client.send_message(
                "/contactBB",
                [
                    0,
                    contact.id,
                    contact.state,
                    contact.min_x_mm or 0.0,
                    contact.min_y_mm or 0.0,
                    contact.max_x_mm or 0.0,
                    contact.max_y_mm or 0.0,
                ],
            )
            peak = contact_peak(contact, raster_peaks)
            self.client.send_message(
                "/contactPeak",
                [
                    0,
                    contact.id,
                    contact.state,
                    peak.x_mm,
                    peak.y_mm,
                    peak.force,
                ],
            )
        self.client.send_message("/sync", [0, *updated])


def frame_content_mask(args: argparse.Namespace) -> int:
    """Return the Morph frame-content bitmask implied by output and LED modes."""
    mask = CONTENT_BITS["accelerometer"]
    for name, bit in CONTENT_BITS.items():
        if name == "accelerometer":
            continue
        if getattr(args, name):
            mask |= bit
    if led_mode_requires_pressure(args):
        mask |= CONTENT_BITS["pressure"]
    return mask


def scan_detail_value_for_pressure_res(pressure_res: str) -> int:
    """Map public pressure resolution names to the firmware scan-detail register."""
    detail_name = SCAN_DETAIL_BY_PRESSURE_RES[pressure_res]
    return SCAN_DETAILS[detail_name]


def led_mode_requires_pressure(args: argparse.Namespace) -> bool:
    """Return true when an LED mode needs raw pressure, not just contact force."""
    mode = getattr(args, "led_mode_name", None)
    if mode in LED_SPATIAL_PRESSURE_MODES:
        return True
    if mode in LED_TOTAL_FORCE_MODES:
        return not bool(getattr(args, "contacts", False))
    return False


def led_frame_due(args: argparse.Namespace, frame_count: int) -> bool:
    """Throttle LED writes by frame count so OSC throughput stays prioritized."""
    interval = max(1, int(getattr(args, "led_frame_interval", 4)))
    return frame_count % interval == 0


class LedModeAction(argparse.Action):
    """Parse ``--led-mode`` values, including the two-token ``all N`` mode."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[str],
        option_string: str | None = None,
    ) -> None:
        items = [values] if isinstance(values, str) else list(values)
        if not items:
            parser.error("--led-mode requires a mode")
        mode = items[0].lower()
        if mode not in LED_MODES:
            parser.error(f"--led-mode must be one of {', '.join(LED_MODES)}")
        if mode == "all":
            if len(items) != 2:
                parser.error("--led-mode all requires one brightness value")
            try:
                brightness = int(items[1], 0)
            except ValueError:
                parser.error("--led-mode all brightness must be an integer")
            setattr(namespace, "led_mode", items)
            setattr(namespace, "led_mode_name", "all")
            setattr(namespace, "led_all_brightness", brightness)
            return
        if len(items) != 1:
            parser.error(f"--led-mode {mode} does not accept extra values")
        setattr(namespace, "led_mode", items)
        setattr(namespace, "led_mode_name", mode)
        setattr(namespace, "led_all_brightness", None)


def led_args_for_osc(args: argparse.Namespace) -> argparse.Namespace:
    """Translate OSC CLI names into the argument object used by LED helpers."""
    return argparse.Namespace(
        mode_name=args.led_mode_name,
        pressure_ref=args.led_pressure_ref,
        pressure_floor=args.led_pressure_floor,
        column_threshold=args.led_column_threshold,
        pulse_min_step=args.led_pulse_min_step,
        pulse_max_step=args.led_pulse_max_step,
        pulse_response_gamma=args.led_pulse_response_gamma,
        kitt_min_step=args.led_kitt_min_step,
        kitt_max_step=args.led_kitt_max_step,
        seed=args.led_seed,
    )


def decode_live_frame(frame: dict[str, object], grids: Iterable[dp.Grid]) -> LiveFrame:
    """Decode one raw serial frame into typed pressure/label/contact sections."""
    payload_hex = frame.get("payload_hex")
    if not isinstance(payload_hex, str):
        raise dp.DecodeError("missing payload_hex")
    payload = bytes.fromhex(payload_hex)
    if len(payload) < dp.FRAME_HEADER_SIZE:
        raise dp.DecodeError("truncated frame header")

    content_mask = payload[0]
    pos = dp.FRAME_HEADER_SIZE
    contacts = None
    accel = None

    if content_mask & 0x04:
        contacts = dp.parse_contacts(payload, pos)
        pos += contacts.bytes_used

    if content_mask & 0x08:
        if pos + 6 > len(payload):
            raise dp.DecodeError("truncated accelerometer section")
        accel = (dp.i16_le(payload, pos), dp.i16_le(payload, pos + 2), dp.i16_le(payload, pos + 4))
        pos += 6

    pressure = None
    labels = None
    if content_mask & 0x03:
        body = payload[pos:]
        if content_mask & 0x01:
            pressure, errors = dp.infer_frame(body, grids, require_all=not bool(content_mask & 0x02))
            if pressure is None:
                raise dp.DecodeError(f"pressure decode failed: {errors}")
            if content_mask & 0x02:
                labels = dp.decode_label_body(body[pressure.bytes_used :], pressure.grid)
        elif content_mask & 0x02:
            labels = decode_labels_without_pressure(body, grids)

    return LiveFrame(
        frame_id=int(frame.get("rolling_counter") or payload[1]),
        timestamp=int(frame.get("timestamp_le") or int.from_bytes(payload[2:6], "little")),
        content_mask=content_mask,
        pressure=pressure,
        labels=labels,
        contacts=contacts,
        accel=accel,
    )


def decode_labels_without_pressure(body: bytes, grids: Iterable[dp.Grid]) -> dp.DecodedLabels:
    """Decode a labels-only frame by trying all plausible source grids."""
    errors: dict[str, str] = {}
    for grid in grids:
        try:
            return dp.decode_label_body(body, grid)
        except dp.DecodeError as exc:
            errors[grid.name] = str(exc)
    raise dp.DecodeError(f"label decode failed: {errors}")


def pressure_values_for_output(
    decoded: dp.DecodedFrame,
    resolution: str,
    calibration: PressureCalibration | None = None,
) -> tuple[list[float], int, int]:
    """Return pressure values at the requested public raster resolution."""
    width, height = PRESSURE_SIZES[resolution]
    if calibration is not None:
        values, high_w, high_h = expand_calibrated_pressure(decoded, calibration)
        if (high_w, high_h) != (calibration.width, calibration.height):
            raise dp.DecodeError(
                f"calibration size {calibration.width}x{calibration.height} does not match pressure {high_w}x{high_h}"
            )
        if resolution == "high":
            return values, high_w, high_h
        return resize_values_nearest(values, high_w, high_h, width, height), width, height

    if resolution == "high":
        return dp.expand_pressure(decoded)
    if decoded.grid.cols < width or decoded.grid.rows < height:
        values, expanded_w, expanded_h = dp.expand_pressure(decoded)
        return resize_values_nearest(values, expanded_w, expanded_h, width, height), width, height
    return resize_values_nearest(decoded.values, decoded.grid.cols, decoded.grid.rows, width, height), width, height


def expand_calibrated_pressure(decoded: dp.DecodedFrame, calibration: PressureCalibration) -> tuple[list[float], int, int]:
    """Apply calibration either before or after Sensel's interpolation kernel."""
    source_calibration = source_calibration_for_grid(calibration, decoded.grid)
    if source_calibration is None:
        values, width, height = dp.expand_pressure(decoded)
        return apply_pressure_calibration(values, calibration), width, height

    source = np.asarray(decoded.values, dtype=np.float64).reshape((decoded.grid.rows, decoded.grid.cols))
    corrected_source = np.maximum(0.0, source - source_calibration.dark) * source_calibration.gain
    x_matrix, y_matrix = dp.interpolation_plan(decoded.grid.cols, decoded.grid.rows, decoded.grid.x_scale, decoded.grid.y_scale)
    expanded = y_matrix @ corrected_source @ x_matrix.T
    return expanded.ravel().tolist(), int(expanded.shape[1]), int(expanded.shape[0])


def apply_pressure_calibration(values: Sequence[int | float], calibration: PressureCalibration) -> list[float]:
    """Apply expanded-pixel dark subtraction and gain correction."""
    raw = np.asarray(values, dtype=np.float64)
    corrected = np.maximum(0.0, raw - calibration.dark) * calibration.gain
    return corrected.tolist()


def source_calibration_for_grid(calibration: PressureCalibration, grid: dp.Grid) -> SourcePressureCalibration | None:
    """Return a cached source-grid calibration fitted for this compressed grid."""
    if calibration.light is None:
        return None
    if calibration.source_maps is None:
        return None

    key = (grid.cols, grid.rows, grid.x_scale, grid.y_scale)
    cached = calibration.source_maps.get(key)
    if cached is not None:
        return cached

    out_cols = (grid.cols - 1) * grid.x_scale + 1
    out_rows = (grid.rows - 1) * grid.y_scale + 1
    if (out_cols, out_rows) != (calibration.width, calibration.height):
        raise dp.DecodeError(
            f"calibration size {calibration.width}x{calibration.height} does not match grid expansion {out_cols}x{out_rows}"
        )

    source = fit_source_calibration(calibration, grid)
    calibration.source_maps[key] = source
    return source


def fit_source_calibration(calibration: PressureCalibration, grid: dp.Grid) -> SourcePressureCalibration:
    """Fit high-res calibration maps back through the Sensel interpolation kernel."""
    x_matrix, y_matrix = dp.interpolation_plan(grid.cols, grid.rows, grid.x_scale, grid.y_scale)
    x_pinv_t = np.linalg.pinv(x_matrix).T
    y_pinv = np.linalg.pinv(y_matrix)

    target = calibration.target if calibration.target > 0 else calibration_target_from_gain(calibration)
    light = np.asarray(calibration.light, dtype=np.float64).reshape((calibration.height, calibration.width))
    coverage = coverage_mask_for_calibration(calibration).reshape((calibration.height, calibration.width))
    filled_light = np.where(coverage, light, target)
    source_light = np.maximum(1.0e-6, y_pinv @ filled_light @ x_pinv_t)

    dark = calibration.dark.reshape((calibration.height, calibration.width))
    source_dark = np.maximum(0.0, y_pinv @ dark @ x_pinv_t)

    source_support = source_support_from_coverage(coverage, x_matrix, y_matrix)
    source_gain = np.clip(target / source_light, calibration.min_gain, calibration.max_gain)
    source_gain = 1.0 + (source_gain - 1.0) * source_support

    return SourcePressureCalibration(
        grid_key=(grid.cols, grid.rows, grid.x_scale, grid.y_scale),
        dark=source_dark,
        gain=source_gain,
        support=source_support,
    )


def coverage_mask_for_calibration(calibration: PressureCalibration) -> np.ndarray:
    """Return pixels covered by a brush/light calibration pass."""
    if calibration.coverage is not None:
        return np.asarray(calibration.coverage, dtype=np.float64) > 0
    if calibration.light is not None:
        return np.asarray(calibration.light, dtype=np.float64) > 0
    return np.ones(calibration.width * calibration.height, dtype=bool)


def source_support_from_coverage(coverage: np.ndarray, x_matrix: np.ndarray, y_matrix: np.ndarray) -> np.ndarray:
    """Project expanded-pixel calibration coverage into compressed source cells."""
    weighted = y_matrix.T @ coverage.astype(np.float64) @ x_matrix
    normalizer = y_matrix.T @ np.ones_like(coverage, dtype=np.float64) @ x_matrix
    support = np.divide(weighted, normalizer, out=np.zeros_like(weighted), where=normalizer > 0)
    return np.clip(support, 0.0, 1.0)


def calibration_target_from_gain(calibration: PressureCalibration) -> float:
    """Choose a stable target response for gain fitting."""
    if calibration.light is not None:
        values = calibration.light[coverage_mask_for_calibration(calibration)]
        if values.size:
            return float(np.median(values))
    return 1.0


def load_pressure_calibration(path_value: str, detected_serial: str) -> PressureCalibration:
    """Load a calibration JSON only when its serial matches the connected Morph."""
    path = resolve_calibration_path(path_value, detected_serial)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    serial = str(data.get("device_serial") or data.get("serial_number") or "")
    if not serial:
        raise SystemExit(f"Refusing calibration without device_serial: {path}")
    if not detected_serial:
        raise SystemExit(f"Refusing calibration {path}: connected device serial could not be detected")
    if serial != detected_serial:
        raise SystemExit(
            f"Refusing calibration {path}: calibration serial {serial!r} does not match connected device {detected_serial!r}"
        )

    filename_serial = serial_from_calibration_filename(path)
    if filename_serial and filename_serial != detected_serial:
        raise SystemExit(
            f"Refusing calibration {path}: filename serial {filename_serial!r} does not match connected device {detected_serial!r}"
        )

    width = int(data.get("width") or 0)
    height = int(data.get("height") or 0)
    if (width, height) != PRESSURE_SIZES["high"]:
        raise SystemExit(f"Calibration must be 185x105, got {width}x{height}: {path}")

    gain_key = "gain"
    cells = width * height
    dark = require_numeric_array(data, "dark", cells, path)
    gain = require_numeric_array(data, gain_key, cells, path)
    coverage = None
    if isinstance(data.get("coverage"), list):
        coverage = require_numeric_array(data, "coverage", cells, path)
    light = None
    if isinstance(data.get("light"), list):
        light = require_numeric_array(data, "light", cells, path)
    target = float(data.get("target") or 0.0)
    min_gain = float(data.get("min_gain") or 0.5)
    max_gain = float(data.get("max_gain") or 2.0)

    return PressureCalibration(
        path=path,
        serial_number=serial,
        width=width,
        height=height,
        dark=dark,
        gain=gain,
        gain_key=gain_key,
        coverage=coverage,
        light=light,
        target=target,
        min_gain=min_gain,
        max_gain=max_gain,
        source_maps={},
    )


def resolve_calibration_path(path_value: str, detected_serial: str = "") -> pathlib.Path:
    """Resolve a calibration file or a directory containing calibration_<serial>.json."""
    path = pathlib.Path(path_value).expanduser()
    candidates = [path]
    if not path.is_absolute():
        candidates.append(pathlib.Path.cwd() / path)
        candidates.append(pathlib.Path(__file__).resolve().parent / path)
    for candidate in candidates:
        if candidate.is_dir():
            if detected_serial:
                serial_named = candidate / f"calibration_{detected_serial}.json"
                if serial_named.exists():
                    return serial_named.resolve()
            serial_named_files = sorted(candidate.glob("calibration_*.json"))
            if len(serial_named_files) == 1:
                return serial_named_files[0].resolve()
            if len(serial_named_files) > 1:
                raise SystemExit(f"Multiple calibration_*.json files in {candidate}; pass the exact file path")
            raise SystemExit(f"No calibration_*.json file found in calibration directory: {candidate}")
        if candidate.exists():
            return candidate.resolve()
    raise SystemExit(f"Calibration file not found: {path_value}")


def serial_from_calibration_filename(path: pathlib.Path) -> str:
    """Extract the serial from calibration_<serial>.json when present."""
    match = re.search(r"calibration_([A-Za-z0-9]+)\.json$", path.name)
    return match.group(1) if match else ""


def require_numeric_array(data: dict[str, object], key: str, count: int, path: pathlib.Path) -> np.ndarray:
    """Read a fixed-length numeric calibration array or raise a CLI error."""
    values = data.get(key)
    if not isinstance(values, list) or len(values) != count:
        raise SystemExit(f"Calibration {key!r} must have {count} values: {path}")
    try:
        return np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Calibration {key!r} contains non-numeric values: {path}") from exc


def label_values_for_output(labels: dp.DecodedLabels, resolution: str) -> tuple[bytes, int, int]:
    """Return label IDs at the requested output resolution using nearest sampling."""
    width, height = PRESSURE_SIZES[resolution]
    if labels.grid.cols == width and labels.grid.rows == height:
        return np.asarray(labels.values, dtype=np.uint8).tobytes(), width, height
    source = np.asarray(labels.values, dtype=np.uint8).reshape((labels.grid.rows, labels.grid.cols))
    y_indices = np.minimum(labels.grid.rows - 1, ((np.arange(height) + 0.5) * labels.grid.rows / height).astype(np.intp))
    x_indices = np.minimum(labels.grid.cols - 1, ((np.arange(width) + 0.5) * labels.grid.cols / width).astype(np.intp))
    return source[np.ix_(y_indices, x_indices)].tobytes(), width, height


def raster_ellipses_by_label(
    pressure_frame: dp.DecodedFrame | None,
    labels_frame: dp.DecodedLabels | None,
) -> dict[int, RasterEllipse]:
    """Fit fresh pressure-weighted ellipses from current pressure and label masks."""
    if pressure_frame is None or labels_frame is None:
        return {}
    pressure_w = pressure_frame.grid.cols
    pressure_h = pressure_frame.grid.rows
    label_w = labels_frame.grid.cols
    label_h = labels_frame.grid.rows
    if pressure_w <= 0 or pressure_h <= 0 or label_w <= 0 or label_h <= 0:
        return {}
    if len(pressure_frame.values) != pressure_w * pressure_h or len(labels_frame.values) != label_w * label_h:
        return {}

    pressure = np.asarray(pressure_frame.values, dtype=np.float64).reshape((pressure_h, pressure_w))
    labels = np.asarray(labels_frame.values, dtype=np.uint8).reshape((label_h, label_w))
    if labels.shape != pressure.shape:
        y_indices = np.minimum(label_h - 1, ((np.arange(pressure_h) + 0.5) * label_h / pressure_h).astype(np.intp))
        x_indices = np.minimum(label_w - 1, ((np.arange(pressure_w) + 0.5) * label_w / pressure_w).astype(np.intp))
        labels = labels[np.ix_(y_indices, x_indices)]

    ellipses: dict[int, RasterEllipse] = {}
    for label_id in np.unique(labels):
        label = int(label_id)
        if label == 255:
            continue
        mask = labels == label_id
        if not bool(mask.any()):
            continue
        fit = fit_pressure_moment_ellipse(label, pressure, mask)
        if fit is not None:
            ellipses[label] = fit
    return ellipses


def fit_pressure_moment_ellipse(label: int, pressure: np.ndarray, mask: np.ndarray) -> RasterEllipse | None:
    """Compute a contact ellipse from pressure-weighted second moments."""
    area_cells = int(mask.sum())
    if area_cells < 2:
        return None

    y_indices, x_indices = np.nonzero(mask)
    weights = np.maximum(pressure[y_indices, x_indices], 0.0)
    total_weight = float(weights.sum())
    if total_weight <= 0.0:
        return None

    height, width = pressure.shape
    x_mm = (x_indices.astype(np.float64) + 0.5) * ACTIVE_W_MM / width
    y_mm = (y_indices.astype(np.float64) + 0.5) * ACTIVE_H_MM / height
    mean_x = float(np.sum(weights * x_mm) / total_weight)
    mean_y = float(np.sum(weights * y_mm) / total_weight)
    dx = x_mm - mean_x
    dy = y_mm - mean_y
    cov_xx = float(np.sum(weights * dx * dx) / total_weight)
    cov_xy = float(np.sum(weights * dx * dy) / total_weight)
    cov_yy = float(np.sum(weights * dy * dy) / total_weight)
    covariance = np.array([[cov_xx, cov_xy], [cov_xy, cov_yy]], dtype=np.float64)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    major_value = max(0.0, float(eigenvalues[order[0]]))
    minor_value = max(0.0, float(eigenvalues[order[1]]))
    major_vector = eigenvectors[:, order[0]]
    major_angle = math.degrees(math.atan2(float(major_vector[1]), float(major_vector[0])))
    return RasterEllipse(
        x_mm=mean_x,
        y_mm=mean_y,
        orientation_deg=wrap_contact_degrees(major_angle - 90.0),
        major_axis_mm=4.0 * math.sqrt(major_value),
        minor_axis_mm=4.0 * math.sqrt(minor_value),
        area_cells=area_cells,
    )


def raster_peaks_by_label(
    pressure_values: Sequence[int | float] | None,
    pressure_w: int,
    pressure_h: int,
    label_blob: bytes | None,
    label_w: int,
    label_h: int,
    force_scale: float = 1.0,
) -> dict[int, RasterPeak]:
    """Find each contact's brightest pressure cell inside its current label mask."""
    if pressure_values is None or label_blob is None:
        return {}
    if pressure_w <= 0 or pressure_h <= 0 or label_w <= 0 or label_h <= 0:
        return {}
    expected_pressure = pressure_w * pressure_h
    expected_labels = label_w * label_h
    if len(pressure_values) != expected_pressure or len(label_blob) != expected_labels:
        return {}

    pressure = np.asarray(pressure_values, dtype=np.float64).reshape((pressure_h, pressure_w))
    labels = np.frombuffer(label_blob, dtype=np.uint8).reshape((label_h, label_w))
    if labels.shape != pressure.shape:
        y_indices = np.minimum(label_h - 1, ((np.arange(pressure_h) + 0.5) * label_h / pressure_h).astype(np.intp))
        x_indices = np.minimum(label_w - 1, ((np.arange(pressure_w) + 0.5) * label_w / pressure_w).astype(np.intp))
        labels = labels[np.ix_(y_indices, x_indices)]

    scale = force_scale if force_scale else 1.0
    peaks: dict[int, RasterPeak] = {}
    for label_id in np.unique(labels):
        label = int(label_id)
        if label == 255:
            continue
        mask = labels == label_id
        if not bool(mask.any()):
            continue
        y_indices, x_indices = np.nonzero(mask)
        values = pressure[y_indices, x_indices]
        best_index = int(np.argmax(values))
        y = int(y_indices[best_index])
        x = int(x_indices[best_index])
        peaks[label] = RasterPeak(
            x_mm=(x + 0.5) * ACTIVE_W_MM / pressure_w,
            y_mm=(y + 0.5) * ACTIVE_H_MM / pressure_h,
            force=float(values[best_index]) / scale,
        )
    return peaks


def contact_peak(contact: dp.DecodedContact, raster_peaks: dict[int, RasterPeak] | None = None) -> RasterPeak:
    """Prefer fresh raster peaks, falling back to firmware contact peaks."""
    if raster_peaks is not None and contact.id in raster_peaks:
        return raster_peaks[contact.id]
    return RasterPeak(contact.peak_x_mm or 0.0, contact.peak_y_mm or 0.0, contact.peak_force or 0.0)


def contact_ellipse(contact: dp.DecodedContact, raster_ellipses: dict[int, RasterEllipse] | None = None) -> RasterEllipse:
    """Prefer fresh raster ellipses, falling back to firmware contact ellipses."""
    if raster_ellipses is not None and contact.id in raster_ellipses:
        return raster_ellipses[contact.id]
    return RasterEllipse(
        x_mm=contact.x_mm,
        y_mm=contact.y_mm,
        orientation_deg=contact.orientation_deg or 0.0,
        major_axis_mm=contact.major_axis_mm or 0.0,
        minor_axis_mm=contact.minor_axis_mm or 0.0,
        area_cells=int(contact.area),
    )


def wrap_contact_degrees(angle: float) -> float:
    """Match Sensel's -90..90 degree orientation convention."""
    while angle <= -90.0:
        angle += 180.0
    while angle > 90.0:
        angle -= 180.0
    return angle


def resize_values_nearest(
    values: Sequence[int | float],
    src_w: int,
    src_h: int,
    dst_w: int,
    dst_h: int,
) -> list[int | float]:
    """Resize a scalar raster without interpolation, preserving physical bins."""
    if src_w == dst_w and src_h == dst_h:
        return list(values)
    out: list[int | float] = []
    for y in range(dst_h):
        src_y = min(src_h - 1, int((y + 0.5) * src_h / dst_h))
        row = src_y * src_w
        for x in range(dst_w):
            src_x = min(src_w - 1, int((x + 0.5) * src_w / dst_w))
            out.append(values[row + src_x])
    return out


def pack_pressure(
    values: Sequence[float],
    pressure_type: str,
    force_scale: float,
    normalize: bool = False,
) -> tuple[bytes, float]:
    """Pack floating pressure values to uint8/uint16 and return pre-clamp max."""
    scaled = np.asarray(values, dtype=np.float64)
    if force_scale != 1.0:
        scaled = scaled / force_scale
    scaled = np.maximum(scaled, 0.0)
    max_value = float(scaled.max()) if scaled.size else 0.0
    if pressure_type == "uint8":
        if normalize:
            multiplier = 255.0 / max_value if max_value else 0.0
            packed = np.rint(scaled * multiplier)
        else:
            packed = np.rint(scaled)
        return np.clip(packed, 0, 255).astype(np.uint8).tobytes(), max_value
    packed16 = np.clip(np.rint(scaled), 0, 65535).astype("<u2")
    return packed16.tobytes(), max_value


def rle_encode(blob: bytes) -> bytes:
    """Encode bytes as simple [run_length, value] pairs."""
    if not blob:
        return b""
    out = bytearray()
    run_value = blob[0]
    run_count = 1
    for value in blob[1:]:
        if value == run_value and run_count < 255:
            run_count += 1
        else:
            out.append(run_count)
            out.append(run_value)
            run_value = value
            run_count = 1
    out.append(run_count)
    out.append(run_value)
    return bytes(out)


def bit_depth(pressure_type: str) -> int:
    return 8 if pressure_type == "uint8" else 16


def clamp_u8(value: int | float) -> int:
    return max(0, min(255, int(value)))


def clamp_u16(value: int | float) -> int:
    return max(0, min(65535, int(value)))


def distance(x0: float, y0: float, x1: float, y1: float) -> float:
    return math.hypot(x0 - x1, y0 - y1)


def average_contact_distance(
    contacts: Sequence[dp.DecodedContact],
    raster_ellipses: dict[int, RasterEllipse] | None = None,
) -> float:
    if len(contacts) < 2:
        return 0.0
    ellipses = [contact_ellipse(contact, raster_ellipses) for contact in contacts]
    cx = sum(ellipse.x_mm for ellipse in ellipses) / len(ellipses)
    cy = sum(ellipse.y_mm for ellipse in ellipses) / len(ellipses)
    return sum(distance(ellipse.x_mm, ellipse.y_mm, cx, cy) for ellipse in ellipses) / len(ellipses)


def contact_stats(
    contacts: Sequence[dp.DecodedContact],
    raster_ellipses: dict[int, RasterEllipse] | None = None,
) -> dict[str, float]:
    if not contacts:
        return {
            "x": 0.0,
            "y": 0.0,
            "avg_force": 0.0,
            "avg_dist": 0.0,
            "area": 0.0,
            "x_w": 0.0,
            "y_w": 0.0,
            "total_force": 0.0,
            "avg_wdist": 0.0,
        }

    n = len(contacts)
    ellipses = [contact_ellipse(contact, raster_ellipses) for contact in contacts]
    x = sum(ellipse.x_mm for ellipse in ellipses) / n
    y = sum(ellipse.y_mm for ellipse in ellipses) / n
    total_force = sum(contact.force for contact in contacts)
    area = sum(contact.area for contact in contacts)
    if total_force:
        x_w = sum(ellipse.x_mm * contact.force for ellipse, contact in zip(ellipses, contacts)) / total_force
        y_w = sum(ellipse.y_mm * contact.force for ellipse, contact in zip(ellipses, contacts)) / total_force
    else:
        x_w = x
        y_w = y
    avg_dist = sum(distance(ellipse.x_mm, ellipse.y_mm, x, y) for ellipse in ellipses) / n
    avg_wdist = sum(distance(ellipse.x_mm, ellipse.y_mm, x_w, y_w) for ellipse in ellipses) / n
    return {
        "x": x,
        "y": y,
        "avg_force": total_force / n,
        "avg_dist": avg_dist,
        "area": area / n,
        "x_w": x_w,
        "y_w": y_w,
        "total_force": total_force,
        "avg_wdist": avg_wdist,
    }


def parse_compat(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value == "none":
            continue
        out.extend(item.strip() for item in value.split(",") if item.strip())
    invalid = sorted(set(out) - {"morphosc", "senselosc"})
    if invalid:
        raise argparse.ArgumentTypeError(f"invalid compat mode(s): {', '.join(invalid)}")
    return sorted(set(out))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Broadcast live Sensel Morph pressure, labels, contacts, and accelerometer data over OSC.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="OSC destination host.")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_OSC_PORT,
        help="OSC destination UDP port. Default is 1560, the Morph product ID 0x0618 in decimal.",
    )
    parser.add_argument("--device", default=None, help="Serial device path. Defaults to first /dev/cu.usbmodem*.")
    parser.add_argument("--pressure", action="store_true", help="Send pressure raster frames.")
    parser.add_argument("--labels", action="store_true", help="Send label raster frames.")
    parser.add_argument("--contacts", action="store_true", help="Send contact frames.")
    parser.add_argument("--accelerometer", action="store_true", help="Accepted for compatibility; accelerometer frames are always sent.")
    parser.add_argument("--pressure-res", choices=sorted(PRESSURE_SIZES), default="high")
    parser.add_argument("--label-res", choices=sorted(PRESSURE_SIZES), default=None)
    parser.add_argument("--pressure-type", choices=("uint8", "uint16"), default="uint8")
    parser.add_argument(
        "--pressure-normalize",
        action="store_true",
        help="For uint8 pressure, scale each frame so its maximum pressure becomes 255. Default is absolute clamp.",
    )
    parser.add_argument(
        "--force-scale",
        type=float,
        default=1.0,
        help="Divide pressure values before packing. Use 8 for SDK-style force units on this Morph.",
    )
    parser.add_argument(
        "--calibration",
        default=None,
        help=(
            "Path to calibration JSON or a calibrator output directory. "
            "The JSON device_serial, and filename serial when present, must match the connected Morph."
        ),
    )
    parser.add_argument(
        "--compat",
        action="append",
        default=[],
        help="Also emit compatibility contact OSC. Use none, morphosc, senselosc, or comma-separated values.",
    )
    parser.add_argument("--fps-limit", type=float, default=0.0, help="Limit sent frame rate; 0 means unbounded.")
    parser.add_argument("--max-frames", type=int, default=None, help="Stop after this many sent frames.")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=(
            "Chunk raster blobs larger than this many bytes. Default 4096 keeps "
            "high-resolution pressure frames below UDP datagram limits. Use 0 "
            "to send one OSC blob per raster."
        ),
    )
    parser.add_argument(
        "--rle",
        action="store_true",
        help="Send pressure and label raster OSC blobs as byte-RLE on _rle addresses. Raw raster frames are not mixed into an RLE run.",
    )
    parser.add_argument("--profile", action="store_true", help="Print per-stage transmitter timing and raster byte averages on exit.")
    parser.add_argument("--read-timeout", type=float, default=1.0)
    parser.add_argument("--accel-counts-per-g", type=float, default=15600.0)
    parser.set_defaults(led_mode_name=None, led_all_brightness=None)
    parser.add_argument(
        "--led-mode",
        nargs="+",
        action=LedModeAction,
        metavar="MODE",
        help=(
            "Drive the 24-LED strip while broadcasting OSC. Modes: "
            "glow, pulse, kitt, twinkle, columns, meter, or all BRIGHTNESS. "
            "If omitted, all LEDs are set to 0."
        ),
    )
    parser.add_argument("--led-pressure-ref", type=float, default=ledctl.DEFAULT_PRESSURE_REF)
    parser.add_argument("--led-pressure-floor", type=float, default=ledctl.DEFAULT_PRESSURE_FLOOR)
    parser.add_argument("--led-column-threshold", type=float, default=ledctl.DEFAULT_PRESSURE_FLOOR)
    parser.add_argument(
        "--led-frame-interval",
        type=int,
        default=4,
        help="Update pressure-responsive LED modes every N decoded frames. Default 4.",
    )
    parser.add_argument(
        "--led-read-timeout",
        type=float,
        default=1.0,
        help="Accepted for parity with sensel_morph_led; sensel_morph_osc uses --read-timeout for frame reads.",
    )
    parser.add_argument("--led-pulse-min-step", type=float, default=0.025)
    parser.add_argument("--led-pulse-max-step", type=float, default=1.25)
    parser.add_argument("--led-pulse-response-gamma", type=float, default=2.5)
    parser.add_argument("--led-kitt-min-step", type=float, default=0.08)
    parser.add_argument("--led-kitt-max-step", type=float, default=1.4)
    parser.add_argument("--led-seed", type=int, default=None)
    parser.add_argument("--led-write-timeout", type=float, default=1.0, help="Timeout for draining pipelined LED write acknowledgements.")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.compat = parse_compat(args.compat)
    return SenselMorphOsc(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
