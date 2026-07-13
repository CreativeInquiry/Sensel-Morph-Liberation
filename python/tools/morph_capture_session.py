#!/usr/bin/env python3
"""Persistent Morph capture session.

Reads one JSON command per line from stdin:

  {"label":"name","duration":10,"max_frames":300}
  {"label":"name","duration":10,"max_frames":300,"out_dir":"captures/pressure_and_labels","frame_content":3}
  {"label":"name","duration":10,"max_frames":300,"out_dir":"captures/full","frame_content":15,"contacts_mask":15}
  {"label":"name","duration":10,"max_frames":300,"output_format":"json"}
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import morph_capture as mc


def capture(
    label: str,
    duration: float,
    max_frames: int,
    out_dir: str = "captures",
    frame_content: int = mc.FRAME_CONTENT_PRESSURE,
    scan_detail: int = mc.SCAN_DETAIL_HIGH,
    contacts_mask: int | None = None,
    output_format: str = "jsonl",
) -> dict[str, object]:
    """Capture one labeled recording while preserving initial/final registers."""
    device = mc.find_device()
    usb_info = mc.usb_info_for_device(device)
    pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
    output_format = normalize_output_format(output_format)
    out_path = recording_output_path(out_dir, label, output_format)

    fd = mc.open_serial(device)
    record: dict[str, object] = {
        "label": label,
        "path": device,
        "usb": usb_info,
        "serial_number": usb_info.get("serial_number") or "",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "duration": duration,
        "writes": [],
        "frames": [],
    }
    try:
        record["initial"] = {
            "scan_detail": mc.read_reg(fd, mc.REG_SCAN_DETAIL, 1).hex(),
            "frame_content": mc.read_reg(fd, mc.REG_FRAME_CONTENT, 1).hex(),
            "scan_enabled": mc.read_reg(fd, mc.REG_SCAN_ENABLED, 1).hex(),
            "contacts_mask": mc.read_reg(fd, 0x4B, 1).hex(),
        }
        record["writes"].append(mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0))
        record["writes"].append(mc.write_reg(fd, mc.REG_SCAN_DETAIL, scan_detail))
        record["writes"].append(mc.write_reg(fd, mc.REG_FRAME_CONTENT, frame_content))
        if contacts_mask is not None:
            record["writes"].append(mc.write_reg(fd, 0x4B, contacts_mask))
        record["requested"] = {
            "scan_detail": scan_detail,
            "frame_content": frame_content,
            "contacts_mask": contacts_mask,
        }
        if frame_content & 0x03:
            record["compression_metadata"] = mc.read_vs(fd, mc.REG_COMPRESSION_METADATA)

        print(json.dumps({"event": "recording", "label": label}), flush=True)
        record["writes"].append(mc.write_reg(fd, mc.REG_SCAN_ENABLED, 1))
        end = time.monotonic() + duration
        while time.monotonic() < end and len(record["frames"]) < max_frames:
            try:
                record["frames"].append(mc.read_frame(fd, timeout=1.0))
            except Exception as exc:
                record.setdefault("frame_errors", []).append(repr(exc))
                time.sleep(0.02)
    finally:
        try:
            record["writes"].append(mc.write_reg(fd, mc.REG_SCAN_ENABLED, 0))
        except Exception as exc:
            record["stop_error"] = repr(exc)
        mc.clear_leds_safely(fd)
        initial = record.get("initial", {})
        if isinstance(initial, dict):
            try:
                record["writes"].append(mc.write_reg(fd, mc.REG_SCAN_DETAIL, int(initial["scan_detail"], 16)))
            except Exception as exc:
                record["restore_detail_error"] = repr(exc)
            try:
                record["writes"].append(mc.write_reg(fd, mc.REG_FRAME_CONTENT, int(initial["frame_content"], 16)))
            except Exception as exc:
                record["restore_content_error"] = repr(exc)
            try:
                record["writes"].append(mc.write_reg(fd, 0x4B, int(initial["contacts_mask"], 16)))
            except Exception as exc:
                record["restore_contacts_mask_error"] = repr(exc)
        try:
            record["final"] = {
                "scan_detail": mc.read_reg(fd, mc.REG_SCAN_DETAIL, 1).hex(),
                "frame_content": mc.read_reg(fd, mc.REG_FRAME_CONTENT, 1).hex(),
                "scan_enabled": mc.read_reg(fd, mc.REG_SCAN_ENABLED, 1).hex(),
                "contacts_mask": mc.read_reg(fd, 0x4B, 1).hex(),
            }
        except Exception as exc:
            record["final_read_error"] = repr(exc)
        mc.os.close(fd)

    if output_format == "json":
        write_legacy_json_record(record, out_path)
    else:
        write_jsonl_record(record, out_path)
    frames = record.get("frames", [])
    sizes = [frame["payload_size"] for frame in frames if isinstance(frame, dict)]
    summary = {
        "event": "done",
        "out": str(out_path),
        "format": output_format,
        "label": label,
        "frames": len(frames),
        "min_size": min(sizes) if sizes else None,
        "max_size": max(sizes) if sizes else None,
        "checksum_ok_all": all(frame.get("checksum_ok") for frame in frames if isinstance(frame, dict)),
        "initial": record.get("initial"),
        "final": record.get("final"),
    }
    print(json.dumps(summary), flush=True)
    return summary


def normalize_output_format(value: str) -> str:
    """Normalize command-line and per-capture output format names."""
    fmt = value.strip().lower().replace("-", "_")
    if fmt in ("jsonl", "processing_jsonl", "raw_jsonl"):
        return "jsonl"
    if fmt in ("json", "legacy", "legacy_json", "old_json"):
        return "json"
    raise ValueError(f"unsupported output_format {value!r}; use jsonl or json")


def safe_filename(value: str) -> str:
    """Return a conservative filename fragment."""
    out = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return out or "recording"


def recording_output_path(out_dir: str, label: str, output_format: str) -> pathlib.Path:
    """Return the default output path for the selected recording format."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    directory = pathlib.Path(out_dir)
    if output_format == "json":
        return unique_path(directory / f"{stamp}_{safe_filename(label)}.json")
    return unique_path(directory / f"sensel_recording_{stamp}.jsonl")


def unique_path(path: pathlib.Path) -> pathlib.Path:
    """Avoid clobbering if multiple captures start inside the same second."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem}_{i:03d}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find unused output path near {path}")


def write_legacy_json_record(record: dict[str, object], out_path: pathlib.Path) -> None:
    """Write the old top-level JSON recording format."""
    out_path.write_text(json.dumps(record, indent=2) + "\n")


def write_jsonl_record(record: dict[str, object], out_path: pathlib.Path) -> None:
    """Write Processing-compatible raw packet JSONL."""
    with out_path.open("w", encoding="utf-8") as fp:
        fp.write(json.dumps(jsonl_header(record), separators=(",", ":")) + "\n")
        for frame in record.get("frames", []):
            if isinstance(frame, dict):
                fp.write(json.dumps(jsonl_frame(frame), separators=(",", ":")) + "\n")


def jsonl_header(record: dict[str, object]) -> dict[str, object]:
    """Return the JSONL header object used by Processing recorder/player tools."""
    requested = record.get("requested")
    if not isinstance(requested, dict):
        requested = {}
    initial = record.get("initial")
    compression_metadata = record.get("compression_metadata")
    scan_detail = int(requested.get("scan_detail", mc.SCAN_DETAIL_HIGH))
    return {
        "type": "header",
        "format": "sensel_morph_raw_jsonl",
        "version": 1,
        "source": "python:sensel_morph_capture_session",
        "label": record.get("label", ""),
        "started_at": record.get("started_at", ""),
        "duration": record.get("duration", 0),
        "path": record.get("path", ""),
        "serial_number": record.get("serial_number", ""),
        "requested": {
            "scan_detail": scan_detail,
            "pressure_res": pressure_res_for_scan_detail(scan_detail),
            "pressure_type": "uint8",
            "frame_content": int(requested.get("frame_content", mc.FRAME_CONTENT_PRESSURE)),
            "contacts_mask": -1 if requested.get("contacts_mask") is None else int(requested.get("contacts_mask")),
        },
        "initial": initial if isinstance(initial, dict) else {},
        "compression_metadata": normalize_compression_metadata(compression_metadata),
    }


def pressure_res_for_scan_detail(scan_detail: int) -> str:
    """Mirror the Processing convention for recording resolution metadata."""
    return "high" if scan_detail == mc.SCAN_DETAIL_HIGH else "med"


def normalize_compression_metadata(value: object) -> dict[str, object]:
    """Return a Processing-style compression_metadata object."""
    if not isinstance(value, dict):
        return {"header": "", "size": 0, "data_hex": "", "checksum": 0}
    return {
        "header": str(value.get("header", "")),
        "size": int(value.get("size", 0)),
        "data_hex": str(value.get("data_hex", "")),
        "checksum": int(value.get("checksum", 0)),
    }


def jsonl_frame(frame: dict[str, object]) -> dict[str, object]:
    """Return one Processing-compatible raw frame packet JSON object."""
    return {
        "type": "frame",
        "reg": int(frame.get("reg", mc.REG_SCAN_READ_FRAME)),
        "header": int(frame.get("header", 0)),
        "payload_size": int(frame.get("payload_size", 0)),
        "payload_hex": str(frame.get("payload_hex", "")),
        "checksum": int(frame.get("checksum", 0)),
        "checksum_ok": bool(frame.get("checksum_ok", False)),
        "content_mask": nullable_int(frame.get("content_mask")),
        "rolling_counter": nullable_int(frame.get("rolling_counter")),
        "timestamp_le": nullable_int(frame.get("timestamp_le")),
    }


def nullable_int(value: object) -> int | None:
    """Convert optional numeric frame fields without inventing zeros."""
    return None if value is None else int(value)


def main(argv: list[str] | None = None) -> int:
    """Run a line-oriented capture command loop for coordinated recordings."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-format",
        default="jsonl",
        choices=("jsonl", "json"),
        help="recording format: jsonl for Processing recorder/player compatibility, or json for the legacy top-level frames[] format",
    )
    args = parser.parse_args(argv)
    default_output_format = normalize_output_format(args.output_format)

    print(json.dumps({"event": "session_ready", "output_format": default_output_format}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == "quit":
            print(json.dumps({"event": "session_quit"}), flush=True)
            return 0
        try:
            command = json.loads(line)
            label = command["label"]
            duration = float(command.get("duration", 10))
            max_frames = int(command.get("max_frames", 300))
            out_dir = str(command.get("out_dir", "captures"))
            frame_content = int(command.get("frame_content", mc.FRAME_CONTENT_PRESSURE))
            scan_detail = int(command.get("scan_detail", mc.SCAN_DETAIL_HIGH))
            contacts_mask = command.get("contacts_mask")
            contacts_mask = None if contacts_mask is None else int(contacts_mask)
            output_format = normalize_output_format(str(command.get("output_format", default_output_format)))
            capture(
                label,
                duration,
                max_frames,
                out_dir=out_dir,
                frame_content=frame_content,
                scan_detail=scan_detail,
                contacts_mask=contacts_mask,
                output_format=output_format,
            )
        except Exception as exc:
            print(json.dumps({"event": "error", "error": repr(exc)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
