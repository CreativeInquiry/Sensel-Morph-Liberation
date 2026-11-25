#!/usr/bin/env python3
"""Broadcast live Sensel Morph contacts as TUIO 1.1 cursor and blob profiles."""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Sequence

from pythonosc.osc_bundle_builder import OscBundleBuilder
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.udp_client import UDPClient
from pythontuio import Blob, Cursor
from pythontuio.const import TUIO_ALIVE, TUIO_BLOB, TUIO_CURSOR, TUIO_END, TUIO_SOURCE

import decode_pressure as dp
import morph_capture as mc


ACTIVE_W_MM = 230.0
ACTIVE_H_MM = 130.0
DEFAULT_SENSOR_COLS = 185
DEFAULT_SENSOR_ROWS = 105
DEFAULT_TUIO_PORT = 3333
DEFAULT_MIN_CONTACT_FORCE = 10.0
DEFAULT_SCAN_DETAIL = "high"
DEFAULT_FINGER_SIZE_MM = 20.0
DEFAULT_MIN_FINGER_WIDTH_MM = 3.0
DEFAULT_ELONGATED_ASPECT_RATIO = 2.0
DEFAULT_ELONGATED_MIN_FORCE_DENSITY = 0.35
DEFAULT_CLUSTER_MIN_CONTACTS = 3
DEFAULT_CLUSTER_DISTANCE_MM = 15.0
REG_CONTACTS_MASK = 0x4B
REG_SENSOR_NUM_COLS = 0x10
REG_SENSOR_NUM_ROWS = 0x12
REG_SENSOR_ACTIVE_AREA_WIDTH_UM = 0x14
REG_SENSOR_ACTIVE_AREA_HEIGHT_UM = 0x18
REG_UNIT_SHIFT_FORCE = 0xA1
REG_UNIT_SHIFT_AREA = 0xA2
CONTACT_MASK_NONE = 0x00
CONTACT_MASK_ELLIPSE = 0x01
FRAME_CONTENT_CONTACTS = 0x04
CONTACT_STATE_INVALID = 0
CONTACT_STATE_START = 1
CONTACT_STATE_MOVE = 2
CONTACT_STATE_END = 3
SCAN_DETAILS = {
    "high": 0,
    "medium": 1,
    "low": 2,
}
TAU = math.tau
IMMEDIATE_OSC_TIMESTAMP = 0


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def wrap_angle_delta(delta: float) -> float:
    if delta > math.pi:
        return delta - TAU
    if delta < -math.pi:
        return delta + TAU
    return delta


def _osc_message(address: str, *args: object) -> object:
    builder = OscMessageBuilder(address=address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build()


class SenselMorphTuioServer(UDPClient):
    """TUIO 1.1 sender using python-tuio Cursor/Blob set-message encoding.

    Unlike python-tuio 0.0.9's stock server, this sender emits ordered
    profile-specific cursor/blob bundles with source and incrementing fseq
    messages, and without the unused object profile.
    """

    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = DEFAULT_TUIO_PORT,
    ) -> None:
        self._fseq = 0
        self._fseq_lock = threading.Lock()
        self._source_name = ""
        super().__init__(ip, port)

    def set_source_name(self, name: str, ip: str | None = None) -> None:
        """Set the optional TUIO source string sent before alive in each bundle."""
        self._source_name = f"{name}@{ip}" if ip else str(name)

    def next_fseq(self) -> int:
        with self._fseq_lock:
            value = self._fseq
            self._fseq = (self._fseq + 1) % (2**31)
            return value

    def build_profile_bundle(
        self,
        address: str,
        active_profiles: Sequence[Cursor | Blob],
        set_profiles: Sequence[Cursor | Blob],
        fseq: int,
    ) -> object:
        """Build one ordered TUIO 1.1 profile-specific bundle."""
        builder = OscBundleBuilder(IMMEDIATE_OSC_TIMESTAMP)
        if self._source_name:
            builder.add_content(_osc_message(address, TUIO_SOURCE, self._source_name))

        alive_args: list[object] = [TUIO_ALIVE]
        alive_args.extend(int(profile.session_id) for profile in active_profiles)
        builder.add_content(_osc_message(address, *alive_args))

        for profile in set_profiles:
            builder.add_content(profile.get_message())

        builder.add_content(_osc_message(address, TUIO_END, int(fseq)))
        return builder.build()

    def send_profile_frame(
        self,
        address: str,
        active_profiles: Sequence[Cursor | Blob],
        set_profiles: Sequence[Cursor | Blob],
        fseq: int,
    ) -> None:
        self.send(self.build_profile_bundle(address, active_profiles, set_profiles, fseq))

    def send_empty_cursor_bundle(self) -> None:
        self.send_profile_frame(TUIO_CURSOR, [], [], -1)

    def send_empty_blob_bundle(self) -> None:
        self.send_profile_frame(TUIO_BLOB, [], [], -1)

    def send_empty_bundles(self, include_blob: bool = False) -> None:
        self.send_empty_cursor_bundle()
        if include_blob:
            self.send_empty_blob_bundle()

@dataclass(frozen=True)
class DeviceGeometry:
    sensor_cols: int = DEFAULT_SENSOR_COLS
    sensor_rows: int = DEFAULT_SENSOR_ROWS
    active_width_mm: float = ACTIVE_W_MM
    active_height_mm: float = ACTIVE_H_MM
    area_value_scale: float = 1.0
    force_value_scale: float = 8.0


def contact_force_grams(contact: dp.DecodedContact, geometry: DeviceGeometry) -> float:
    return float(contact.force) * 8.0 / geometry.force_value_scale


@dataclass(frozen=True)
class ContactShapeFeatures:
    width_mm: float | None
    length_mm: float | None
    force_density_g_mm2: float | None


@dataclass(frozen=True)
class ContactFrameContext:
    contact_count: int = 1
    nearest_contact_distance_mm: float | None = None


def contact_area_mm2(contact: dp.DecodedContact, geometry: DeviceGeometry) -> float:
    area_scale = geometry.area_value_scale if geometry.area_value_scale > 0.0 else 1.0
    sensor_elements = max(0.0, float(contact.area) / area_scale)
    cell_width_mm = geometry.active_width_mm / max(1, geometry.sensor_cols)
    cell_height_mm = geometry.active_height_mm / max(1, geometry.sensor_rows)
    return sensor_elements * cell_width_mm * cell_height_mm


def contact_shape_features(contact: dp.DecodedContact, geometry: DeviceGeometry) -> ContactShapeFeatures:
    axes = sorted(
        float(axis)
        for axis in (contact.major_axis_mm, contact.minor_axis_mm)
        if axis is not None and math.isfinite(float(axis)) and float(axis) > 0.0
    )
    width_mm = axes[0] if len(axes) == 2 else None
    length_mm = axes[1] if len(axes) == 2 else None
    physical_area_mm2 = contact_area_mm2(contact, geometry)
    force_density = None
    if physical_area_mm2 > 0.0:
        force_density = contact_force_grams(contact, geometry) / physical_area_mm2
    return ContactShapeFeatures(
        width_mm=width_mm,
        length_mm=length_mm,
        force_density_g_mm2=force_density,
    )


def finger_shape_allowed(
    features: ContactShapeFeatures,
    max_width_mm: float,
    context: ContactFrameContext | None = None,
) -> bool:
    if max_width_mm == 0.0:
        return True
    if features.width_mm is None or features.length_mm is None:
        return True
    width_mm = features.width_mm
    if width_mm < DEFAULT_MIN_FINGER_WIDTH_MM or width_mm > max_width_mm:
        return False
    aspect_ratio = features.length_mm / width_mm
    if aspect_ratio > DEFAULT_ELONGATED_ASPECT_RATIO:
        density = features.force_density_g_mm2
        if density is None or density < DEFAULT_ELONGATED_MIN_FORCE_DENSITY:
            return False
    if context is not None and context.contact_count >= DEFAULT_CLUSTER_MIN_CONTACTS:
        nearest = context.nearest_contact_distance_mm
        if nearest is not None and nearest < DEFAULT_CLUSTER_DISTANCE_MM:
            return False
    return True


def contact_frame_contexts(contacts: Sequence[dp.DecodedContact]) -> dict[int, ContactFrameContext]:
    active = [contact for contact in contacts if contact.state != CONTACT_STATE_END]
    contexts: dict[int, ContactFrameContext] = {}
    for contact in active:
        distances = [
            math.hypot(contact.x_mm - other.x_mm, contact.y_mm - other.y_mm)
            for other in active
            if other is not contact
        ]
        contexts[contact.id] = ContactFrameContext(
            contact_count=len(active),
            nearest_contact_distance_mm=min(distances) if distances else None,
        )
    return contexts


@dataclass
class TrackedTuioContact:
    morph_id: int
    session_id: int
    cursor_session_id: int | None = None
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0
    width: float = 0.0
    height: float = 0.0
    area: float = 0.0
    x_speed: float = 0.0
    y_speed: float = 0.0
    motion_speed: float = 0.0
    motion_accel: float = 0.0
    rotation_speed: float = 0.0
    rotation_accel: float = 0.0
    last_time_ms: int | None = None
    cursor_values: tuple[float | int, ...] = ()
    blob_values: tuple[float | int, ...] = ()
    last_cursor_values: tuple[float | int, ...] | None = None
    last_blob_values: tuple[float | int, ...] | None = None


@dataclass(frozen=True)
class TuioFrameUpdate:
    active: tuple[TrackedTuioContact, ...]
    changed_cursors: tuple[TrackedTuioContact, ...]
    changed_blobs: tuple[TrackedTuioContact, ...]
    cursor_lifecycle_changed: bool
    blob_lifecycle_changed: bool


class TuioContactTracker:
    """Track firmware contacts and calculate TUIO C++-compatible motion."""

    def __init__(
        self,
        geometry: DeviceGeometry | None = None,
        invert_x: bool = False,
        invert_y: bool = False,
        invert_angle: bool = False,
        min_contact_force: float = DEFAULT_MIN_CONTACT_FORCE,
        finger_size_mm: float = DEFAULT_FINGER_SIZE_MM,
    ) -> None:
        if float(min_contact_force) < 0.0:
            raise ValueError("min_contact_force must be nonnegative")
        if float(finger_size_mm) < 0.0:
            raise ValueError("finger_size_mm must be nonnegative")
        self.geometry = geometry or DeviceGeometry()
        self.invert_x = invert_x
        self.invert_y = invert_y
        self.invert_angle = invert_angle
        self.min_contact_force = float(min_contact_force)
        self.finger_size_mm = float(finger_size_mm)
        self.states: dict[int, TrackedTuioContact] = {}
        self.next_session_id = 0

    def allocate_session_id(self) -> int:
        value = self.next_session_id
        self.next_session_id = (self.next_session_id + 1) % (2**31)
        return int(value)

    def cursor_shape_allowed(self, contact: dp.DecodedContact, context: ContactFrameContext | None = None) -> bool:
        features = contact_shape_features(contact, self.geometry)
        return finger_shape_allowed(features, self.finger_size_mm, context=context)

    def update(self, contacts: Sequence[dp.DecodedContact], now_ms: int) -> TuioFrameUpdate:
        cursor_lifecycle_changed = False
        blob_lifecycle_changed = False

        valid_contacts: list[dp.DecodedContact] = []
        seen_ids: set[int] = set()
        for contact in contacts:
            if contact.state == CONTACT_STATE_INVALID or contact.id in seen_ids:
                continue
            seen_ids.add(contact.id)
            valid_contacts.append(contact)

        contexts = contact_frame_contexts(valid_contacts)
        present_ids = {contact.id for contact in valid_contacts}
        for morph_id in list(self.states):
            if morph_id not in present_ids:
                state = self.states[morph_id]
                cursor_lifecycle_changed = cursor_lifecycle_changed or state.cursor_session_id is not None
                del self.states[morph_id]
                blob_lifecycle_changed = True

        active: list[TrackedTuioContact] = []
        changed_cursors: list[TrackedTuioContact] = []
        changed_blobs: list[TrackedTuioContact] = []

        for contact in valid_contacts:
            state = self.states.get(contact.id)
            if contact.state == CONTACT_STATE_END:
                if state is not None:
                    cursor_lifecycle_changed = cursor_lifecycle_changed or state.cursor_session_id is not None
                    del self.states[contact.id]
                    blob_lifecycle_changed = True
                continue

            if state is None:
                if contact_force_grams(contact, self.geometry) < self.min_contact_force:
                    continue
                session_id = self.allocate_session_id()
                state = TrackedTuioContact(morph_id=contact.id, session_id=session_id)
                if self.cursor_shape_allowed(contact, contexts.get(contact.id)):
                    state.cursor_session_id = session_id
                    cursor_lifecycle_changed = True
                self.states[contact.id] = state
                blob_lifecycle_changed = True
            elif contact.state == CONTACT_STATE_START:
                had_cursor = state.cursor_session_id is not None
                session_id = self.allocate_session_id()
                state = TrackedTuioContact(morph_id=contact.id, session_id=session_id)
                if self.cursor_shape_allowed(contact, contexts.get(contact.id)):
                    state.cursor_session_id = session_id
                self.states[contact.id] = state
                cursor_lifecycle_changed = cursor_lifecycle_changed or had_cursor or state.cursor_session_id is not None
                blob_lifecycle_changed = True
            else:
                size_allowed = self.cursor_shape_allowed(contact, contexts.get(contact.id))
                if size_allowed and state.cursor_session_id is None:
                    state.cursor_session_id = self.allocate_session_id()
                    state.last_cursor_values = None
                    cursor_lifecycle_changed = True
                elif not size_allowed and state.cursor_session_id is not None:
                    state.cursor_session_id = None
                    state.last_cursor_values = None
                    cursor_lifecycle_changed = True

            self._update_state(state, contact, now_ms)
            active.append(state)

            if state.cursor_session_id is not None and (
                state.last_cursor_values != state.cursor_values or contact.state == CONTACT_STATE_START
            ):
                changed_cursors.append(state)
                state.last_cursor_values = state.cursor_values
            if state.last_blob_values != state.blob_values or contact.state == CONTACT_STATE_START:
                changed_blobs.append(state)
                state.last_blob_values = state.blob_values

        return TuioFrameUpdate(
            active=tuple(active),
            changed_cursors=tuple(changed_cursors),
            changed_blobs=tuple(changed_blobs),
            cursor_lifecycle_changed=cursor_lifecycle_changed,
            blob_lifecycle_changed=blob_lifecycle_changed,
        )

    def _update_state(self, state: TrackedTuioContact, contact: dp.DecodedContact, now_ms: int) -> None:
        geometry = self.geometry
        previous_x = state.x
        previous_y = state.y
        previous_angle = state.angle
        previous_motion_speed = state.motion_speed
        previous_rotation_speed = state.rotation_speed

        x = clamp(contact.x_mm / geometry.active_width_mm)
        y = clamp(contact.y_mm / geometry.active_height_mm)
        angle = math.radians(float(contact.orientation_deg or 0.0) + 90.0) % TAU
        width = clamp((contact.major_axis_mm or 0.0) / geometry.active_width_mm)
        height = clamp((contact.minor_axis_mm or 0.0) / geometry.active_height_mm)
        sensor_area = max(1.0, float(geometry.sensor_cols * geometry.sensor_rows))
        area = clamp((float(contact.area) / geometry.area_value_scale) / sensor_area)

        state.x = x
        state.y = y
        state.angle = angle
        state.width = width
        state.height = height
        state.area = area

        if state.last_time_ms is not None:
            dt = (int(now_ms) - state.last_time_ms) / 1000.0
            if dt > 0.0:
                dx = x - previous_x
                dy = y - previous_y
                state.x_speed = dx / dt
                state.y_speed = dy / dt
                state.motion_speed = math.sqrt(dx * dx + dy * dy) / dt
                state.motion_accel = (state.motion_speed - previous_motion_speed) / dt

                da = wrap_angle_delta(angle - previous_angle)
                state.rotation_speed = (da / TAU) / dt
                state.rotation_accel = (state.rotation_speed - previous_rotation_speed) / dt
        state.last_time_ms = int(now_ms)

        out_x = 1.0 - x if self.invert_x else x
        out_y = 1.0 - y if self.invert_y else y
        out_x_speed = -state.x_speed if self.invert_x else state.x_speed
        out_y_speed = -state.y_speed if self.invert_y else state.y_speed
        out_angle = (TAU - angle) % TAU if self.invert_angle else angle
        out_rotation_speed = -state.rotation_speed if self.invert_angle else state.rotation_speed

        state.cursor_values = (
            int(state.cursor_session_id if state.cursor_session_id is not None else state.session_id),
            float(out_x),
            float(out_y),
            float(out_x_speed),
            float(out_y_speed),
            float(state.motion_accel),
        )
        state.blob_values = (
            int(state.session_id),
            float(out_x),
            float(out_y),
            float(out_angle),
            float(state.width),
            float(state.height),
            float(state.area),
            float(out_x_speed),
            float(out_y_speed),
            float(out_rotation_speed),
            float(state.motion_accel),
            float(state.rotation_accel),
        )


def cursor_from_state(state: TrackedTuioContact) -> Cursor:
    session_id, x, y, x_speed, y_speed, motion_accel = state.cursor_values
    cursor = Cursor(int(session_id))
    cursor.position = (float(x), float(y))
    cursor.velocity = (float(x_speed), float(y_speed))
    cursor.motion_acceleration = float(motion_accel)
    return cursor


def blob_from_state(state: TrackedTuioContact) -> Blob:
    (
        session_id,
        x,
        y,
        angle,
        width,
        height,
        area,
        x_speed,
        y_speed,
        rotation_speed,
        motion_accel,
        rotation_accel,
    ) = state.blob_values
    blob = Blob(int(session_id))
    blob.position = (float(x), float(y))
    blob.angle = float(angle)
    blob.dimension = (float(width), float(height))
    blob.area = float(area)
    blob.velocity = (float(x_speed), float(y_speed))
    blob.velocity_rotation = float(rotation_speed)
    blob.motion_acceleration = float(motion_accel)
    blob.rotation_acceleration = float(rotation_accel)
    return blob


def decode_contact_frame(raw_frame: dict[str, object]) -> dp.DecodedContacts:
    payload_hex = raw_frame.get("payload_hex")
    if not isinstance(payload_hex, str):
        raise dp.DecodeError("missing payload_hex")
    payload = bytes.fromhex(payload_hex)
    if len(payload) < dp.FRAME_HEADER_SIZE:
        raise dp.DecodeError("truncated frame header")
    if not (payload[0] & FRAME_CONTENT_CONTACTS):
        raise dp.DecodeError(f"frame does not contain contacts: content=0x{payload[0]:02x}")
    return dp.parse_contacts(payload, dp.FRAME_HEADER_SIZE)


def _read_reg_or_default(fd: int, reg: int, size: int, default: bytes) -> bytes:
    try:
        return mc.read_reg(fd, reg, size)
    except Exception:
        return default


def read_device_geometry(fd: int) -> DeviceGeometry:
    cols_raw = _read_reg_or_default(fd, REG_SENSOR_NUM_COLS, 2, DEFAULT_SENSOR_COLS.to_bytes(2, "little"))
    rows_raw = _read_reg_or_default(fd, REG_SENSOR_NUM_ROWS, 2, DEFAULT_SENSOR_ROWS.to_bytes(2, "little"))
    width_raw = _read_reg_or_default(fd, REG_SENSOR_ACTIVE_AREA_WIDTH_UM, 4, int(ACTIVE_W_MM * 1000).to_bytes(4, "little"))
    height_raw = _read_reg_or_default(fd, REG_SENSOR_ACTIVE_AREA_HEIGHT_UM, 4, int(ACTIVE_H_MM * 1000).to_bytes(4, "little"))

    force_shift_raw = _read_reg_or_default(fd, REG_UNIT_SHIFT_FORCE, 1, b"\x03")
    area_shift_raw = _read_reg_or_default(fd, REG_UNIT_SHIFT_AREA, 1, b"\x00")
    force_shift = force_shift_raw[0] if force_shift_raw else 3
    shift_area = area_shift_raw[0] if area_shift_raw else 0

    sensor_cols = int.from_bytes(cols_raw[:2], "little") or DEFAULT_SENSOR_COLS
    sensor_rows = int.from_bytes(rows_raw[:2], "little") or DEFAULT_SENSOR_ROWS
    width_mm = (int.from_bytes(width_raw[:4], "little") or int(ACTIVE_W_MM * 1000)) / 1000.0
    height_mm = (int.from_bytes(height_raw[:4], "little") or int(ACTIVE_H_MM * 1000)) / 1000.0
    return DeviceGeometry(
        sensor_cols=sensor_cols,
        sensor_rows=sensor_rows,
        active_width_mm=width_mm,
        active_height_mm=height_mm,
        area_value_scale=float(1 << shift_area),
        force_value_scale=float(1 << force_shift),
    )


def contact_mask_for_args(args: argparse.Namespace) -> int:
    if args.blob or args.finger_size > 0.0:
        return CONTACT_MASK_ELLIPSE
    return CONTACT_MASK_NONE


class SenselMorphTuio:
    """Live Morph-to-TUIO transmitter."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.server = SenselMorphTuioServer(args.host, args.port)
        if not args.no_source:
            self.server.set_source_name(args.source)
        self.running = True
        self.tracker = TuioContactTracker(
            invert_x=args.invert_x,
            invert_y=args.invert_y,
            invert_angle=args.invert_angle,
            min_contact_force=args.min_contact_force,
            finger_size_mm=args.finger_size,
        )
        self.last_send_time = 0.0
        self.last_cursor_refresh = 0.0
        self.last_blob_refresh = 0.0

    def stop(self, *_args: object) -> None:
        self.running = False

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        device = self.args.device or mc.find_device()
        fd = mc.open_serial(device)
        initial: dict[str, str] = {}
        try:
            initial = {
                "scan_detail": mc.read_reg(fd, mc.REG_SCAN_DETAIL, 1).hex(),
                "frame_content": mc.read_reg(fd, mc.REG_FRAME_CONTENT, 1).hex(),
                "scan_enabled": mc.read_reg(fd, mc.REG_SCAN_ENABLED, 1).hex(),
                "contacts_mask": mc.read_reg(fd, REG_CONTACTS_MASK, 1).hex(),
            }
            geometry = read_device_geometry(fd)
            self.tracker.geometry = geometry

            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0)
            mc.write_reg(fd, mc.REG_SCAN_DETAIL, SCAN_DETAILS[self.args.scan_detail])
            mc.write_reg(fd, mc.REG_FRAME_CONTENT, FRAME_CONTENT_CONTACTS)
            mc.write_reg(fd, REG_CONTACTS_MASK, contact_mask_for_args(self.args))

            self.server.send_empty_bundles(include_blob=self.args.blob)
            if not self.args.quiet:
                profiles = "2Dcur,2Dblb" if self.args.blob else "2Dcur"
                print(
                    f"sensel_morph_tuio device={device} host={self.args.host} port={self.args.port} "
                    f"profiles={profiles} mode={self.args.update_mode} scan_detail={self.args.scan_detail} "
                    f"sensor={geometry.sensor_cols}x{geometry.sensor_rows} area={geometry.active_width_mm:g}x{geometry.active_height_mm:g}mm "
                    f"min_force={self.args.min_contact_force:g}g finger_size={self.args.finger_size:g}mm",
                    file=sys.stderr,
                )

            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 1)
            return self.loop(fd)
        finally:
            try:
                self.server.send_empty_bundles(include_blob=self.args.blob)
            except Exception as exc:
                print(f"warning: failed to send shutdown TUIO state: {exc}", file=sys.stderr)
            self.restore_device(fd, initial)

    def loop(self, fd: int) -> int:
        frame_count = 0
        start = time.monotonic()
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
                contacts = decode_contact_frame(raw_frame)
            except Exception as exc:
                if not self.args.quiet:
                    print(f"frame error: {exc}", file=sys.stderr)
                time.sleep(0.005)
                continue

            now_ms = monotonic_ms()
            update = self.tracker.update(contacts.contacts, now_ms)
            self.send_update(update)
            self.last_send_time = time.monotonic()
            frame_count += 1

        elapsed = max(0.001, time.monotonic() - start)
        if not self.args.quiet:
            print(f"sent updates for {frame_count} frames in {elapsed:.2f}s ({frame_count / elapsed:.1f} fps)", file=sys.stderr)
        return 0

    def restore_device(self, fd: int, initial: dict[str, str]) -> None:
        try:
            mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0)
        except Exception as exc:
            print(f"warning: failed to stop scanning: {exc}", file=sys.stderr)
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
            try:
                mc.write_reg(fd, mc.REG_SCAN_ENABLED, int(initial["scan_enabled"], 16))
            except Exception as exc:
                print(f"warning: failed to restore scan_enabled: {exc}", file=sys.stderr)
        os.close(fd)

    def refresh_due(self, last_refresh: float, now: float) -> bool:
        return now - last_refresh >= self.args.refresh_interval

    def select_profile_states(
        self,
        active: tuple[TrackedTuioContact, ...],
        changed: tuple[TrackedTuioContact, ...],
        lifecycle_changed: bool,
        last_refresh: float,
        now: float,
    ) -> tuple[bool, tuple[TrackedTuioContact, ...], bool]:
        if self.args.update_mode == "full":
            return True, active, False
        if self.refresh_due(last_refresh, now):
            return True, active, True
        return bool(changed or lifecycle_changed), changed, False

    def send_update(self, update: TuioFrameUpdate) -> int:
        now = time.monotonic()
        active_cursor_states = tuple(state for state in update.active if state.cursor_session_id is not None)
        cursor_due, cursor_states, cursor_refreshed = self.select_profile_states(
            active_cursor_states,
            update.changed_cursors,
            update.cursor_lifecycle_changed,
            self.last_cursor_refresh,
            now,
        )
        blob_due = False
        blob_states: tuple[TrackedTuioContact, ...] = ()
        blob_refreshed = False
        if self.args.blob:
            blob_due, blob_states, blob_refreshed = self.select_profile_states(
                update.active,
                update.changed_blobs,
                update.blob_lifecycle_changed,
                self.last_blob_refresh,
                now,
            )

        if not cursor_due and not blob_due:
            return 0

        fseq = self.server.next_fseq()
        sent = 0
        active_cursors = [cursor_from_state(state) for state in active_cursor_states]
        if cursor_due:
            self.server.send_profile_frame(
                TUIO_CURSOR,
                active_cursors,
                [cursor_from_state(state) for state in cursor_states],
                fseq,
            )
            sent += 1

        if self.args.blob:
            active_blobs = [blob_from_state(state) for state in update.active]
            if blob_due:
                self.server.send_profile_frame(
                    TUIO_BLOB,
                    active_blobs,
                    [blob_from_state(state) for state in blob_states],
                    fseq,
                )
                sent += 1

        if cursor_refreshed:
            self.last_cursor_refresh = now
        if blob_refreshed:
            self.last_blob_refresh = now
        return sent


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Broadcast live Sensel Morph contacts as TUIO 1.1 /tuio/2Dcur and optional /tuio/2Dblb bundles.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="TUIO destination host.")
    parser.add_argument("--port", type=int, default=DEFAULT_TUIO_PORT, help="TUIO destination UDP port. Default 3333.")
    parser.add_argument("--device", default=None, help="Serial device path. Defaults to first /dev/cu.usbmodem*.")
    parser.add_argument("--blob", action="store_true", help="Also send /tuio/2Dblb using firmware ellipse geometry.")
    parser.add_argument(
        "--update-mode",
        choices=("full", "changed"),
        default="full",
        help="full sends every active profile each frame; changed sends only changed profiles plus periodic refreshes.",
    )
    parser.add_argument("--refresh-interval", type=float, default=1.0, help="Periodic full-state refresh interval in seconds.")
    parser.add_argument(
        "--min-contact-force",
        type=float,
        default=DEFAULT_MIN_CONTACT_FORCE,
        help="Minimum firmware contact force in grams required to activate a new TUIO contact. Default %(default)g; 0 disables force filtering.",
    )
    parser.add_argument(
        "--finger-size",
        type=float,
        default=DEFAULT_FINGER_SIZE_MM,
        help="Maximum contact ellipse width/minor axis in millimeters considered finger-like; wider contacts are omitted from cursor output. Default %(default)g; 0 disables shape/context filtering.",
    )
    parser.add_argument("--scan-detail", choices=sorted(SCAN_DETAILS), default=DEFAULT_SCAN_DETAIL)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--source", default="sensel_morph", help="TUIO source string.")
    source_group.add_argument("--no-source", action="store_true", help="Omit optional TUIO source messages.")
    parser.add_argument("--invert-x", action="store_true", help="Invert TUIO x positions and x velocity.")
    parser.add_argument("--invert-y", action="store_true", help="Invert TUIO y positions and y velocity.")
    parser.add_argument("--invert-angle", action="store_true", help="Invert TUIO blob angle and rotation direction.")
    parser.add_argument("--fps-limit", type=float, default=0.0, help="Limit processed frame rate; 0 means unbounded.")
    parser.add_argument("--max-frames", type=int, default=None, help="Stop after this many decoded frames.")
    parser.add_argument("--read-timeout", type=float, default=1.0)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.refresh_interval <= 0:
        parser.error("--refresh-interval must be positive")
    if args.min_contact_force < 0:
        parser.error("--min-contact-force must be nonnegative")
    if args.finger_size < 0:
        parser.error("--finger-size must be nonnegative")
    return SenselMorphTuio(args).run()


if __name__ == "__main__":
    raise SystemExit(main())

