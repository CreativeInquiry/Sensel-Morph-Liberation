#!/usr/bin/env python3
"""Try to reproduce Sensel contact ellipses from pressure + label rasters.

This is intentionally experimental. It compares the firmware contact ellipses
from frame N against moments computed from the pressure/label rasters in frame
N-1, matching the observed one-frame contact lag.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

import decode_pressure as dp


ACTIVE_W_MM = 230.0
ACTIVE_H_MM = 130.0
BACKGROUND_LABEL = 255


@dataclass(frozen=True)
class DecodedSurfaceFrame:
    index: int
    frame_id: int
    pressure: dp.DecodedFrame
    labels: dp.DecodedLabels
    contacts: dp.DecodedContacts | None


@dataclass(frozen=True)
class FitConfig:
    name: str
    raster: str
    weight: str
    exponent: float = 1.0
    threshold_frac: float = 0.0


@dataclass(frozen=True)
class MomentFit:
    label: int
    x_mm: float
    y_mm: float
    orientation_deg: float
    major_sigma: float
    minor_sigma: float
    area_cells: int
    total_weight: float


@dataclass
class Sample:
    capture: str
    method: str
    frame_index: int
    frame_id: int
    source_index: int
    source_frame_id: int
    contact_id: int
    contact_state: int
    actual_x: float
    actual_y: float
    actual_orientation: float
    actual_major: float
    actual_minor: float
    fit_x: float
    fit_y: float
    fit_orientation: float
    fit_major_sigma: float
    fit_minor_sigma: float
    area_cells: int
    total_weight: float


FIT_CONFIGS = [
    FitConfig("raw_uniform", "raw", "uniform"),
    FitConfig("raw_pressure", "raw", "pressure", 1.0),
    FitConfig("raw_pressure_sqrt", "raw", "pressure", 0.5),
    FitConfig("raw_pressure_top25", "raw", "pressure", 1.0, 0.25),
    FitConfig("raw_pressure_top50", "raw", "pressure", 1.0, 0.50),
    FitConfig("raw_uniform_top25", "raw", "uniform", 1.0, 0.25),
    FitConfig("expanded_uniform", "expanded", "uniform"),
    FitConfig("expanded_pressure", "expanded", "pressure", 1.0),
    FitConfig("expanded_pressure_sqrt", "expanded", "pressure", 0.5),
    FitConfig("expanded_pressure_squared", "expanded", "pressure", 2.0),
    FitConfig("expanded_pressure_top25", "expanded", "pressure", 1.0, 0.25),
    FitConfig("expanded_pressure_top50", "expanded", "pressure", 1.0, 0.50),
    FitConfig("expanded_uniform_top25", "expanded", "uniform", 1.0, 0.25),
]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Experimentally fit contact ellipses from stale pressure/label rasters.",
    )
    parser.add_argument(
        "captures",
        nargs="*",
        type=pathlib.Path,
        default=sorted(pathlib.Path("captures/full").glob("*.json")),
        help="Full capture JSON files. Defaults to captures/full/*.json.",
    )
    parser.add_argument("--lag", type=int, default=1, help="Raster frame lag to compare against contact frame. Default 1.")
    parser.add_argument("--force-scale", type=float, default=1.0)
    parser.add_argument("--out-dir", type=pathlib.Path, default=pathlib.Path("analysis/ellipse_fit_experiment"))
    parser.add_argument("--top", type=int, default=8, help="Number of summary rows to print.")
    args = parser.parse_args(argv)

    if args.lag < 0:
        raise SystemExit("--lag must be non-negative")
    if not args.captures:
        raise SystemExit("No captures found.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_samples: list[Sample] = []
    for path in args.captures:
        capture_samples = analyze_capture(path, lag=args.lag, force_scale=args.force_scale)
        all_samples.extend(capture_samples)
        write_samples(args.out_dir / f"{path.stem}_samples.csv", capture_samples)

    summary = summarize(all_samples)
    write_summary(args.out_dir / "summary.csv", summary)
    stable_summary = summarize(stable_ellipse_samples(all_samples))
    write_summary(args.out_dir / "summary_stable_elongated.csv", stable_summary)
    print_summary(summary, top=args.top)
    if stable_summary:
        print("\nstable elongated contacts: contact_state=start/move, area_cells>=10, actual_major/actual_minor>=1.5")
        print_summary(stable_summary, top=min(args.top, 5))
    return 0


def analyze_capture(path: pathlib.Path, lag: int, force_scale: float) -> list[Sample]:
    """Compare firmware contacts against fitted ellipses from earlier rasters."""
    record = json.loads(path.read_text())
    frames = decode_surface_frames(record)
    by_index = {frame.index: frame for frame in frames}
    samples: list[Sample] = []

    for frame in frames:
        source = by_index.get(frame.index - lag)
        if source is None or frame.contacts is None:
            continue
        contacts = contacts_with_ellipse(frame.contacts.contacts)
        if not contacts:
            continue

        fit_cache = {config.name: compute_fits(source, config, force_scale=force_scale) for config in FIT_CONFIGS}
        for contact in contacts:
            for config in FIT_CONFIGS:
                fit = fit_cache[config.name].get(contact.id)
                if fit is None:
                    continue
                samples.append(
                    Sample(
                        capture=path.name,
                        method=config.name,
                        frame_index=frame.index,
                        frame_id=frame.frame_id,
                        source_index=source.index,
                        source_frame_id=source.frame_id,
                        contact_id=contact.id,
                        contact_state=contact.state,
                        actual_x=contact.x_mm,
                        actual_y=contact.y_mm,
                        actual_orientation=float(contact.orientation_deg),
                        actual_major=float(contact.major_axis_mm),
                        actual_minor=float(contact.minor_axis_mm),
                        fit_x=fit.x_mm,
                        fit_y=fit.y_mm,
                        fit_orientation=fit.orientation_deg,
                        fit_major_sigma=fit.major_sigma,
                        fit_minor_sigma=fit.minor_sigma,
                        area_cells=fit.area_cells,
                        total_weight=fit.total_weight,
                    )
                )
    return samples


def decode_surface_frames(record: dict[str, object]) -> list[DecodedSurfaceFrame]:
    grids = dp.grids_for_record(record)
    raw_frames = record.get("frames", [])
    if not isinstance(raw_frames, list):
        return []

    out: list[DecodedSurfaceFrame] = []
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, dict):
            continue
        try:
            sections = dp.frame_sections(raw)
            if sections is None or not (sections.content_mask & 0x03):
                continue
            pressure, errors = dp.infer_frame(sections.pressure_label_body, grids, require_all=False)
            if pressure is None:
                print(f"{index}: pressure decode failed: {errors}", file=sys.stderr)
                continue
            if not (sections.content_mask & 0x02):
                continue
            labels = dp.decode_label_body(sections.pressure_label_body[pressure.bytes_used :], pressure.grid)
            payload_hex = raw.get("payload_hex")
            payload_frame_id = 0
            if isinstance(payload_hex, str) and len(payload_hex) >= 4:
                payload_frame_id = bytes.fromhex(payload_hex)[1]
            out.append(
                DecodedSurfaceFrame(
                    index=index,
                    frame_id=int(raw.get("rolling_counter") or payload_frame_id),
                    pressure=pressure,
                    labels=labels,
                    contacts=sections.contacts,
                )
            )
        except Exception as exc:
            print(f"{index}: decode error: {exc!r}", file=sys.stderr)
    return out


def contacts_with_ellipse(contacts: Iterable[dp.DecodedContact]) -> list[dp.DecodedContact]:
    return [
        contact
        for contact in contacts
        if contact.orientation_deg is not None
        and contact.major_axis_mm is not None
        and contact.minor_axis_mm is not None
        and contact.major_axis_mm > 0
        and contact.minor_axis_mm > 0
    ]


def compute_fits(frame: DecodedSurfaceFrame, config: FitConfig, force_scale: float) -> dict[int, MomentFit]:
    pressure, labels = raster_arrays(frame, config.raster, force_scale=force_scale)
    fits: dict[int, MomentFit] = {}
    for label_id in np.unique(labels):
        label = int(label_id)
        if label == BACKGROUND_LABEL:
            continue
        fit = fit_label_moments(label, pressure, labels, config)
        if fit is not None:
            fits[label] = fit
    return fits


def raster_arrays(frame: DecodedSurfaceFrame, raster: str, force_scale: float) -> tuple[np.ndarray, np.ndarray]:
    if raster == "raw":
        pressure = np.asarray(frame.pressure.values, dtype=np.float64).reshape((frame.pressure.grid.rows, frame.pressure.grid.cols))
        if force_scale != 1.0:
            pressure = pressure / force_scale
        labels = np.asarray(frame.labels.values, dtype=np.uint8).reshape((frame.labels.grid.rows, frame.labels.grid.cols))
        return pressure, labels

    if raster != "expanded":
        raise ValueError(f"unknown raster mode: {raster}")

    expanded, width, height = dp.expand_pressure_scaled(frame.pressure, force_scale=force_scale)
    pressure = np.asarray(expanded, dtype=np.float64).reshape((height, width))
    source_labels = np.asarray(frame.labels.values, dtype=np.uint8).reshape((frame.labels.grid.rows, frame.labels.grid.cols))
    y_indices = np.minimum(frame.labels.grid.rows - 1, ((np.arange(height) + 0.5) * frame.labels.grid.rows / height).astype(np.intp))
    x_indices = np.minimum(frame.labels.grid.cols - 1, ((np.arange(width) + 0.5) * frame.labels.grid.cols / width).astype(np.intp))
    labels = source_labels[np.ix_(y_indices, x_indices)]
    return pressure, labels


def fit_label_moments(label: int, pressure: np.ndarray, labels: np.ndarray, config: FitConfig) -> MomentFit | None:
    mask = labels == label
    if config.threshold_frac > 0.0:
        label_pressure = pressure[mask]
        if not label_pressure.size:
            return None
        threshold = float(label_pressure.max()) * config.threshold_frac
        mask = mask & (pressure >= threshold)
    area_cells = int(mask.sum())
    if area_cells < 2:
        return None

    y_idx, x_idx = np.nonzero(mask)
    if config.weight == "uniform":
        weights = np.ones_like(x_idx, dtype=np.float64)
    elif config.weight == "pressure":
        weights = np.maximum(pressure[y_idx, x_idx], 0.0)
        if config.exponent != 1.0:
            weights = np.power(weights, config.exponent)
    else:
        raise ValueError(f"unknown weight mode: {config.weight}")

    total_weight = float(weights.sum())
    if total_weight <= 0:
        return None

    height, width = pressure.shape
    x_mm = (x_idx.astype(np.float64) + 0.5) * ACTIVE_W_MM / width
    y_mm = (y_idx.astype(np.float64) + 0.5) * ACTIVE_H_MM / height
    mean_x = float(np.sum(weights * x_mm) / total_weight)
    mean_y = float(np.sum(weights * y_mm) / total_weight)

    dx = x_mm - mean_x
    dy = y_mm - mean_y
    cov = np.array(
        [
            [float(np.sum(weights * dx * dx) / total_weight), float(np.sum(weights * dx * dy) / total_weight)],
            [float(np.sum(weights * dx * dy) / total_weight), float(np.sum(weights * dy * dy) / total_weight)],
        ],
        dtype=np.float64,
    )
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    order = np.argsort(eigenvalues)[::-1]
    major_value = max(0.0, float(eigenvalues[order[0]]))
    minor_value = max(0.0, float(eigenvalues[order[1]]))
    major_vector = eigenvectors[:, order[0]]
    major_angle = math.degrees(math.atan2(float(major_vector[1]), float(major_vector[0])))
    orientation = wrap_degrees(major_angle - 90.0)

    return MomentFit(
        label=label,
        x_mm=mean_x,
        y_mm=mean_y,
        orientation_deg=orientation,
        major_sigma=math.sqrt(major_value),
        minor_sigma=math.sqrt(minor_value),
        area_cells=area_cells,
        total_weight=total_weight,
    )


def summarize(samples: list[Sample]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for method in sorted({sample.method for sample in samples}):
        method_samples = [sample for sample in samples if sample.method == method]
        if not method_samples:
            continue

        fit_major = np.asarray([sample.fit_major_sigma for sample in method_samples], dtype=np.float64)
        fit_minor = np.asarray([sample.fit_minor_sigma for sample in method_samples], dtype=np.float64)
        actual_major = np.asarray([sample.actual_major for sample in method_samples], dtype=np.float64)
        actual_minor = np.asarray([sample.actual_minor for sample in method_samples], dtype=np.float64)
        scale = least_squares_scale(
            np.concatenate([fit_major, fit_minor]),
            np.concatenate([actual_major, actual_minor]),
        )
        major_scale = least_squares_scale(fit_major, actual_major)
        minor_scale = least_squares_scale(fit_minor, actual_minor)

        center_errors = np.asarray([distance(sample.actual_x, sample.actual_y, sample.fit_x, sample.fit_y) for sample in method_samples])
        orientation_errors = np.asarray(
            [angle_error_deg(sample.actual_orientation, sample.fit_orientation) for sample in method_samples],
            dtype=np.float64,
        )
        major_errors = actual_major - fit_major * scale
        minor_errors = actual_minor - fit_minor * scale
        axis_errors = np.concatenate([major_errors, minor_errors])
        rows.append(
            {
                "method": method,
                "n": len(method_samples),
                "center_rmse_mm": rmse(center_errors),
                "center_mae_mm": mae(center_errors),
                "orientation_rmse_deg": rmse(orientation_errors),
                "orientation_mae_deg": mae(orientation_errors),
                "axis_scale": scale,
                "major_scale": major_scale,
                "minor_scale": minor_scale,
                "major_rmse_mm": rmse(major_errors),
                "minor_rmse_mm": rmse(minor_errors),
                "axis_rmse_mm": rmse(axis_errors),
                "axis_mae_mm": mae(np.abs(axis_errors)),
            }
        )
    rows.sort(key=lambda row: (float(row["orientation_mae_deg"]), float(row["axis_rmse_mm"]), float(row["center_rmse_mm"])))
    return rows


def stable_ellipse_samples(samples: list[Sample]) -> list[Sample]:
    out = []
    for sample in samples:
        aspect = sample.actual_major / sample.actual_minor if sample.actual_minor else 0.0
        if sample.contact_state in (1, 2) and sample.area_cells >= 10 and aspect >= 1.5:
            out.append(sample)
    return out


def least_squares_scale(predicted: np.ndarray, actual: np.ndarray) -> float:
    denom = float(np.dot(predicted, predicted))
    return float(np.dot(predicted, actual) / denom) if denom else 0.0


def distance(x0: float, y0: float, x1: float, y1: float) -> float:
    return math.hypot(x0 - x1, y0 - y1)


def rmse(values: np.ndarray) -> float:
    return float(math.sqrt(float(np.mean(values * values)))) if values.size else 0.0


def mae(values: np.ndarray) -> float:
    return float(np.mean(np.abs(values))) if values.size else 0.0


def wrap_degrees(angle: float) -> float:
    while angle <= -90.0:
        angle += 180.0
    while angle > 90.0:
        angle -= 180.0
    return angle


def angle_error_deg(a: float, b: float) -> float:
    return abs(wrap_degrees(a - b))


def write_samples(path: pathlib.Path, samples: list[Sample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(Sample.__dataclass_fields__.keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            writer.writerow(sample.__dict__)


def write_summary(path: pathlib.Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "method",
        "n",
        "center_rmse_mm",
        "center_mae_mm",
        "orientation_rmse_deg",
        "orientation_mae_deg",
        "axis_scale",
        "major_scale",
        "minor_scale",
        "major_rmse_mm",
        "minor_rmse_mm",
        "axis_rmse_mm",
        "axis_mae_mm",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_summary(rows: list[dict[str, object]], top: int) -> None:
    if not rows:
        print("No comparable ellipse samples found.")
        return
    print("method,n,center_rmse_mm,orientation_mae_deg,axis_scale,axis_rmse_mm,major_scale,minor_scale")
    for row in rows[:top]:
        print(
            f"{row['method']},{row['n']},"
            f"{float(row['center_rmse_mm']):.3f},"
            f"{float(row['orientation_mae_deg']):.3f},"
            f"{float(row['axis_scale']):.3f},"
            f"{float(row['axis_rmse_mm']):.3f},"
            f"{float(row['major_scale']):.3f},"
            f"{float(row['minor_scale']):.3f}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
