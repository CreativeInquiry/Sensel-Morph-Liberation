#!/usr/bin/env python3
"""Render high-resolution PNGs comparing firmware and fitted contact ellipses."""

from __future__ import annotations

import argparse
import math
import pathlib
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw

import decode_pressure as dp
import experiment_fit_ellipses as efe


SCALE = 10
WIDTH = 185
HEIGHT = 105
OUT_W = WIDTH * SCALE
OUT_H = HEIGHT * SCALE
FIT_AXIS_SCALE = 4.0
LABEL_ALPHA = 0.225


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render firmware-vs-fitted ellipse comparison PNGs.")
    parser.add_argument("capture", type=pathlib.Path, help="Full capture JSON file.")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--lag", type=int, default=1)
    parser.add_argument("--method", default="raw_pressure")
    parser.add_argument("--force-scale", type=float, default=1.0)
    parser.add_argument(
        "--out-dir",
        type=pathlib.Path,
        default=pathlib.Path("analysis/ellipse_fit_experiment/png_compare"),
    )
    args = parser.parse_args(argv)

    out_dir = args.out_dir / args.capture.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = efe.decode_surface_frames(load_json(args.capture))
    selected = select_frames(frames, args.count, args.lag, args.method, args.force_scale)
    if not selected:
        raise SystemExit("No comparable frames found.")

    for frame, source, fits in selected:
        image = render_frame(frame, source, fits, method=args.method, force_scale=args.force_scale)
        image.save(out_dir / f"frame_{frame.index:04d}_src_{source.index:04d}_{args.method}.png")

    print(f"wrote {len(selected)} PNGs to {out_dir}")
    return 0


def load_json(path: pathlib.Path) -> dict[str, object]:
    import json

    return json.loads(path.read_text())


def select_frames(
    frames: list[efe.DecodedSurfaceFrame],
    count: int,
    lag: int,
    method: str,
    force_scale: float,
) -> list[tuple[efe.DecodedSurfaceFrame, efe.DecodedSurfaceFrame, dict[int, efe.MomentFit]]]:
    """Choose evenly spaced frames with stable firmware/fitted ellipse pairs."""
    by_index = {frame.index: frame for frame in frames}
    config = next((item for item in efe.FIT_CONFIGS if item.name == method), None)
    if config is None:
        raise SystemExit(f"unknown method {method!r}")

    candidates: list[tuple[efe.DecodedSurfaceFrame, efe.DecodedSurfaceFrame, dict[int, efe.MomentFit]]] = []
    for frame in frames:
        source = by_index.get(frame.index - lag)
        if source is None or frame.contacts is None:
            continue
        contacts = efe.contacts_with_ellipse(frame.contacts.contacts)
        if not contacts:
            continue
        fits = efe.compute_fits(source, config, force_scale=force_scale)
        matched = [
            contact
            for contact in contacts
            if contact.id in fits
            and contact.state in (1, 2)
            and fits[contact.id].area_cells >= 10
        ]
        if matched:
            candidates.append((frame, source, fits))

    if len(candidates) <= count:
        return candidates
    indices = np.linspace(0, len(candidates) - 1, count)
    return [candidates[int(round(index))] for index in indices]


def render_frame(
    frame: efe.DecodedSurfaceFrame,
    source: efe.DecodedSurfaceFrame,
    fits: dict[int, efe.MomentFit],
    method: str,
    force_scale: float,
) -> Image.Image:
    """Render one side-by-side visual comparison onto a high-resolution PNG."""
    image = pressure_label_background(source, force_scale=force_scale)
    draw = ImageDraw.Draw(image, "RGBA")

    if frame.contacts is not None:
        for contact in efe.contacts_with_ellipse(frame.contacts.contacts):
            if contact.state not in (1, 2):
                continue
            fit = fits.get(contact.id)
            if fit is None:
                continue
            draw_contact_pair(draw, contact, fit)

    draw.rectangle((0, 0, 760, 82), fill=(0, 0, 0, 190))
    draw.text((14, 10), f"{frame.capture if hasattr(frame, 'capture') else ''}", fill=(255, 255, 255, 255))
    draw.text(
        (14, 30),
        f"contact frame {frame.index} / raster frame {source.index}   method={method}   fitted axis=4*sigma",
        fill=(255, 255, 255, 255),
    )
    draw.text((14, 52), "cyan=firmware ellipse   magenta=our pressure-moment fit", fill=(255, 255, 255, 255))
    return image


def pressure_label_background(frame: efe.DecodedSurfaceFrame, force_scale: float) -> Image.Image:
    values, width, height = dp.expand_pressure_scaled(frame.pressure, force_scale=force_scale)
    pressure = np.asarray(values, dtype=np.float64).reshape((height, width))
    max_value = float(pressure.max()) if pressure.size else 0.0
    gray = np.clip(np.rint(pressure * (255.0 / max_value if max_value else 0.0)), 0, 255).astype(np.uint8)
    rgb = np.repeat(gray[:, :, None], 3, axis=2)

    labels_low = np.asarray(frame.labels.values, dtype=np.uint8).reshape((frame.labels.grid.rows, frame.labels.grid.cols))
    y_indices = np.minimum(frame.labels.grid.rows - 1, ((np.arange(height) + 0.5) * frame.labels.grid.rows / height).astype(np.intp))
    x_indices = np.minimum(frame.labels.grid.cols - 1, ((np.arange(width) + 0.5) * frame.labels.grid.cols / width).astype(np.intp))
    labels = labels_low[np.ix_(y_indices, x_indices)]
    for label_id in np.unique(labels):
        label = int(label_id)
        if label == efe.BACKGROUND_LABEL:
            continue
        color = np.asarray(dp.LABEL_PALETTE[label % len(dp.LABEL_PALETTE)], dtype=np.float64)
        mask = labels == label_id
        rgb[mask] = np.rint(rgb[mask] * (1.0 - LABEL_ALPHA) + color * LABEL_ALPHA)

    return Image.fromarray(rgb.astype(np.uint8), "RGB").resize((OUT_W, OUT_H), Image.Resampling.NEAREST)


def draw_contact_pair(draw: ImageDraw.ImageDraw, contact: dp.DecodedContact, fit: efe.MomentFit) -> None:
    firmware = ellipse_points(
        contact.x_mm,
        contact.y_mm,
        float(contact.minor_axis_mm),
        float(contact.major_axis_mm),
        float(contact.orientation_deg),
    )
    fitted = ellipse_points(
        fit.x_mm,
        fit.y_mm,
        fit.minor_sigma * FIT_AXIS_SCALE,
        fit.major_sigma * FIT_AXIS_SCALE,
        fit.orientation_deg,
    )
    draw.line(firmware, fill=(0, 255, 255, 255), width=3, joint="curve")
    draw.line(fitted, fill=(255, 0, 255, 255), width=3, joint="curve")
    cx, cy = mm_to_screen(contact.x_mm, contact.y_mm)
    fx, fy = mm_to_screen(fit.x_mm, fit.y_mm)
    draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(0, 255, 255, 220))
    draw.ellipse((fx - 5, fy - 5, fx + 5, fy + 5), fill=(255, 0, 255, 220))
    draw.text((cx + 8, cy + 8), str(contact.id), fill=(255, 255, 255, 255))


def ellipse_points(x_mm: float, y_mm: float, width_mm: float, height_mm: float, orientation_deg: float) -> list[tuple[float, float]]:
    cx, cy = mm_to_screen(x_mm, y_mm)
    w = width_mm / efe.ACTIVE_W_MM * OUT_W
    h = height_mm / efe.ACTIVE_H_MM * OUT_H
    angle = math.radians(orientation_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    points = []
    for step in range(97):
        theta = math.tau * step / 96.0
        x = math.cos(theta) * w / 2.0
        y = math.sin(theta) * h / 2.0
        points.append((cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a))
    return points


def mm_to_screen(x_mm: float, y_mm: float) -> tuple[float, float]:
    return x_mm / efe.ACTIVE_W_MM * OUT_W, y_mm / efe.ACTIVE_H_MM * OUT_H


if __name__ == "__main__":
    raise SystemExit(main())
