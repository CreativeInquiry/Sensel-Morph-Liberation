# sensel_morph_websocket_transmitter

This is a Processing 4 sketch that connects directly to the Sensel Morph over USB CDC
serial, decodes live frames, displays the pressure/label/contact layers, and
serves the device data over WebSockets.

The default WebSocket endpoint is:

```text
ws://127.0.0.1:1561
```

It is intended to work with the included p5.js receivers:

- [`p5js/sensel_morph_ws_receiver_p5v1`](../../p5js/sensel_morph_ws_receiver_p5v1)
- [`p5js/sensel_morph_ws_receiver_p5v2`](../../p5js/sensel_morph_ws_receiver_p5v2)


(The app uses the same WebSocket message family and raster header as the
Python-based `sensel_morph_ws` tool in this repository.)

---

## Settings

Edit `data/settings.txt`. The file is tab-separated:

```text
key<TAB>value
```

Important keys:

- `host`, `port`: WebSocket bind address. Default is `127.0.0.1:1561`.
- `device`: optional serial port path. Empty means first `usbmodem` port.
- `serial_number`: optional override. Empty means inferred from the usbmodem
  port name when possible.
- `pressure`, `labels`, `contacts`: `true` or `false`.
- `pressure_res`: `high`, `med`, or `low`.
- `pressure_type`: `uint8`. The disabled `uint16` UI option is shown only as a
  reminder that other non-WebSocket transmitters can use 16-bit pressure.
- `local_view`: bitmask for local display layers (`1` pressure, `2` labels,
  `4` contacts; `7` means all layers).
- `use_calibration`: `true` or `false`; only active when a matching
  `data/calibration_<serial_number>.json` file is present.
- `rle`: `true` sends pressure/label raster bytes as byte-RLE `[count,value]`
  payloads using WS header flag bit `0x04`.
- `force_scale`: divide pressure values before transport packing.
- `fps_limit`: optional frame-rate limit; `0` means unbounded.
- `read_timeout_ms`: serial read timeout.
- `accel_counts_per_g`: accelerometer scale for JSON `*_g` fields.
- `source`: `device` for live capture or `recording` for playback.
- `recording_file`: optional file under `data/recordings/`, or an absolute path.
- `recording_loop`, `recording_timing`, `playback_policy`, `recording_fps`:
  playback behavior controls shared with the OSC transmitter.

All WebSocket raster data in this repository is `uint8`: pressure rasters,
label-ID rasters, the Python `sensel_morph_ws` sender, this Processing
transmitter, and the included p5.js WebSocket receivers all use `bit_depth = 8`.

Accelerometer data is always requested and transmitted. When contacts are
enabled along with pressure and labels, the sketch uses fresh label-mask
raster-derived ellipses, peaks, and bounding boxes where available. With
pressure plus contacts but no labels, it uses the hybrid pressure+bbox ellipse
path for isolated contacts and falls back to firmware ellipses for overlaps.
Contacts-only mode remains lightweight and uses firmware contact geometry.



---

## Protocol

Binary raster messages:

- `SMPR`: pressure raster
- `SMLB`: label raster

Both use the same 32-byte little-endian header layout as the Python
`sensel_morph_ws` program presented in this repository.

```text
magic        4s   "SMPR" or "SMLB"
version      u8   1
kind         u8   1 = pressure, 2 = labels
header_size  u16  32
frame_id     u32
timestamp    u32
width        u16
height       u16
bit_depth    u8   8
flags        u8   bit0 calibrated, bit2 RLE
reserved     u16  0
payload_len  u32
max_value    f32
payload      u8[payload_len]
```

JSON text messages use the same shape as the Python sender:

```text
{"type":"status", ...}
{"address":"/sensel_morph/accelerometer", ...}
{"address":"/sensel_morph/contacts", ...}
{"address":"/sensel_morph/contact_summary", ...}
{"address":"/sensel_morph/contact", ...}
```

---

## Keys

- `1`: pressure
- `2`: labels
- `3`: pressure + labels
- `4`: contacts
- `7`: all layers
- `s`: toggle local pressure display sampling
- `c`: toggle calibration, when a matching calibration file is available
- Spacebar: pause/resume playback when in playback mode
- Left/right arrows: step one frame while playback is paused
- `p`: save `screenshots/sensel_<frameCount>_pressure.png`,
  `screenshots/sensel_<frameCount>_labels.png`, and
  `screenshots/sensel_<frameCount>_contacts.png`
- `h`: toggle HUD and right-side settings UI

The right-side settings UI can change transmit streams, resolution, RLE,
calibration, live/record/playback mode, and recording selection.
The left-side `local view` checkboxes control the same layer bitmask as keys
`1` through `7`, with local display sampling grouped below them. Stream changes
briefly stop and restart device scanning, then send a fresh status message. Use
`Save settings.txt` to persist the current UI values.
