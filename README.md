# Sensel Morph Liberation

Status: working raw-pressure decoder and visualizer, July 2026.

This repo is a reverse-engineering workspace and tool collection for liberating
the Sensel Morph's full pressure image. The practical result so far is that
Morph pressure frames can be captured over USB CDC serial, decompressed without
Sensel's closed library, and expanded into the Morph's `185 x 105` force image.

The pressure stream is compressed, not encrypted.

![processing_to_touchdesigner_via_syphon.png](images/processing_to_touchdesigner_via_syphon.png)

## Current Result

We can:

- Communicate with the Morph over `/dev/cu.usbmodem*` using the same register
  protocol used by the public SDK.
- Request raw pressure frames with `frame_content_control = 0x01`.
- Decode the compressed pressure payload into a low-resolution pressure grid.
- Expand high-detail pressure frames into `185 x 105` force images.
- Decode pressure+labels mixed frames for label reverse engineering.
- Play captured recordings in a Processing sketch.

Important files:

- `python/tools/morph_capture.py`: one-shot CDC pressure capture.
- `python/tools/morph_capture_session.py`: stdin-driven capture session used for the
  recorded experiments.
- `python/tools/decode_pressure.py`: offline pressure decoder, metrics writer, and PGM
  preview/PNG generator.
- `python/tools/sensel_morph_osc.py`: source-checkout runner for the live OSC
  broadcaster installed as `sensel_morph_osc`.
- `processing/sensel_morph_capture_viewer/`: offline Processing viewer for raw
  JSONL and legacy JSON recordings.
- `processing/sensel_morph_osc_receiver/`: Processing receiver for live OSC
  from `sensel_morph_osc`.
- `processing/sensel_morph_osc_transmitter/`: Processing device-to-OSC
  transmitter, with native Java serial and OSC code.
- `processing/sensel_morph_websocket_transmitter/`: Processing device-to-WebSocket
  transmitter, compatible with the p5.js WebSocket receivers.
- `processing/sensel_morph_syphon_osc_transmitter/`: Processing
  device-to-OSC transmitter that also publishes pressure, labels, and contacts
  as Syphon buffers.
- `processing/sensel_morph_osc_calibrator/`: Processing pressure calibration
  tool for dark/no-touch and brush-pass per-pixel maps.
- `docs/communications_protocol.md`: measured CDC/register protocol notes.
- `docs/narrative.md`: readable account of how the raw data was recovered.
- `docs/prior_art_survey.md`: annotated prior-art survey and source inventory.

## Documentation

- [docs/narrative.md](docs/narrative.md): how the raw pressure, labels, contact
  geometry, accelerometer, calibration, and output bridges were recovered.
- [docs/communications_protocol.md](docs/communications_protocol.md): low-level
  USB CDC/register protocol, frame formats, compression, labels, contacts,
  accelerometer, and LED notes.
- [docs/prior_art_survey.md](docs/prior_art_survey.md): annotated technical
  bibliography of SDKs, examples, OSC bridges, decompression resources, and
  other prior work.
- [docs/third_party_archive/](docs/third_party_archive/): small archival copies
  of third-party files that were important to the reverse-engineering work.

## Python Commands

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
3.10+ interpreter.

This installs command-line tools without requiring you to create or activate a
project venv:

- `sensel_morph_osc`: live USB CDC reader and OSC broadcaster.
- `sensel_morph_ws`: live USB CDC reader and WebSocket broadcaster for browser sketches.
- `sensel_morph_led`: white LED strip control and pressure-responsive modes.
- `sensel_decode_pressure`: offline pressure decoder and PNG/CSV/JSON preview writer.
- `sensel_morph_capture`: one-shot capture utility.
- `sensel_morph_capture_session`: stdin-driven capture session utility.

`sensel_morph_capture_session` now writes Processing-compatible raw JSONL
recordings by default, with filenames like
`sensel_recording_20260712_153000.jsonl`. Each file has one header object and
then one raw packet object per line, matching the recorder/playback format used
by the Processing transmitters. From `/Users/gl/Desktop/sensel/python`, a full
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

For example, from `/Users/gl/Desktop/sensel/python`, the same full capture in
legacy JSON format is:

```sh
printf '{"label":"full_test","duration":10,"max_frames":600,"out_dir":"../captures/full","frame_content":15,"contacts_mask":15}\nquit\n' | sensel_morph_capture_session --output-format json
```

Within a running session, a single stdin command can also override the default:

```json
{"label":"test","duration":10,"max_frames":300,"frame_content":15,"contacts_mask":15,"output_format":"json"}
```

For local development and tests:

```sh
cd python
python -m pip install -e ".[dev]"
python -m pytest
```

## LED Strip Control

`sensel_morph_led` controls the Morph's 24 white LED strip over the same USB CDC
register protocol. The strip is per-LED brightness only; the separate RGB status
LED remains firmware-controlled.

Warning: the LED display is a fun diversion, but live LED animation uses the
same acknowledged serial register protocol as frame capture. When used inside
`sensel_morph_osc`, it can essentially halve your OSC data transmission rate.
Do not enable LED modes when maximum data throughput matters.

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

## Running the Decoder

```sh
sensel_decode_pressure captures/pressure \
  --out-dir analysis/pressure_preview \
  --expanded-dir analysis/pressure_expanded_preview \
  --png-dir analysis/pressure_png_preview \
  --csv analysis/pressure_metrics.csv \
  --json analysis/pressure_decode_summary.json
```

Use `--force-scale 8` to report SDK-style force units for this device. Without
that option, decoded values are kept in raw RLE units.

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

Contact geometry depends on which streams are available:

| Requested streams | Ellipse source | Bbox source | Peak source | Notes |
|---|---|---|---|---|
| contacts only | firmware | firmware | firmware | Fastest path. Uses the device's contact packet as-is. The firmware ellipses are known to be one frame stale. |
| contacts + labels | firmware | current label blob when available, otherwise firmware | firmware | Labels improve visualization bboxes, but without pressure there is no pressure-weighted ellipse or peak to compute. |
| contacts + pressure | hybrid pressure+bbox | firmware | firmware | For isolated firmware bboxes, the transmitter computes a fresh pressure-weighted ellipse from pressure pixels inside the bbox. If bboxes overlap, it falls back to firmware geometry. |
| contacts + labels + pressure | label-mask pressure moments | current label blob when available, otherwise firmware | current pressure peak inside label blob | Best-quality path. Computes fresh ellipses, bboxes, and peaks from the current pressure/label rasters. |

The full pressure+labels+contacts path replaces the firmware's one-frame-lagged
ellipse geometry by default. For each current contact label, the tools compute
pressure-weighted second moments over the decoded pressure grid, emit the
pressure-weighted centroid as `x_mm`/`y_mm`, and emit an oriented ellipse with
axes `4 * sqrt(covariance eigenvalue)`.

The pressure+contacts path uses a newer "hybrid" computation. Firmware bounding
boxes appear to be contemporary with the pressure frame, even though firmware
ellipses are stale. When a contact's firmware bbox does not overlap any other
contact bbox, the tools compute the same pressure-weighted moment ellipse inside
that bbox. When bboxes overlap, there is not enough segmentation information to
separate contacts robustly without labels, so the tools intentionally fall back
to firmware ellipses for those complex spots.

The Morph firmware can also emit dimensionless contact bboxes, where
`min_x == max_x` and/or `min_y == max_y`, even while the pressure image contains
a visible contact blob and the contact has nonzero force. The hybrid path treats
these point-like bboxes as location seeds, expands them by a small pressure-grid
neighborhood, and fits the pressure distribution inside that local window.

The raw JSON/JSONL capture tools still preserve device packets literally. The
fresh ellipse, bbox, and peak values are computed by transmitters/viewers at
decode time.

The transmitter detects the connected USB serial number with `pyserial`
`serial.tools.list_ports` and includes it in `/sensel_morph/status`. The current
Processing calibrator uses that value to tag its output files. Status is
re-sent periodically so receivers opened after the transmitter still learn the
serial number. Calibration loading is strict: the JSON `device_serial` must
match the detected USB serial, and a filename like
`calibration_2044B8374E33.json` must also match. Calibration applies:

```text
source_dark, source_gain = kernel_fit(dark_185x105, light_185x105, coverage_185x105)
corrected_source = max(0, raw_source - source_dark) * source_gain
corrected_185x105 = sensel_4tap_expand(corrected_source)
```

This is a kernel-aware pre-interpolation correction: calibrated expanded pixels
inform neighboring compressed source cells through the same four-tap Sensel
interpolation matrices used by the decoder. Calibration JSONs without a `light`
map fall back to the older expanded-pixel correction:
`corrected = max(0, expanded_raw - dark) * gain`.

`/sensel_morph/contact_summary` is the native aggregate contact message. It
reports unweighted and force-weighted contact centers normalized to `0..1`,
total and average force, average area, `spread` as average distance from the
unweighted center in millimeters, and `avg_weighted_distance` as average distance
from the force-weighted center in millimeters. When raster-derived geometry is
available, these summary values use the same fresh contact centers as
`/sensel_morph/contact`. Compatibility outputs keep their original millimeter
coordinate ranges.

## Processing Capture Viewer

Open this sketch in Processing:

```text
processing/sensel_morph_capture_viewer/sensel_morph_capture_viewer.pde
```

The sketch auto-loads the newest `.jsonl` or legacy `.json` recording from its
`data/recordings/` folder, decodes the raw packets in Java/Processing, and
displays pressure, labels, and contacts on a `1280 x 720` canvas.

Contact ellipses are drawn directly on the main Processing canvas as unfilled,
rotated `ellipse()` outlines. They use the same color palette as the labels
channel, with full alpha, so contact geometry can be inspected independently of
the low-resolution pressure/label rasters. The viewer uses the same decoder,
fresh raster-derived contact geometry, layer compositing, and display-sampling
behavior as the Processing OSC transmitter playback path, but it does not
connect to the device or transmit data.

The UI is intentionally small: `local view` checkboxes choose pressure, labels,
and contacts; `display sampling` switches pressure upscaling between nearest and
linear; and `load recording` opens a file picker rooted in `data/recordings/`.

Controls:

- `1`: pressure
- `2`: labels
- `3`: pressure + labels
- `4`: contacts
- `5`: pressure + contacts
- `6`: labels + contacts
- `7`: pressure + labels + contacts
- Space: pause/play
- Left/Right: step frames
- Up/Down: change playback speed
- `r`: restart
- Return or `n` / `p`: next/previous JSON recording
- `h`: toggle legend/progress display
- Drag the bottom progress bar: scrub

## Processing OSC Receiver

Open this sketch in Processing:

```text
processing/sensel_morph_osc_receiver/sensel_morph_osc_receiver.pde
```

It listens on UDP port `1560` for the native `/sensel_morph/...` OSC messages
from `sensel_morph_osc`. It uses native Java UDP/OSC parsing, not oscP5.
The HUD reports frame-message receive FPS and the observed raster transport
(`raw` or `rle`). Display modes match the recording player:

- `1`: pressure
- `2`: labels
- `3`: pressure + labels
- `4`: contacts
- `5`: pressure + contacts
- `6`: labels + contacts
- `7`: pressure + labels + contacts
- `h`: toggle HUD
- `s`: toggle nearest/linear texture sampling
- `n`: toggle absolute/normalized pressure display; absolute is the default
- `p`: save separate pressure, labels, and contacts PNGs under `screenshots/`

Pressure display is not frame-normalized by default. For `uint16` OSC pressure,
the receiver clamps each value to `0..255` for grayscale display while preserving
the full 16-bit values in the OSC payload. The vanilla OSC receiver intentionally
keeps display sampling to nearest/linear; shader-based bicubic interpolation is
used by `processing/sensel_morph_syphon_osc_transmitter/`.

Contact overlays draw the reported ellipse center/axes, bounding box, peak-force
crosshair, and `delta_x`/`delta_y` vector when those optional contact fields are
present. In the live OSC receiver, bounding boxes are snapped to the matching
received label raster when labels and contacts share a frame ID, so the box
contains the visible label blob even when labels are transmitted at an upsampled
resolution. Contact ID labels are drawn over a small translucent black disk for
legibility.

The OSC receiver sketch also exposes user-facing getter functions for reuse in
custom Processing sketches:

- `getPressureImage()` / `getRawPressureImage()`: current pressure `PImage` at
  the received raster resolution
- `getPressureBytes()` / `getRawPressureBytes()`: raw current pressure bytes;
  use `getPressureValueAt(x, y)` for decoded `uint8`/`uint16` cell values
- `getPressureFrameId()`, `getPressureWidth()`, `getPressureHeight()`,
  `getPressureBitDepth()`, and `getPressureMax()`: pressure metadata
- `getLabelImage()` / `getRawLabelImage()`: colorized current label `PImage`
  at the received raster resolution
- `getLabelIds()` / `getRawLabelIds()`: raw label IDs; use
  `getLabelIdAt(x, y)` for one label cell
- `getLabelFrameId()`, `getLabelWidth()`, and `getLabelHeight()`: label
  metadata
- `getContacts()` / `getContactObjects()`: friendly `SenselContactInfo[]`
  snapshots with `id`, `position`/`xy`, `screenPosition`, `force`, `area`,
  `orientation`, `majorAxis`, `minorAxis`, `delta`/`dxdy`, `deltaScreen`,
  `axisScreenWidth`, `axisScreenHeight`, `peak`/`peakXY`, `peakScreen`,
  `peakForce`, `hasPeak`, `bbox`, and `firmwareBBox`; `hasPeak` means the peak
  coordinate is drawable, while `peakForce` remains available separately
- `getContact(id)`: one `SenselContactInfo` by contact ID, or `null`
- `getContactsFrameId()` and `getContactCount()`: contact metadata
- `getRawContacts()`: lower-level `SenselContact[]` records as received from
  OSC
- `getBoundingBoxes()`: visualization-correct `SenselBoundingBox[]` values,
  preferring matching label-raster bounds and falling back to firmware bbox
  fields
- `getBoundingBox(id)`: one visualization-correct bbox by contact ID, or
  `null`
- `getFrameId()` and `getReceiveFps()`: current stream metadata
- `getDeviceSerial()`: USB serial number reported by `/sensel_morph/status`,
  or `unknown`

Getter bboxes are in screen/display coordinates for the current sketch canvas.
Each `SenselBoundingBox` has `x/y/w/h`, `width/height`, `left/top/right/bottom`,
`min`, `max`, `center`, `source`, and `fromLabelRaster`. Pass a target
`PGraphics` to `getContacts(pg)`, `getContactObjects(pg)`, `getBoundingBoxes(pg)`,
or `getBoundingBox(id, pg)` when drawing into another buffer.

---

## Processing OSC Transmitters

Two standalone Processing sketches can connect directly to the Morph and transmit OSC:

```text
processing/sensel_morph_osc_transmitter/sensel_morph_osc_transmitter.pde
processing/sensel_morph_syphon_osc_transmitter/sensel_morph_syphon_osc_transmitter.pde
```

Both sketches use Processing's [Serial Library](https://processing.org/reference/libraries/serial/index.html) for USB CDC register I/O and
native Java UDP code for OSC; they do not use `oscP5`, the Sensel SDK, or the
Python transmitter. 

Each sketch loads a tab-separated `data/settings.txt`:

```text
host	127.0.0.1
port	1560
pressure	true
pressure_res	med
pressure_type	uint8
labels	true
contacts	true
rle	true
compat	
```

Supported settings include `device`, `serial_number`, `pressure`,
`pressure_res` (`high`, `med`, `low`), `pressure_type` (`uint8`, `uint16`),
`use_calibration`, `labels`, `contacts`, `rle`, `chunk_size`, `compat`
(`morphosc`, `senselosc`, or both), `force_scale`, `fps_limit`,
`read_timeout_ms`, and `accel_counts_per_g`. Accelerometer data is always
requested and transmitted.

In `sensel_morph_osc_transmitter`, press `h` to show/hide the HUD and
right-side settings UI. The UI can change streams, resolution, pressure type,
RLE, calibration, compatibility modes, and display sampling while the app is
running. Calibration is offered only when a matching
`data/calibration_<serial_number>.json` file exists. Stream and format changes
briefly reconfigure device scanning without restarting the Processing sketch.
The `Save settings.txt` button writes the current UI values to
`data/settings.txt`.

The Syphon transmitter additionally requires the Processing Syphon library
(`codeanticode.syphon.*`) and publishes three sources: `Sensel Morph Pressure`,
`Sensel Morph Labels`, and `Sensel Morph Contacts`. Its pressure buffer defaults
to bicubic shader interpolation for prettier graphics output; label buffers are
always displayed nearest-neighbor so label IDs remain discrete.


---

## Processing WebSocket Transmitter

This sketch connects directly to the Morph and serves the same WebSocket
protocol as `sensel_morph_ws`, without Python:

```text
processing/sensel_morph_websocket_transmitter/sensel_morph_websocket_transmitter.pde
```

It listens by default at:

```text
ws://127.0.0.1:1561
```

It is compatible with both p5.js receivers in `p5js/`. Pressure and label
rasters are always sent as `uint8`; `rle true` uses the same byte-RLE payload
format as the Python WebSocket sender. Contacts-only mode remains lightweight
and uses firmware contact geometry. With pressure plus contacts but no labels,
the sketch uses the hybrid pressure+bbox ellipse path for isolated contacts and
falls back to firmware ellipses for overlaps. When pressure, labels, and
contacts are all enabled, the sketch uses current label-mask raster-derived
ellipses, bboxes, and peaks.


---

## Processing OSC Calibrator

Open this sketch in Processing:

```text
processing/sensel_morph_osc_calibrator/sensel_morph_osc_calibrator.pde
```

It listens on UDP port `1560` for high-resolution pressure OSC from
`sensel_morph_osc`. Recommended transmitter command:

```sh
sensel_morph_osc --pressure --pressure-res high --pressure-type uint16
```

Controls:

- `0`: capture a 10-second dark/no-touch baseline average
- `1`..`9`: select one of nine brush-pass slots
- Space: clear the current dark/pass slot
- Return: compute the calibration and save files
- `+` / `-`: manually raise/lower the coverage threshold
- `h`: toggle HUD

Each brush-pass slot accumulates the per-pixel maximum pressure seen while that
slot is selected. The 10-second dark/no-touch pass tracks both average and peak
no-touch values, then derives the coverage threshold from the 99th percentile of
`dark_max - dark_average`, with a small bit-depth-aware floor: `1` for `uint8`
and `8` for `uint16`. Return subtracts the dark map if present, ignores
corrected values below that threshold, then computes one selected per-pixel
aggregate across covered brush-pass slots. The current calibrator default is the
average of the middle five values from the nine brush passes. Pixels never
covered above threshold are marked
uncalibrated and get gain `1.0`.

The calibrator HUD reports `current_max` for the latest incoming pressure frame
and `scene_global_max` for the selected scene: dark/no-touch max for scene `0`,
or brush-pass max for scenes `1..9`.

Saved calibrations are written under:

```text
processing/sensel_morph_osc_calibrator/calibrations/<timestamp>/
```

Output files:

- `dark_185x105_u16.tif`: 16-bit unsigned dark/no-touch baseline
- `pass_01_max_185x105_u16.tif` etc.: raw 16-bit unsigned brush-pass maxima,
  one file for each populated pass slot
- `light_dark_subtracted_185x105_u16.tif`: 16-bit unsigned selected light map
- `gain_185x105_u16_gain32768.tif`: 16-bit unsigned gain map, where `32768`
  means gain `1.0`
- `coverage_mask_185x105_u16.tif`: 16-bit mask; `65535` means calibrated
- `gain_185x105_f32.tif`: 32-bit float, single-channel gain map
- `gain_185x105_f32.pfm`: 32-bit float grayscale PFM gain map
- `calibration_<serial_number>.json`: plain JSON serial, dark, light, coverage,
  and gain values for `sensel_morph_osc --calibration`

The TIFF writer is implemented directly in Processing/Java and does not depend
on ImageIO plugins. Files are uncompressed little-endian grayscale TIFFs, intended
to be Photoshop-compatible. The float TIFF uses TIFF `SampleFormat=IEEEFP`;
PFM is also emitted because it is a simple viewable/storable `32f1c` image
format, though not every image editor handles it.

The calibrator stores the detected serial number in the JSON `device_serial`
field, in each TIFF's `ImageDescription`, and in the PFM header comment.
The dark/no-touch preview is displayed on an absolute pressure scale rather than
normalizing the brightest noise pixel to white.

The calibration model follows camera flat-field practice:

```text
corrected = (raw - dark) * gain
gain[pixel] = target / (flat[pixel] - dark[pixel])
```

For the Morph, the brush pass substitutes for a true uniform flat field:

```text
corrected_pressure[pixel] = max(0, raw_pressure[pixel] - dark_offset[pixel]) * gain[pixel]
gain[pixel] = target_response / measured_brush_response[pixel]
```

Raw pass maxima are saved so the calibration can be recomputed later with a
different threshold, statistic, or gain clamp.
