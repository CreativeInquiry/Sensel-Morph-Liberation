# Python Tools for Sensel Morph

---

## Overview

This directory contains the Python command-line tools for capturing, decoding,
broadcasting, and experimenting with Sensel Morph data. Install them from this
directory with `pipx` or run them from source during development.

**Contents:**

* [Installation](#installation)
* [Recording](#recording)
* [Live OSC Broadcast](#live-osc-broadcast)
* [Live WebSocket Broadcast](#live-websocket-broadcast)
* [Resolution Behavior](#resolution-behavior)
* [LED Strip Control](#led-strip-control)

### Summary of Python Tools

| Command & Source File | Primary use | Receives | Sends / Produces | Notes |
|---|---|---|---|---|
| [`sensel_morph_osc.py`](tools/sensel_morph_osc.py) | Live USB CDC reader and OSC broadcaster. | Raw Morph frames over USB CDC serial. | OSC over UDP, default port `1560`. | Main Python bridge for Processing, Max, TouchDesigner, and other OSC tools. Accelerometer is always sent; pressure, labels, and contacts are selected by flags. |
| [`sensel_morph_ws.py`](tools/sensel_morph_ws.py) | Live USB CDC reader and WebSocket broadcaster for browser sketches. | Raw Morph frames over USB CDC serial. | WebSocket binary raster frames plus JSON metadata/contact messages. | Browser-facing path for the p5.js receivers. Pressure and labels are sent as `uint8`. |
| [`sensel_morph_capture_session.py`](tools/morph_capture_session.py) | Current Python recorder. | Raw Morph frames over USB CDC serial. | Processing-compatible JSONL recordings by default; legacy JSON on request. | Preferred Python recording tool. Takes newline-delimited JSON commands on stdin. |
| [`sensel_morph_capture.py`](tools/morph_capture.py) | One-shot low-level capture/probe utility. | Raw Morph frames over USB CDC serial. | Single capture/probe outputs. | Older diagnostic utility; useful for low-level probing, not the main recorder. |
| [`sensel_morph_led.py`](tools/sensel_morph_led.py) | Control the Morph's 24 white LEDs. | Raw force/contact data as needed for pressure-reactive modes. | LED register writes over USB CDC serial. | Fun but throughput-expensive. LED animation can significantly slow frame capture. |

---

## Installation

Install the Python commands from a source checkout with `pipx`:

```sh
cd python
pipx install --python python3.10 .
```

If you already installed an earlier checkout, force a reinstall so new commands
and dependencies are picked up:

```sh
cd python
pipx install --force --python python3.10 .
```

If `pipx install .` reports that Python 3.9 is too old, keep using the explicit
`--python python3.10` form above, or point `pipx` at another installed Python
3.10+ interpreter. This installs command-line tools without requiring you to create or activate a project venv; it should produce: 

- `sensel_morph_osc`: live USB CDC reader and OSC broadcaster.
- `sensel_morph_ws`: live USB CDC reader and WebSocket broadcaster for browser sketches.
- `sensel_morph_capture`: one-shot capture utility.
- `sensel_morph_capture_session`: stdin-driven capture session utility.
- `sensel_morph_led`: white LED strip control and pressure-responsive modes.

For local development and tests:

```sh
cd python
python -m pip install -e ".[dev]"
python -m pytest
```

---

## Recording

`sensel_morph_capture_session` now writes Processing-compatible raw JSONL
recordings by default, with filenames like
`sensel_recording_20260712_153000.jsonl`. Each file has one header object and
then one raw packet object per line, matching the recorder/playback format used
by the Processing transmitters. From this `python/` directory, a full
pressure+labels+contacts+accelerometer JSONL capture can be made with this exact
one-line command:

```sh
printf '{"label":"full_test","duration":10,"max_frames":600,"out_dir":"../captures/full","frame_content":15,"contacts_mask":15}\nquit\n' | sensel_morph_capture_session
```

If that command prints only `{"event":"session_ready"}` and produces a `.json`
file, your shell is running an older installed copy. Reinstall the current
checkout first:

```sh
pipx install --force --python python3.10 .
```

The updated tool prints `{"event":"session_ready","output_format":"jsonl"}` on
startup.

The older top-level JSON format is still available when needed:

```sh
sensel_morph_capture_session --output-format json
```

For example, from this `python/` directory, the same full capture in legacy JSON
format is:

```sh
printf '{"label":"full_test","duration":10,"max_frames":600,"out_dir":"../captures/full","frame_content":15,"contacts_mask":15}\nquit\n' | sensel_morph_capture_session --output-format json
```

Within a running session, a single stdin command can also override the default:

```json
{"label":"test","duration":10,"max_frames":300,"frame_content":15,"contacts_mask":15,"output_format":"json"}
```


---

## Live OSC Broadcast

`sensel_morph_osc` reads the Morph over USB CDC serial, decodes frames locally,
and broadcasts OSC over UDP. Its default OSC port is `1560`, because the Morph
product ID is `0x0618`, decimal `1560`. Accelerometer data is always requested
and broadcast; pressure, label, and contact streams are selected with flags.

Examples:

```sh
sensel_morph_osc --pressure --pressure-res high --pressure-type uint8
```

```sh
sensel_morph_osc \
  --pressure --pressure-res med --pressure-type uint16 \
  --labels --contacts --compat morphosc,senselosc
```

From any shell, a good live test command for the Processing OSC receiver is:

```sh
sensel_morph_osc \
  --pressure --pressure-res low --pressure-type uint8 \
  --labels --contacts
```

With the checked-out calibration file for Morph serial `2044B8374E33`:

```sh
sensel_morph_osc \
  --pressure --pressure-res high --pressure-type uint16 \
  --rle \
  --calibration python/tools/calibration_2044B8374E33.json
```

`sensel_morph_osc` can also drive the Morph LED strip while broadcasting OSC:

```sh
sensel_morph_osc --pressure --labels --contacts --led-mode twinkle
sensel_morph_osc --led-mode pulse --led-pressure-floor 50 --led-frame-interval 4
sensel_morph_osc --led-mode all 123
```

If `--led-mode` is omitted, the broadcaster sets all LEDs to `0` on startup.
Warning: LED animation is decorative and expensive. Because LED brightness
writes block the same serial command stream used for frame reads, enabling
`--led-mode` can essentially halve your OSC data transmission rate. Use it only
when the visual LED effect is worth the throughput cost.
`columns` and `pulse` request pressure frames internally even when `--pressure`
is not being broadcast over OSC. `glow`, `kitt`, `meter`, and `twinkle` can use
firmware contact force totals instead when `--contacts` is enabled, so
`--contacts --led-mode twinkle` does not request pressure. LED tuning options
mirror `sensel_morph_led` with a `led-` prefix: `--led-pressure-ref`,
`--led-pressure-floor`, `--led-column-threshold`, `--led-frame-interval`,
`--led-pulse-min-step`, `--led-pulse-max-step`,
`--led-pulse-response-gamma`, `--led-kitt-min-step`, `--led-kitt-max-step`,
and `--led-seed`. `--led-read-timeout` is accepted for CLI parity, but OSC frame
reads use the main `--read-timeout` option.
Pressure-responsive LED modes update after OSC frame transmission, every
`--led-frame-interval` decoded frames by default. The default interval is `4`.
OSC LED updates use a pipelined register write: the brightness command is sent
first, then the expected acknowledgement bytes are drained before the next frame
read. This keeps the serial stream synchronized while avoiding an extra
wait-between-header-and-payload round trip.

Native OSC messages use the `/sensel_morph` prefix:

```text
/sensel_morph/status        device content_mask pressure_res pressure_type rle_enabled serial_number calibrated
/sensel_morph/frame         frame_id timestamp content_mask
/sensel_morph/pressure      frame_id width height bit_depth max_value blob
/sensel_morph/pressure_rle  frame_id width height bit_depth max_value uncompressed_bytes rle_blob
/sensel_morph/labels        frame_id width height blob
/sensel_morph/labels_rle    frame_id width height uncompressed_bytes rle_blob
/sensel_morph/contacts      frame_id count
/sensel_morph/contact_summary frame_id count x_avg y_avg x_force_avg y_force_avg force_total force_avg area_avg spread avg_weighted_distance
/sensel_morph/contact       frame_id id state x_mm y_mm force area orientation major_axis minor_axis delta_x delta_y delta_force delta_area min_x min_y max_x max_y peak_x peak_y peak_force
/sensel_morph/accelerometer frame_id x y z x_g y_g z_g
/sensel_morph/sync          frame_id
```

`--rle` is strict: when enabled, pressure and label rasters are always sent on
the `_rle` addresses and are never mixed with raw raster frames in the same run.
The RLE payload is byte-level `[count, value]` pairs, with `count` in `1..255`.
This can significantly improve throughput for sparse pressure frames and label
buffers. Some dense/noisy `uint16` pressure frames may compress poorly, so raw
mode remains the default.

Use `--profile` to print transmitter timing and byte-count averages on exit.
This is useful for distinguishing device frame rate, Python decode/expansion
cost, OSC transmission cost, and receiver-side display cost.

Large rasters are chunked by default with `--chunk-size 4096`, which sends
`/pressure/start` + `/pressure/chunk` or `/labels/start` + `/labels/chunk`
messages instead of one too-large UDP datagram. In RLE mode, the corresponding
chunked addresses are `/pressure_rle/start`, `/pressure_rle/chunk`,
`/labels_rle/start`, and `/labels_rle/chunk`. Use `--chunk-size 0` only when you
know the receiver and network stack can accept the full raster as one OSC blob.

Warning: unchunked high-resolution raster OSC can exceed the maximum UDP
datagram size. The theoretical IPv4 UDP payload limit is `65,507` bytes, but
real systems are often much lower; macOS commonly uses `9,216` bytes for
`net.inet.udp.maxdgram`. A `185 x 105` uint8 pressure image is already `19,425`
bytes before OSC overhead, and uint16 is twice that, so high-resolution pressure
should normally stay chunked.

Contact compatibility modes are optional:

- `--compat morphosc`: also sends the older `morphosc`-style `/num_contacts`,
  `/spread`, `/total_force`, `/lifecycle`, `/x_position`, `/y_position`, and
  `/force` messages.
- `--compat senselosc`: also sends the richer `senselosc`-style `/contactAvg`,
  `/contact`, `/contactDelta`, `/contactBB`, `/contactPeak`, and `/sync`
  messages.

When `--contacts` is enabled, the broadcaster writes the internal contact mask
`0x0f` before scanning and restores the previous device value on exit. This asks
the firmware to include every contact packet field, but it is not exposed as a
user option: the public contact stream should report the best known geometry,
not ask users to choose among firmware ellipse/bbox/peak variants we already
know are inferior or laggy.

---

## Live WebSocket Broadcast

`sensel_morph_ws` is the browser-facing transmitter. It reads the Morph over USB
CDC serial, decodes frames locally, and broadcasts binary raster messages plus
JSON metadata/contact messages. Pressure and label rasters are `uint8`. The
default URL is:

```txt
ws://127.0.0.1:1561
```

Typical p5.js test command:

```sh
sensel_morph_ws --pressure --pressure-res high
```

Useful options:

```sh
sensel_morph_ws --pressure --pressure-res med --fps-limit 30
sensel_morph_ws --pressure --labels --contacts --pressure-res high
sensel_morph_ws --pressure --labels --contacts --pressure-res high --rle
sensel_morph_ws --labels --label-res low
sensel_morph_ws --contacts
sensel_morph_ws --pressure --pressure-res high --calibration python/tools/calibration_2044B8374E33.json
sensel_morph_ws --pressure --pressure-res low --pressure-normalize
```

For convenience, running `sensel_morph_ws` with no stream flags still enables
pressure output, matching the first p5 pressure demo. As soon as any stream flag
is supplied, only the requested streams are transmitted. Accelerometer JSON is
always requested and sent when present.

Arguments:

- `--host`: WebSocket bind host, default `127.0.0.1`.
- `--port`: WebSocket TCP port, default `1561`.
- `--device`: serial device path; default is the first Morph `/dev/cu.usbmodem*`.
- `--pressure`: send the pressure raster stream.
- `--labels`: send the label-ID raster stream.
- `--contacts`: send `/sensel_morph` contact JSON messages.
- `--pressure-res`: `high` = `185x105`, `med` = `93x53`, `low` = `47x27`.
- `--label-res`: `high` = `185x105`, `med` = `93x53`, `low` = `47x27`; default
  follows `--pressure-res`.
- `--fps-limit`: optional maximum send rate; `0` means unbounded.
- `--calibration`: optional matching `calibration_<serial>.json`.
- `--force-scale`: divide pressure values before uint8 packing.
- `--pressure-normalize`: per-frame normalize max pressure to `255`; off by default.
- `--rle`: send pressure and label raster payloads as byte-RLE `[count,value]`
  pairs. When enabled, every raster packet is compressed.
- `--accelerometer`: accepted for API symmetry; accelerometer output is always
  enabled.

The WebSocket server sends pressure and label frames as binary messages. Each
message is a 32-byte little-endian header followed by row-major `uint8` pixels:

```txt
magic        4s   "SMPR" pressure, "SMLB" labels
version      u8   1
kind         u8   1 = pressure, 2 = labels
header_size  u16  32
frame_id     u32
timestamp    u32
width        u16
height       u16
bit_depth    u8   8
flags        u8   bit0 calibrated, bit1 normalized, bit2 RLE
reserved     u16  0
payload_len  u32  compressed length when RLE is set, else width * height
max_value    f32  maximum pre-clamped pressure value
payload      u8[payload_len]
```

The RLE payload is the same simple byte-level format used by the OSC path:
`[count, value]` pairs with `count` in `1..255`. Width and height in the header
give the expected uncompressed raster size.

It also sends JSON text messages. Browser receivers should check whether
`event.data` is a string or an `ArrayBuffer`.

Status messages have `type:"status"`. Accelerometer and contact messages use
the native `/sensel_morph` addresses in an object shape:

```txt
{"address":"/sensel_morph/accelerometer", ...}
{"address":"/sensel_morph/contacts", ...}
{"address":"/sensel_morph/contact_summary", ...}
{"address":"/sensel_morph/contact", ...}
```

Contact objects include firmware fields plus normalized browser-friendly
coordinates such as `x_norm`, `y_norm`, `peak_x_norm`, and `peak_y_norm`.
They also include bbox fields, peak coordinates, and delta vectors. When
`--pressure`, `--labels`, and `--contacts` are enabled together,
`sensel_morph_ws` uses fresh label-mask raster-derived ellipse, bbox, and peak
estimates. Contacts-only mode remains lightweight and uses the firmware contact
geometry.

---

## Resolution Behavior

Pressure and label streams are plain row-major raster blobs:

- `--pressure-res high`: `185 x 105`
- `--pressure-res med`: `93 x 53`
- `--pressure-res low`: `47 x 27`
- `--pressure-res` also selects the device scan detail. `high` requests the
  high-detail scan. `med` and `low` request the faster medium-detail scan.
  `med` uses the recovered Sensel interpolation kernel to avoid fake 2x nearest
  blocks from the `47 x 27` source grid; `low` sends that source grid directly.
  There is no separate public `--scan-detail` option.
- `--pressure-normalize`: optional uint8 display mode that scales each frame so
  its maximum pressure becomes `255`
- `--calibration <json-or-dir>`: apply a calibrator JSON to outgoing pressure
  frames after strict serial-number validation. If a directory is passed, it
  looks for `calibration_<connected_serial>.json`.

Resolution behavior:

| Flag | Device scan detail | Decoded source grid | Interpolated pressure | Raster output | Notes |
|---|---|---:|---:|---:|---|
| `--pressure-res high` | high | `93 x 53` | `185 x 105` | `185 x 105` | best quality, slowest |
| `--pressure-res med` | medium | `47 x 27` | `185 x 105` | `93 x 53` | good compromise, much faster than high |
| `--pressure-res low` | medium | `47 x 27` | none | `47 x 27` | fastest and smallest pressure stream |

`--scan-detail` has been removed. `--pressure-res` is the single resolution
control. `med` is not fake nearest-upsampled `47 x 27`; it uses the recovered
Sensel interpolation kernel first, then outputs `93 x 53`.

Empirical July 2026 profile results on serial `2044B8374E33`, with
`--pressure-type uint8 --rle --profile` and accelerometer enabled by default:

| Resolution | Observed transmit rate | Mean frame read | Mean loop | Mean pressure bytes sent |
|---|---:|---:|---:|---:|
| `high` | `28.0 fps` | `27.020 ms` | `35.689 ms` | `8067.5` |
| `med` | `41.1 fps` | `15.171 ms` | `24.314 ms` | `3194.8` |
| `low` | `40.4 fps` | `21.939 ms` | `24.726 ms` | `598.3` |

The main speed break is the device scan detail, not the OSC blob size alone:
`med` and `low` both use medium scan detail and are currently similar in frame
rate, while `high` asks the device to scan and report more source data.


---

## LED Strip Control

`sensel_morph_led` controls the Morph's 24 white LED strip over the same USB CDC
register protocol. The strip is per-LED brightness only; the separate RGB status
LED remains firmware-controlled.

**Note:** the LED display is a fun diversion, but live LED animation uses the
same acknowledged serial register protocol as frame capture. As a result, when used inside `sensel_morph_osc`, *LED control can essentially halve your OSC data transmission rate*. Do not enable LED modes when maximum data throughput matters.

Basic command:

```sh
sensel_morph_led info
```

Pressure-responsive modes run until Ctrl-C:

```sh
sensel_morph_led mode glow
sensel_morph_led mode pulse
sensel_morph_led mode kitt
sensel_morph_led mode twinkle
sensel_morph_led mode columns
sensel_morph_led mode meter
```

The pressure response is logarithmic:

```text
response = log1p(total_pressure / pressure_floor) / log1p(pressure_ref / pressure_floor)
```

Defaults are `pressure_floor=50` and `pressure_ref=15000`, matching observed
brush/finger/hand pressure totals. Use `--pressure-ref` or `--pressure-floor`
to retune a mode. Per-column modes also use `--column-threshold`, default `50`,
to suppress no-touch noise.

`glow` uses a 10% brightness floor. `pulse` additionally shapes each column's
pressure response with `--pulse-response-gamma`, default `2.5`, so modest
pressure changes the pulse speed less while firm pressure still reaches the top
speed. `meter` uses a fixed `2.5` gamma curve, so ghost noise and light touches
consume less of the strip. `twinkle` uses a low idle firing probability, then
adds global total-pressure response rather than per-column pressure.

`mode`, `sensel_morph_osc`, and the capture utilities clear all LEDs during
shutdown.

---