#!/usr/bin/env python3
"""Decode Sensel Morph compressed pressure payloads captured over CDC serial.

This implements the simple low-resolution pressure RLE recovered from
LibSenselDecompress.dll. It intentionally stops at the pre-interpolation grid;
that is the directly compressed data stream and is the best target for protocol
validation.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

import numpy as np
from PIL import Image


FRAME_HEADER_SIZE = 6
KNOWN_GRIDS = {
    "medium": (47, 27, 4, 4),
    "high": (93, 53, 2, 2),
}
LABEL_PALETTE = [
    (255, 0, 0),
    (0, 255, 0),
    (0, 96, 255),
    (255, 255, 0),
    (0, 255, 255),
    (255, 0, 255),
    (255, 128, 0),
    (128, 255, 0),
    (0, 128, 255),
    (255, 0, 128),
    (128, 255, 255),
    (255, 128, 255),
    (192, 192, 192),
    (255, 255, 255),
    (128, 128, 128),
    (128, 0, 0),
]


class DecodeError(Exception):
    """Raised when a pressure stream does not exactly fill a candidate grid."""


@dataclass(frozen=True)
class Grid:
    name: str
    cols: int
    rows: int
    x_scale: int
    y_scale: int

    @property
    def cells(self) -> int:
        return self.cols * self.rows


@dataclass
class DecodedFrame:
    grid: Grid
    base: int
    values: list[int]
    bytes_used: int
    varints: int
    zero_runs: int
    nonzero_values: int


@dataclass
class DecodedLabels:
    grid: Grid
    values: list[int]
    bytes_used: int


@dataclass
class DecodedContact:
    id: int
    state: int
    x_mm: float
    y_mm: float
    force: float
    area: float
    orientation_deg: float | None = None
    major_axis_mm: float | None = None
    minor_axis_mm: float | None = None
    delta_x_mm: float | None = None
    delta_y_mm: float | None = None
    delta_force: float | None = None
    delta_area: float | None = None
    min_x_mm: float | None = None
    min_y_mm: float | None = None
    max_x_mm: float | None = None
    max_y_mm: float | None = None
    peak_x_mm: float | None = None
    peak_y_mm: float | None = None
    peak_force: float | None = None


@dataclass
class DecodedContacts:
    contacts: list[DecodedContact]
    bytes_used: int
    contact_mask: int


@dataclass
class FrameSections:
    content_mask: int
    pressure_label_body: bytes
    contacts: DecodedContacts | None = None


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read the Morph's one- or two-byte unsigned varint."""
    if pos >= len(data):
        raise DecodeError("unexpected end of stream while reading varint")
    first = data[pos]
    pos += 1
    if first & 0x80:
        if pos >= len(data):
            raise DecodeError("truncated two-byte varint")
        second = data[pos]
        pos += 1
        return ((second << 7) | (first & 0x7F)), pos
    return first, pos


def decode_body(data: bytes, grid: Grid, require_all: bool = True) -> DecodedFrame:
    """Decode one pressure stream into the compressed source grid.

    The stream alternates between runs of zero pressure and nonzero deltas from
    a per-frame base value. Interpolation to 185x105 happens later.
    """
    pos = 0
    base, pos = read_varint(data, pos)
    values: list[int] = []
    zero_mode = True
    varints = 1
    zero_runs = 0
    nonzero_values = 0

    while len(values) < grid.cells:
        code, pos = read_varint(data, pos)
        varints += 1

        if zero_mode:
            if len(values) + code > grid.cells:
                raise DecodeError(
                    f"zero run overflows grid: emitted={len(values)} run={code} cells={grid.cells}"
                )
            values.extend([0] * code)
            zero_runs += 1
            zero_mode = False
            continue

        if code == 0:
            zero_mode = True
            continue

        values.append(base + code)
        nonzero_values += 1

    if require_all and pos != len(data):
        raise DecodeError(f"trailing bytes: used={pos} total={len(data)}")

    return DecodedFrame(
        grid=grid,
        base=base,
        values=values,
        bytes_used=pos,
        varints=varints,
        zero_runs=zero_runs,
        nonzero_values=nonzero_values,
    )


def decode_label_body(data: bytes, grid: Grid) -> DecodedLabels:
    """Decode label IDs from the alternating null/non-null run stream."""
    pos = 0
    values: list[int] = []
    null_run = True

    while len(values) < grid.cells:
        run_length, pos = read_varint(data, pos)
        if null_run:
            label = 255
            null_run = False
        else:
            if pos >= len(data):
                raise DecodeError("truncated label byte")
            encoded = data[pos]
            pos += 1
            label = encoded & 0x7F
            null_run = encoded < 0x80

        if len(values) + run_length > grid.cells:
            raise DecodeError(
                f"label run overflows grid: emitted={len(values)} run={run_length} cells={grid.cells}"
            )
        values.extend([label] * run_length)

    if pos != len(data):
        raise DecodeError(f"trailing label bytes: used={pos} total={len(data)}")

    return DecodedLabels(grid=grid, values=values, bytes_used=pos)


def grids_for_record(record: dict[str, object]) -> list[Grid]:
    """Return candidate source grids, preferring device metadata if present."""
    grids = [Grid(name, *params) for name, params in KNOWN_GRIDS.items()]
    meta = record.get("compression_metadata")
    if isinstance(meta, dict):
        raw_hex = meta.get("data_hex")
        if isinstance(raw_hex, str):
            raw = bytes.fromhex(raw_hex)
            if len(raw) >= 6 and raw[2] and raw[3]:
                meta_grid = Grid("metadata", raw[2], raw[3], raw[4], raw[5])
                grids = [
                    meta_grid,
                    *[g for g in grids if (g.cols, g.rows) != (meta_grid.cols, meta_grid.rows)],
                ]
    return grids


def u16_le(data: bytes, pos: int) -> int:
    return data[pos] | (data[pos + 1] << 8)


def i16_le(data: bytes, pos: int) -> int:
    value = u16_le(data, pos)
    return value - 0x10000 if value >= 0x8000 else value


def parse_contacts(data: bytes, pos: int) -> DecodedContacts:
    """Parse one firmware contact block using its per-packet contact mask."""
    if pos + 2 > len(data):
        raise DecodeError("truncated contact header")

    contact_mask = data[pos]
    n_contacts = data[pos + 1]
    pos += 2
    if n_contacts > 16:
        raise DecodeError(f"too many contacts: {n_contacts}")

    bytes_per_contact = 10
    if contact_mask & 0x01:
        bytes_per_contact += 6
    if contact_mask & 0x02:
        bytes_per_contact += 8
    if contact_mask & 0x04:
        bytes_per_contact += 8
    if contact_mask & 0x08:
        bytes_per_contact += 6
    if pos + bytes_per_contact * n_contacts > len(data):
        raise DecodeError("truncated contact data")

    start = pos - 2
    contacts: list[DecodedContact] = []
    for _ in range(n_contacts):
        contact = DecodedContact(
            id=data[pos],
            state=data[pos + 1],
            x_mm=u16_le(data, pos + 2) / 256.0,
            y_mm=u16_le(data, pos + 4) / 256.0,
            force=u16_le(data, pos + 6) / 8.0,
            area=float(u16_le(data, pos + 8)),
        )
        pos += 10

        if contact_mask & 0x01:
            contact.orientation_deg = i16_le(data, pos) / 16.0
            contact.major_axis_mm = u16_le(data, pos + 2) / 256.0
            contact.minor_axis_mm = u16_le(data, pos + 4) / 256.0
            pos += 6
        if contact_mask & 0x02:
            contact.delta_x_mm = i16_le(data, pos) / 256.0
            contact.delta_y_mm = i16_le(data, pos + 2) / 256.0
            contact.delta_force = i16_le(data, pos + 4) / 8.0
            contact.delta_area = float(i16_le(data, pos + 6))
            pos += 8
        if contact_mask & 0x04:
            contact.min_x_mm = u16_le(data, pos) / 256.0
            contact.min_y_mm = u16_le(data, pos + 2) / 256.0
            contact.max_x_mm = u16_le(data, pos + 4) / 256.0
            contact.max_y_mm = u16_le(data, pos + 6) / 256.0
            pos += 8
        if contact_mask & 0x08:
            contact.peak_x_mm = float(u16_le(data, pos))
            contact.peak_y_mm = float(u16_le(data, pos + 2))
            contact.peak_force = u16_le(data, pos + 4) / 8.0
            pos += 6

        contacts.append(contact)

    return DecodedContacts(contacts=contacts, bytes_used=pos - start, contact_mask=contact_mask)


def frame_sections(frame: dict[str, object]) -> FrameSections | None:
    """Split a raw captured frame into contact and pressure/label sections."""
    payload_hex = frame.get("payload_hex")
    if not isinstance(payload_hex, str):
        return None
    payload = bytes.fromhex(payload_hex)
    if len(payload) < FRAME_HEADER_SIZE:
        return None
    content_mask = payload[0]
    pos = FRAME_HEADER_SIZE
    contacts = None

    if content_mask & 0x04:
        contacts = parse_contacts(payload, pos)
        pos += contacts.bytes_used

    if content_mask & 0x08:
        if pos + 6 > len(payload):
            raise DecodeError("truncated accelerometer section")
        pos += 6

    if not (content_mask & 0x03) and contacts is None:
        return None
    return FrameSections(content_mask=content_mask, pressure_label_body=payload[pos:], contacts=contacts)


def frame_content_and_body(frame: dict[str, object]) -> tuple[int, bytes] | None:
    """Return the content mask and compressed body when pressure is present."""
    sections = frame_sections(frame)
    if sections is None:
        return None
    if not (sections.content_mask & 0x01):
        return None
    return sections.content_mask, sections.pressure_label_body


def frame_pressure_body(frame: dict[str, object]) -> bytes | None:
    """Return a pressure-only frame body, rejecting mixed-content frames."""
    result = frame_content_and_body(frame)
    if result is None:
        return None
    content_mask, body = result
    if content_mask != 0x01:
        return None
    return body


def metrics(decoded: DecodedFrame, force_scale: float = 1.0) -> dict[str, float | int | None]:
    """Summarize a decoded pressure frame for CLI tables and CSV output."""
    values = [value / force_scale for value in decoded.values]
    total = sum(values)
    max_value = max(values) if values else 0
    if total:
        sx = 0
        sy = 0
        for idx, value in enumerate(values):
            if value:
                y, x = divmod(idx, decoded.grid.cols)
                sx += x * value
                sy += y * value
        cx: float | None = sx / total
        cy: float | None = sy / total
    else:
        cx = None
        cy = None
    return {
        "sum": total,
        "max": max_value,
        "nonzero": decoded.nonzero_values,
        "centroid_x": cx,
        "centroid_y": cy,
        "base": decoded.base,
        "zero_runs": decoded.zero_runs,
        "varints": decoded.varints,
    }


def infer_frame(
    body: bytes,
    grids: Iterable[Grid],
    require_all: bool = True,
) -> tuple[DecodedFrame | None, dict[str, str]]:
    """Try all plausible source grids and return the first exact decode."""
    errors = {}
    for grid in grids:
        try:
            return decode_body(body, grid, require_all=require_all), errors
        except DecodeError as exc:
            errors[grid.name] = str(exc)
    return None, errors


def write_pgm(path: pathlib.Path, decoded: DecodedFrame) -> None:
    write_values_pgm(path, decoded.values, decoded.grid.cols, decoded.grid.rows)


def write_values_pgm(path: pathlib.Path, values: Sequence[float], width: int, height: int) -> None:
    max_value = max(values) if values else 0
    scale = 255.0 / max_value if max_value else 0.0
    pixels = bytes(min(255, max(0, int(round(value * scale)))) for value in values)
    header = f"P5\n{width} {height}\n255\n".encode("ascii")
    path.write_bytes(header + pixels)


def write_labels_ppm(path: pathlib.Path, labels: DecodedLabels) -> None:
    pixels = bytearray()
    for label in labels.values:
        if label == 255:
            pixels.extend((0, 0, 0))
        else:
            pixels.extend(LABEL_PALETTE[label % len(LABEL_PALETTE)])
    header = f"P6\n{labels.grid.cols} {labels.grid.rows}\n255\n".encode("ascii")
    path.write_bytes(header + bytes(pixels))


def pressure_image(values: Sequence[float], width: int, height: int) -> Image.Image:
    max_value = max(values) if values else 0
    scale = 255.0 / max_value if max_value else 0.0
    pixels = bytes(min(255, max(0, int(round(value * scale)))) for value in values)
    return Image.frombytes("L", (width, height), pixels)


def labels_image(labels: DecodedLabels) -> Image.Image:
    pixels = bytearray()
    for label in labels.values:
        if label == 255:
            pixels.extend((0, 0, 0))
        else:
            pixels.extend(LABEL_PALETTE[label % len(LABEL_PALETTE)])
    return Image.frombytes("RGB", (labels.grid.cols, labels.grid.rows), bytes(pixels))


def nearest_resize(image: Image.Image, width: int, height: int) -> Image.Image:
    return image.resize((width, height), Image.Resampling.NEAREST)


def overlay_image(pressure: Image.Image, labels: Image.Image, alpha: float = 0.55) -> Image.Image:
    pressure_rgb = pressure.convert("RGB")
    labels_rgb = labels.convert("RGB")
    out = bytearray()
    pressure_bytes = pressure_rgb.tobytes()
    label_bytes = labels_rgb.tobytes()
    for index in range(0, len(pressure_bytes), 3):
        gray = pressure_bytes[index : index + 3]
        color = label_bytes[index : index + 3]
        if color == b"\x00\x00\x00":
            out.extend(gray)
        else:
            out.extend(
                (
                    int(gray[0] * (1.0 - alpha) + color[0] * alpha),
                    int(gray[1] * (1.0 - alpha) + color[1] * alpha),
                    int(gray[2] * (1.0 - alpha) + color[2] * alpha),
                )
            )
    return Image.frombytes("RGB", pressure_rgb.size, bytes(out))


def contact_image(contacts: DecodedContacts, width: int = 185, height: int = 105) -> Image.Image:
    from PIL import ImageDraw
    import math

    active_width_mm = 230.0
    active_height_mm = 130.0
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    for contact in contacts.contacts:
        color = LABEL_PALETTE[contact.id % len(LABEL_PALETTE)]
        cx = contact.x_mm / active_width_mm * width
        cy = contact.y_mm / active_height_mm * height

        if contact.major_axis_mm is not None and contact.minor_axis_mm is not None:
            # Sensel's examples rotate by orientation, then draw minor on local X and major on local Y.
            w = max(2.0, contact.minor_axis_mm / active_width_mm * width)
            h = max(2.0, contact.major_axis_mm / active_height_mm * height)
        else:
            radius = max(1.0, (max(contact.area, 1.0) / 3.14159) ** 0.5)
            w = h = radius * 2.0

        angle = math.radians(contact.orientation_deg or 0.0)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        points = []
        for step in range(65):
            theta = math.tau * step / 64.0
            x = math.cos(theta) * w / 2.0
            y = math.sin(theta) * h / 2.0
            points.append((cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a))
        draw.line(points, fill=(*color, 255), width=1, joint="curve")
        draw.text((cx + 2, cy + 2), str(contact.id), fill=(255, 255, 255, 255))

    return image.convert("RGB")


def write_png_previews(
    capture_dir: pathlib.Path,
    index: int,
    decoded: DecodedFrame,
    labels: DecodedLabels | None,
    contacts: DecodedContacts | None,
    force_scale: float,
) -> None:
    """Write pressure, label, overlay, and firmware-contact PNG previews."""
    expanded, width, height = expand_pressure_scaled(decoded, force_scale=force_scale)
    pressure = pressure_image(expanded, width, height)
    pressure_view = nearest_resize(pressure, width * 5, height * 5)
    pressure_view.save(capture_dir / f"frame_{index:04d}_pressure.png")

    if labels is None:
        return

    label_low = labels_image(labels)
    label_expanded = nearest_resize(label_low, width, height)
    label_view = nearest_resize(label_expanded, width * 5, height * 5)
    label_view.save(capture_dir / f"frame_{index:04d}_labels.png")

    overlay = overlay_image(pressure, label_expanded)
    overlay_view = nearest_resize(overlay, width * 5, height * 5)
    overlay_view.save(capture_dir / f"frame_{index:04d}_overlay.png")

    if contacts is not None:
        contacts_view = contact_image(contacts, width * 5, height * 5)
        contacts_view.save(capture_dir / f"frame_{index:04d}_contacts.png")


@lru_cache(maxsize=None)
def sensel_kernel_weight(center: float, t: float) -> float:
    """Approximate the DLL's 0.001-step normalized kernel integral."""

    numerator = 0.0
    denominator = 0.0
    for i in range(3001):
        sample = -1.0 + i * 0.001

        distance = abs(center - sample)
        basis = 0.0 if distance >= 1.0 else 1.0 - distance

        delta2 = (t - sample) * (t - sample)
        kernel = 0.0 if delta2 >= 1.0 else (1.0 - delta2) * (1.0 - delta2)

        numerator += kernel * basis
        denominator += kernel

    return numerator / denominator if denominator else 0.0


@lru_cache(maxsize=None)
def interpolation_weights(scale: int) -> tuple[tuple[float, float, float, float], ...]:
    """Return the recovered four-tap Sensel interpolation weights."""
    if scale <= 0:
        raise ValueError(f"invalid interpolation scale {scale}")

    weights = []
    for step in range(scale + 1):
        t = step / scale
        raw = [
            sensel_kernel_weight(-1.0, t),
            sensel_kernel_weight(0.0, t),
            sensel_kernel_weight(1.0, t),
            sensel_kernel_weight(2.0, t),
        ]
        total = sum(raw)
        weights.append(tuple(value / total for value in raw))
    return tuple(weights)


def expand_pressure(decoded: DecodedFrame) -> tuple[list[float], int, int]:
    return expand_pressure_scaled(decoded, force_scale=1.0)


def expand_pressure_scaled(decoded: DecodedFrame, force_scale: float = 1.0) -> tuple[list[float], int, int]:
    return expand_pressure_scaled_numpy(decoded, force_scale)


@lru_cache(maxsize=None)
def interpolation_plan(cols: int, rows: int, x_scale: int, y_scale: int) -> tuple[np.ndarray, np.ndarray]:
    """Build cached separable interpolation matrices for a compressed grid."""
    out_cols = (cols - 1) * x_scale + 1
    out_rows = (rows - 1) * y_scale + 1
    x_weights = interpolation_weights(x_scale)
    y_weights = interpolation_weights(y_scale)

    x_matrix = np.zeros((out_cols, cols), dtype=np.float64)
    for x_segment in range(cols - 1):
        x_count = x_scale + 1 if x_segment == cols - 2 else x_scale
        for x_step in range(x_count):
            out_x = x_segment * x_scale + x_step
            for kx, wx_value in enumerate(x_weights[x_step]):
                src_x = x_segment + kx - 1
                if wx_value and 0 <= src_x < cols:
                    x_matrix[out_x, src_x] = wx_value

    y_matrix = np.zeros((out_rows, rows), dtype=np.float64)
    for y_segment in range(rows - 1):
        y_count = y_scale + 1 if y_segment == rows - 2 else y_scale
        for y_step in range(y_count):
            out_y = y_segment * y_scale + y_step
            for ky, wy_value in enumerate(y_weights[y_step]):
                src_y = y_segment + ky - 1
                if wy_value and 0 <= src_y < rows:
                    y_matrix[out_y, src_y] = wy_value

    return x_matrix, y_matrix


def expand_pressure_scaled_numpy(decoded: DecodedFrame, force_scale: float = 1.0) -> tuple[list[float], int, int]:
    """Expand pressure with the same separable SDK kernel using NumPy."""

    grid = decoded.grid
    x_matrix, y_matrix = interpolation_plan(grid.cols, grid.rows, grid.x_scale, grid.y_scale)
    source = np.asarray(decoded.values, dtype=np.float64).reshape((grid.rows, grid.cols))
    if force_scale != 1.0:
        source = source / force_scale
    expanded = y_matrix @ source @ x_matrix.T
    return expanded.ravel().tolist(), int(expanded.shape[1]), int(expanded.shape[0])


def expand_pressure_scaled_python(decoded: DecodedFrame, force_scale: float = 1.0) -> tuple[list[float], int, int]:
    """Expand a low-resolution pressure grid to the SDK force-array dimensions."""

    grid = decoded.grid
    out_cols = (grid.cols - 1) * grid.x_scale + 1
    out_rows = (grid.rows - 1) * grid.y_scale + 1
    x_weights = interpolation_weights(grid.x_scale)
    y_weights = interpolation_weights(grid.y_scale)
    scaled = [value / force_scale for value in decoded.values]

    horizontal = [[0.0] * out_cols for _ in range(grid.rows)]
    for src_y in range(grid.rows):
        src_row = src_y * grid.cols
        out_row = horizontal[src_y]
        for x_segment in range(grid.cols - 1):
            x_count = grid.x_scale + 1 if x_segment == grid.cols - 2 else grid.x_scale
            for x_step in range(x_count):
                out_x = x_segment * grid.x_scale + x_step
                value = 0.0
                for kx, wx_value in enumerate(x_weights[x_step]):
                    src_x = x_segment + kx - 1
                    if wx_value and 0 <= src_x < grid.cols:
                        value += scaled[src_row + src_x] * wx_value
                out_row[out_x] = value

    out = [0.0] * (out_cols * out_rows)
    for y_segment in range(grid.rows - 1):
        y_count = grid.y_scale + 1 if y_segment == grid.rows - 2 else grid.y_scale
        for y_step in range(y_count):
            out_y = y_segment * grid.y_scale + y_step
            wy = y_weights[y_step]
            out_base = out_y * out_cols
            for out_x in range(out_cols):
                value = 0.0
                for ky, wy_value in enumerate(wy):
                    src_y = y_segment + ky - 1
                    if wy_value and 0 <= src_y < grid.rows:
                        value += horizontal[src_y][out_x] * wy_value
                out[out_base + out_x] = value

    return out, out_cols, out_rows


def choose_preview_indices(decoded: list[tuple[int, DecodedFrame, dict[str, float | int | None]]]) -> list[int]:
    if not decoded:
        return []
    nonzero = [item for item in decoded if item[2]["sum"]]
    if not nonzero:
        return [decoded[0][0]]
    by_sum = sorted(nonzero, key=lambda item: int(item[2]["sum"] or 0))
    indices = {
        nonzero[0][0],
        by_sum[len(by_sum) // 2][0],
        by_sum[-1][0],
    }
    return sorted(indices)


def analyze_file(
    path: pathlib.Path,
    out_dir: pathlib.Path | None,
    expanded_dir: pathlib.Path | None,
    label_dir: pathlib.Path | None,
    png_dir: pathlib.Path | None,
    force_scale: float,
    csv_rows: list[dict[str, object]],
) -> dict[str, object]:
    record = json.loads(path.read_text())
    frames = record.get("frames", [])
    if (not frames or not isinstance(frames, list)) and isinstance(record.get("frame"), dict):
        frames = [record["frame"]]
    if not isinstance(frames, list):
        frames = []

    grids = grids_for_record(record)
    decoded_frames: list[tuple[int, DecodedFrame, dict[str, float | int | None]]] = []
    failures = 0
    skipped = 0
    first_errors: dict[str, str] = {}
    grid_counts: dict[str, int] = {}
    decoded_labels: dict[int, DecodedLabels] = {}
    decoded_contacts: dict[int, DecodedContacts] = {}
    contact_frames = 0
    contact_count_max = 0

    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            skipped += 1
            continue
        try:
            sections = frame_sections(frame)
        except DecodeError as exc:
            failures += 1
            if not first_errors:
                first_errors = {"sections": str(exc)}
            continue
        if sections is None:
            skipped += 1
            continue
        if sections.contacts is not None:
            contact_frames += 1
            contact_count_max = max(contact_count_max, len(sections.contacts.contacts))
            decoded_contacts[index] = sections.contacts

        if not (sections.content_mask & 0x01):
            skipped += 1
            continue

        content_mask = sections.content_mask
        body = sections.pressure_label_body
        has_labels = bool(content_mask & 0x02)
        decoded, errors = infer_frame(body, grids, require_all=not has_labels)
        if decoded is None:
            failures += 1
            if not first_errors:
                first_errors = errors
            continue
        labels = None
        if has_labels:
            try:
                labels = decode_label_body(body[decoded.bytes_used:], decoded.grid)
            except DecodeError as exc:
                failures += 1
                if not first_errors:
                    first_errors = {"labels": str(exc)}
                continue
            decoded_labels[index] = labels
        m = metrics(decoded, force_scale=force_scale)
        decoded_frames.append((index, decoded, m))
        grid_counts[decoded.grid.name] = grid_counts.get(decoded.grid.name, 0) + 1
        label_values = sorted(set(labels.values)) if labels else []
        csv_rows.append(
            {
                "capture": path.name,
                "label": record.get("label", ""),
                "frame_index": index,
                "grid": decoded.grid.name,
                "cols": decoded.grid.cols,
                "rows": decoded.grid.rows,
                "body_bytes": len(body),
                "has_contacts": sections.contacts is not None,
                "contact_count": len(sections.contacts.contacts) if sections.contacts else 0,
                "has_labels": bool(labels),
                "label_ids": " ".join(str(value) for value in label_values if value != 255),
                **m,
            }
        )

    summary: dict[str, object] = {
        "file": str(path),
        "label": record.get("label", ""),
        "frames": len(frames),
        "decoded": len(decoded_frames),
        "skipped": skipped,
        "failures": failures,
        "grid_counts": grid_counts,
        "label_frames": len(decoded_labels),
        "contact_frames": contact_frames,
        "contact_count_max": contact_count_max,
    }
    if first_errors:
        summary["first_errors"] = first_errors

    if decoded_frames:
        sums = [int(item[2]["sum"] or 0) for item in decoded_frames]
        nonzeros = [int(item[2]["nonzero"] or 0) for item in decoded_frames]
        maxes = [int(item[2]["max"] or 0) for item in decoded_frames]
        centroids = [
            (float(item[2]["centroid_x"]), float(item[2]["centroid_y"]))
            for item in decoded_frames
            if item[2]["centroid_x"] is not None and item[2]["centroid_y"] is not None
        ]
        summary.update(
            {
                "sum_min": min(sums),
                "sum_max": max(sums),
                "nonzero_min": min(nonzeros),
                "nonzero_max": max(nonzeros),
                "max_value_max": max(maxes),
            }
        )
        if centroids:
            summary.update(
                {
                    "centroid_x_min": min(x for x, _ in centroids),
                    "centroid_x_max": max(x for x, _ in centroids),
                    "centroid_y_min": min(y for _, y in centroids),
                    "centroid_y_max": max(y for _, y in centroids),
                }
            )

    if out_dir and decoded_frames:
        capture_dir = out_dir / path.stem
        capture_dir.mkdir(parents=True, exist_ok=True)
        for index in choose_preview_indices(decoded_frames):
            decoded = next(item[1] for item in decoded_frames if item[0] == index)
            write_pgm(capture_dir / f"frame_{index:04d}_{decoded.grid.name}.pgm", decoded)

    if expanded_dir and decoded_frames:
        capture_dir = expanded_dir / path.stem
        capture_dir.mkdir(parents=True, exist_ok=True)
        for index in choose_preview_indices(decoded_frames):
            decoded = next(item[1] for item in decoded_frames if item[0] == index)
            expanded, width, height = expand_pressure_scaled(decoded, force_scale=force_scale)
            write_values_pgm(
                capture_dir / f"frame_{index:04d}_{decoded.grid.name}_{width}x{height}.pgm",
                expanded,
                width,
                height,
            )

    if label_dir and decoded_labels:
        capture_dir = label_dir / path.stem
        capture_dir.mkdir(parents=True, exist_ok=True)
        for index in choose_preview_indices(decoded_frames):
            labels = decoded_labels.get(index)
            if labels:
                write_labels_ppm(capture_dir / f"frame_{index:04d}_{labels.grid.name}_labels.ppm", labels)

    if png_dir and decoded_frames:
        capture_dir = png_dir / path.stem
        capture_dir.mkdir(parents=True, exist_ok=True)
        for index in choose_preview_indices(decoded_frames):
            decoded = next(item[1] for item in decoded_frames if item[0] == index)
            write_png_previews(
                capture_dir,
                index,
                decoded,
                decoded_labels.get(index),
                decoded_contacts.get(index),
                force_scale=force_scale,
            )

    if png_dir and decoded_contacts and not decoded_frames:
        capture_dir = png_dir / path.stem
        capture_dir.mkdir(parents=True, exist_ok=True)
        contact_indices = sorted(decoded_contacts)
        preview_indices = sorted(
            {
                contact_indices[0],
                contact_indices[len(contact_indices) // 2],
                contact_indices[-1],
            }
        )
        for index in preview_indices:
            contacts_view = contact_image(decoded_contacts[index], 185 * 5, 105 * 5)
            contacts_view.save(capture_dir / f"frame_{index:04d}_contacts.png")

    return summary


def print_table(summaries: list[dict[str, object]]) -> None:
    columns = [
        ("file", 42),
        ("decoded", 7),
        ("contacts", 10),
        ("grid", 12),
        ("sum_range", 20),
        ("centroid_range", 28),
    ]
    print(" ".join(name.ljust(width) for name, width in columns))
    print(" ".join("-" * width for _, width in columns))
    for summary in summaries:
        grid_counts = summary.get("grid_counts", {})
        grid = ",".join(f"{name}:{count}" for name, count in grid_counts.items()) if isinstance(grid_counts, dict) else ""
        sum_range = ""
        if "sum_min" in summary:
            sum_range = f"{summary['sum_min']}..{summary['sum_max']}"
        centroid_range = ""
        if "centroid_x_min" in summary:
            centroid_range = (
                f"x {float(summary['centroid_x_min']):.1f}..{float(summary['centroid_x_max']):.1f} "
                f"y {float(summary['centroid_y_min']):.1f}..{float(summary['centroid_y_max']):.1f}"
            )
        row = {
            "file": pathlib.Path(str(summary["file"])).name,
            "decoded": f"{summary['decoded']}/{summary['frames']}",
            "contacts": f"{summary['contact_frames']}/{summary['frames']}"
            if int(summary.get("contact_frames", 0))
            else "",
            "grid": grid,
            "sum_range": sum_range,
            "centroid_range": centroid_range,
        }
        print(" ".join(str(row[name])[:width].ljust(width) for name, width in columns))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=pathlib.Path)
    parser.add_argument("--out-dir", type=pathlib.Path, default=None)
    parser.add_argument("--expanded-dir", type=pathlib.Path, default=None)
    parser.add_argument("--label-dir", type=pathlib.Path, default=None)
    parser.add_argument(
        "--png-dir",
        type=pathlib.Path,
        default=None,
        help="write viewable pressure/label/overlay PNG previews for representative frames",
    )
    parser.add_argument(
        "--force-scale",
        type=float,
        default=1.0,
        help="divide raw pressure values by this scale; use 8 for this Morph's SDK-style force units",
    )
    parser.add_argument("--csv", type=pathlib.Path, default=None)
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    paths: list[pathlib.Path] = []
    for capture in args.captures:
        if capture.is_dir():
            paths.extend(sorted(capture.glob("*.json")))
        else:
            paths.append(capture)

    csv_rows: list[dict[str, object]] = []
    summaries = [
        analyze_file(path, args.out_dir, args.expanded_dir, args.label_dir, args.png_dir, args.force_scale, csv_rows)
        for path in paths
    ]
    print_table(summaries)

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "capture",
            "label",
            "frame_index",
            "grid",
            "cols",
            "rows",
            "body_bytes",
            "has_contacts",
            "contact_count",
            "has_labels",
            "label_ids",
            "sum",
            "max",
            "nonzero",
            "centroid_x",
            "centroid_y",
            "base",
            "zero_runs",
            "varints",
        ]
        with args.csv.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summaries, indent=2))

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.expanded_dir:
        args.expanded_dir.mkdir(parents=True, exist_ok=True)
    if args.label_dir:
        args.label_dir.mkdir(parents=True, exist_ok=True)
    if args.png_dir:
        args.png_dir.mkdir(parents=True, exist_ok=True)

    return 0 if all(summary["failures"] == 0 for summary in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
