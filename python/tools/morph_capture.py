#!/usr/bin/env python3
"""Capture raw compressed Sensel Morph pressure frames over USB CDC serial."""

from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import select
import sys
import termios
import time
import tty

from serial.tools import list_ports


REG_SCAN_DETAIL = 0x23
REG_FRAME_CONTENT = 0x24
REG_SCAN_ENABLED = 0x25
REG_SCAN_READ_FRAME = 0x26
REG_COMPRESSION_METADATA = 0x1C
REG_LED_BRIGHTNESS = 0x80
REG_LED_BRIGHTNESS_SIZE = 0x81
REG_LED_COUNT = 0x84

SCAN_DETAIL_HIGH = 0
FRAME_CONTENT_PRESSURE = 0x01


def find_device() -> str:
    """Return the first Morph-looking USB CDC callout device."""
    matches = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not matches:
        raise SystemExit("No /dev/cu.usbmodem* device found.")
    return matches[0]


def usb_info_for_device(path: str) -> dict[str, str | None]:
    """Return USB metadata for a serial device path when pyserial can see it."""
    wanted = os.path.realpath(path)
    for port in list_ports.comports():
        device = getattr(port, "device", None)
        if not device:
            continue
        if os.path.realpath(device) != wanted:
            continue
        return {
            "device": device,
            "serial_number": getattr(port, "serial_number", None),
            "manufacturer": getattr(port, "manufacturer", None),
            "product": getattr(port, "product", None),
            "vid": f"{port.vid:04x}" if getattr(port, "vid", None) is not None else None,
            "pid": f"{port.pid:04x}" if getattr(port, "pid", None) is not None else None,
            "location": getattr(port, "location", None),
        }
    return {
        "device": path,
        "serial_number": None,
        "manufacturer": None,
        "product": None,
        "vid": None,
        "pid": None,
        "location": None,
    }


def serial_number_for_device(path: str) -> str:
    """Return the USB serial number, or an empty string if pyserial lacks it."""
    info = usb_info_for_device(path)
    serial = info.get("serial_number")
    return serial or ""


def open_serial(path: str) -> int:
    """Open the Morph CDC serial port in raw, nonblocking, 115200 baud mode."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def read_exact(fd: int, n: int, timeout: float = 2.0) -> bytes:
    """Read exactly ``n`` bytes from a nonblocking fd or raise ``TimeoutError``."""
    out = bytearray()
    end = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        wait = max(0.0, min(0.25, end - time.monotonic()))
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            continue
        try:
            chunk = os.read(fd, n - len(out))
        except BlockingIOError:
            chunk = b""
        if chunk:
            out.extend(chunk)
    if len(out) != n:
        raise TimeoutError(f"wanted {n}, got {len(out)}: {out.hex()}")
    return bytes(out)


def read_reg(fd: int, reg: int, size: int) -> bytes:
    """Read a fixed-size register using the Morph's 0x81 register-read frame."""
    os.write(fd, bytes([0x81, reg, size]))
    hdr = read_exact(fd, 4)
    ack, got_reg, lo, hi = hdr
    resp_size = lo | (hi << 8)
    data = read_exact(fd, resp_size)
    checksum = read_exact(fd, 1)[0]
    if ack != 1 or got_reg != reg or resp_size != size:
        raise RuntimeError(f"bad read header reg=0x{reg:02x}: {hdr.hex()}")
    if (sum(data) & 0xFF) != checksum:
        raise RuntimeError(f"bad read checksum reg=0x{reg:02x}: {data.hex()} {checksum:02x}")
    return data


def write_reg(fd: int, reg: int, data: int | bytes) -> dict[str, object]:
    """Write a small register payload and validate the two-byte device ack."""
    if isinstance(data, int):
        data = bytes([data])
    packet = bytes([0x01, reg, len(data)]) + data + bytes([sum(data) & 0xFF])
    os.write(fd, packet)
    ack = read_exact(fd, 2)
    if ack != bytes([5, reg]):
        raise RuntimeError(f"bad write ack reg=0x{reg:02x}: {ack.hex()}")
    return {"reg": reg, "data": data.hex(), "ack": ack.hex()}


def write_vs(fd: int, reg: int, data: bytes) -> dict[str, object]:
    """Write a variable-size register using acknowledged 512-byte chunks."""
    size = len(data)
    header = bytes([0x01, reg, 0x00, 0x04]) + size.to_bytes(4, "little")
    header_checksum = sum(header[4:8]) & 0xFF
    os.write(fd, header + bytes([header_checksum]))
    header_ack = read_exact(fd, 2)
    if header_ack != bytes([7, reg]):
        raise RuntimeError(f"bad vs write header ack reg=0x{reg:02x}: {header_ack.hex()}")

    written = 0
    while written < size:
        packet_size = min(size - written, 512)
        packet = data[written : written + packet_size]
        os.write(fd, len(packet).to_bytes(2, "little") + packet + bytes([sum(packet) & 0xFF]))
        ack = read_exact(fd, 1)
        if ack != b"\x07":
            raise RuntimeError(f"bad vs write packet ack reg=0x{reg:02x}: {ack.hex()}")
        written += len(packet)
    return {"reg": reg, "size": size, "data": data.hex(), "ack": header_ack.hex()}


def write_vs_pipelined(fd: int, reg: int, data: bytes, timeout: float = 2.0) -> dict[str, object]:
    """Write a variable-size register by sending all chunks before reading acks.

    This is useful for LED animation, where waiting for one ack per LED frame is
    visibly too expensive. The device still returns one ack per transmitted
    chunk; we simply collect them after the write burst.
    """
    size = len(data)
    header = bytes([0x01, reg, 0x00, 0x04]) + size.to_bytes(4, "little")
    header_checksum = sum(header[4:8]) & 0xFF

    packets = []
    written = 0
    while written < size:
        packet_size = min(size - written, 512)
        packet = data[written : written + packet_size]
        packets.append(len(packet).to_bytes(2, "little") + packet + bytes([sum(packet) & 0xFF]))
        written += len(packet)

    os.write(fd, header + bytes([header_checksum]) + b"".join(packets))
    ack = read_exact(fd, 2 + len(packets), timeout=timeout)
    header_ack = ack[:2]
    packet_ack = ack[2:]
    if header_ack != bytes([7, reg]):
        raise RuntimeError(f"bad pipelined vs write header ack reg=0x{reg:02x}: {header_ack.hex()}")
    if packet_ack != b"\x07" * len(packets):
        raise RuntimeError(f"bad pipelined vs write packet ack reg=0x{reg:02x}: {packet_ack.hex()}")
    return {"reg": reg, "size": size, "data": data.hex(), "ack": ack.hex(), "packets": len(packets)}


def clear_leds(fd: int) -> None:
    """Set every controllable white strip LED to zero brightness."""
    count = read_reg(fd, REG_LED_COUNT, 1)[0]
    reg_size = read_reg(fd, REG_LED_BRIGHTNESS_SIZE, 1)[0]
    if count <= 0 or reg_size not in (1, 2):
        return
    write_vs_pipelined(fd, REG_LED_BRIGHTNESS, bytes(count * reg_size))


def clear_leds_safely(fd: int) -> None:
    """Best-effort LED cleanup used by long-running transmitter exits."""
    try:
        clear_leds(fd)
    except Exception as exc:
        print(f"warning: failed to clear LEDs: {exc}", file=sys.stderr)


def read_vs(fd: int, reg: int) -> dict[str, object]:
    """Read a variable-size register and return metadata plus the hex payload."""
    os.write(fd, bytes([0x81, reg, 0x00]))
    hdr = read_exact(fd, 5)
    ack, got_reg, zero, lo, hi = hdr
    size = lo | (hi << 8)
    data = read_exact(fd, size)
    checksum = read_exact(fd, 1)[0]
    if ack != 3 or got_reg != reg or zero != 0:
        raise RuntimeError(f"bad vs header reg=0x{reg:02x}: {hdr.hex()}")
    if (sum(data) & 0xFF) != checksum:
        raise RuntimeError(f"bad vs checksum reg=0x{reg:02x}: {data.hex()} {checksum:02x}")
    return {"header": hdr.hex(), "size": size, "data_hex": data.hex(), "checksum": checksum}


def read_frame(fd: int, timeout: float = 2.0) -> dict[str, object]:
    """Request one live scan frame from ``REG_SCAN_READ_FRAME``."""
    os.write(fd, bytes([0x81, REG_SCAN_READ_FRAME, 0x00]))
    ack = read_exact(fd, 1, timeout=timeout)
    if ack != b"\x03":
        raise RuntimeError(f"bad frame ack: {ack.hex()}")
    hdr = read_exact(fd, 4, timeout=timeout)
    reg, header, lo, hi = hdr
    size = lo | (hi << 8)
    payload = read_exact(fd, size, timeout=timeout)
    checksum = read_exact(fd, 1, timeout=timeout)[0]
    checksum_ok = (sum(payload) & 0xFF) == checksum
    return {
        "reg": reg,
        "header": header,
        "payload_size": size,
        "payload_hex": payload.hex(),
        "checksum": checksum,
        "checksum_ok": checksum_ok,
        "content_mask": payload[0] if len(payload) > 0 else None,
        "rolling_counter": payload[1] if len(payload) > 1 else None,
        "timestamp_le": int.from_bytes(payload[2:6], "little") if len(payload) >= 6 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("label", help="short capture label, e.g. baseline_no_touch")
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--countdown", type=float, default=5.0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--out-dir", default="captures")
    parser.add_argument("--max-frames", type=int, default=240)
    args = parser.parse_args()

    device = args.device or find_device()
    usb_info = usb_info_for_device(device)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in args.label)
    out_path = out_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_label}.json"

    fd = open_serial(device)
    record: dict[str, object] = {
        "label": args.label,
        "path": device,
        "usb": usb_info,
        "serial_number": usb_info.get("serial_number") or "",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "duration": args.duration,
        "writes": [],
        "frames": [],
    }
    try:
        record["initial"] = {
            "scan_detail": read_reg(fd, REG_SCAN_DETAIL, 1).hex(),
            "frame_content": read_reg(fd, REG_FRAME_CONTENT, 1).hex(),
            "scan_enabled": read_reg(fd, REG_SCAN_ENABLED, 1).hex(),
        }
        record["writes"].append(write_reg(fd, REG_SCAN_ENABLED, 0))
        record["writes"].append(write_reg(fd, REG_SCAN_DETAIL, SCAN_DETAIL_HIGH))
        record["writes"].append(write_reg(fd, REG_FRAME_CONTENT, FRAME_CONTENT_PRESSURE))
        record["compression_metadata"] = read_vs(fd, REG_COMPRESSION_METADATA)

        print(f"READY {args.label}", flush=True)
        if args.countdown > 0:
            whole_seconds = int(args.countdown)
            fractional = args.countdown - whole_seconds
            for remaining in range(whole_seconds, 0, -1):
                print(f"COUNTDOWN {remaining}", flush=True)
                time.sleep(1.0)
            if fractional > 0:
                time.sleep(fractional)
        print(f"RECORDING {args.label}", flush=True)

        record["writes"].append(write_reg(fd, REG_SCAN_ENABLED, 1))
        end = time.monotonic() + args.duration
        while time.monotonic() < end and len(record["frames"]) < args.max_frames:
            try:
                record["frames"].append(read_frame(fd, timeout=1.0))
            except Exception as exc:  # keep bounded capture going
                record.setdefault("frame_errors", []).append(repr(exc))
                time.sleep(0.02)
        print(f"STOP {args.label}", flush=True)
    finally:
        try:
            record["writes"].append(write_reg(fd, REG_SCAN_ENABLED, 0))
        except Exception as exc:
            record["stop_error"] = repr(exc)
        clear_leds_safely(fd)
        initial = record.get("initial", {})
        if isinstance(initial, dict):
            try:
                record["writes"].append(write_reg(fd, REG_SCAN_DETAIL, int(initial["scan_detail"], 16)))
            except Exception as exc:
                record["restore_detail_error"] = repr(exc)
            try:
                record["writes"].append(write_reg(fd, REG_FRAME_CONTENT, int(initial["frame_content"], 16)))
            except Exception as exc:
                record["restore_content_error"] = repr(exc)
        try:
            record["final"] = {
                "scan_detail": read_reg(fd, REG_SCAN_DETAIL, 1).hex(),
                "frame_content": read_reg(fd, REG_FRAME_CONTENT, 1).hex(),
                "scan_enabled": read_reg(fd, REG_SCAN_ENABLED, 1).hex(),
            }
        except Exception as exc:
            record["final_read_error"] = repr(exc)
        os.close(fd)

    out_path.write_text(json.dumps(record, indent=2))
    frames = record.get("frames", [])
    sizes = [frame["payload_size"] for frame in frames if isinstance(frame, dict)]
    summary = {
        "out": str(out_path),
        "label": args.label,
        "frames": len(frames),
        "min_size": min(sizes) if sizes else None,
        "max_size": max(sizes) if sizes else None,
        "checksum_ok_all": all(frame.get("checksum_ok") for frame in frames if isinstance(frame, dict)),
        "initial": record.get("initial"),
        "final": record.get("final"),
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
