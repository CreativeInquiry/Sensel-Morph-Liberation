# sensel_morph_osc_transmitter

Processing 4 sketch that connects directly to the Sensel Morph over USB CDC
serial, decodes live frames, displays the pressure/label/contact layers, and
emits OSC using the native `/sensel_morph/...` protocol.

It does not require `oscP5`; OSC packets are built with Java UDP classes. It
also does not drive the Morph LED strip.

## Settings

Edit `data/settings.txt`. The file is tab-separated:

```text
key<TAB>value
```

Important keys:

- `host`, `port`: OSC destination. Default port is `1560`.
- `device`: optional serial port path. Empty means first `usbmodem` port.
- `serial_number`: optional override. Empty means inferred from the usbmodem
  port name when possible.
- `pressure`, `labels`, `contacts`: `true` or `false`.
- `pressure_res`: `high`, `med`, or `low`.
- `pressure_type`: `uint8` or `uint16`.
- `local_view`: bitmask for local display layers (`1` pressure, `2` labels,
  `4` contacts; `7` means all layers).
- `use_calibration`: `true` or `false`; only active when a matching
  `data/calibration_<serial_number>.json` file is present.
- `rle`: `true` sends pressure/label rasters on `_rle` OSC addresses only.
- `chunk_size`: byte size for OSC blob chunking; `4096` is conservative.
- `compat`: empty, `morphosc`, `senselosc`, or `morphosc,senselosc`.
- `source`: `device` for the live Morph, or `recording` to replay a raw
  capture file through the same OSC/display pipeline.
- `recording_file`: file to replay when `source` is `recording`. Relative paths
  are resolved first inside `data/`, then inside the sketch folder.
- `recording_loop`: `true` loops playback at the end of the recording.
- `recording_timing`: `realtime`, `fixed_fps`, or `as_fast_as_possible`.
- `playback_policy`: hidden playback tradeoff. `favor_timing` is the default
  and skips recorded frames when necessary to preserve wall-clock timing;
  `favor_data` sends every recorded frame, even if playback falls behind.
- `recording_fps`: playback rate used by `fixed_fps`, and as a fallback when
  timestamp timing is unavailable.
- `record_enabled`: `true` starts recording immediately in live device mode.
- `record_dir`: recording output directory relative to `data/`; default
  `recordings` means `data/recordings/`.

Accelerometer data is always requested and transmitted. When contacts are
enabled along with pressure and labels, the sketch uses raster-derived ellipses,
bounding boxes, and peaks where available. Contacts-only mode remains lightweight
and uses the firmware contact geometry.

## Keys

- `1`: pressure
- `2`: labels
- `3`: pressure + labels
- `4`: contacts
- `7`: all layers
- `s`: toggle pressure display sampling
- `c`: toggle calibration, when a matching calibration file is available
- `p`: save `screenshots/sensel_<frameCount>_pressure.png`,
  `screenshots/sensel_<frameCount>_labels.png`, and
  `screenshots/sensel_<frameCount>_contacts.png`
- Spacebar: pause/resume playback when `source` is `recording`
- Left/Right arrows: while playback is paused, step backward/forward by one
  recorded frame, wrapping around the recording boundaries
- `R`: toggle between live device capture and recording playback
- `h`: toggle HUD and right-side settings UI

The right-side settings UI can change `pressure`, `labels`, `contacts`,
`pressure_res`, `pressure_type`, `rle`, calibration, compatibility modes, and
display sampling while the sketch is running. The `mode` radio buttons select
live device capture, active raw recording, or recording playback; unavailable
choices are grayed out when no Morph or no recordings are present. Choosing
`playback` from the UI selects the newest recording in `data/recordings/`. The
`recording` mode starts raw packet recording; switch back to `live device` to
stop recording and save the file. The left-side `local view`
checkboxes control the same layer bitmask as keys `1` through `7`. The `use
calibration` checkbox is shown only when `data/calibration_<serial_number>.json`
exists and its `device_serial` matches the connected Morph. Stream and
OSC-format changes briefly stop and restart device scanning, then send a fresh
`/sensel_morph/status`. Use `Save settings.txt` to persist the current UI values
to `data/settings.txt`.

During playback, the `pressure`, `labels`, and `contacts` stream checkboxes
only change which channels are transmitted over OSC; they do not restart or
interrupt the recording playback.

During playback, `pressure_res` is locked to the resolution recorded in the
capture file and other resolution choices are grayed out. `pressure_type`,
`rle`, `morphosc`, and `senselosc` remain selectable because they only affect
output formatting. They do not restart playback; if playback is paused, the held
frame is refreshed and retransmitted with the new output format immediately.

## Raw Recording and Replay

Choose `recording` in the right-side mode controls to start writing raw Morph
frame packets to JSONL files in `data/recordings/`. Switch back to `live device`
to stop recording and save the file. These are raw capture files, not processed
OSC dumps: pressure decoding, calibration, ellipse fitting, and output
formatting are all recomputed when a recording is replayed.

To replay a recording without a connected Morph, set:

```text
source	recording
recording_file	recordings/<filename>.jsonl
recording_loop	true
recording_timing	realtime
playback_policy	favor_timing
```

Then run the sketch normally. The OSC output and local display use the same code
path as live capture. Playback loops by default and shows a 10-pixel progress
bar along the bottom of the window; set the hidden `recording_loop` setting to
`false` to stop at the end instead. If `source` is `recording` and
`recording_file` is blank, the newest `.jsonl` or `.json` file in
`data/recordings/` is selected. Older Python-style JSON captures with a
top-level `frames` array can also be used as `recording_file`.

When playback is paused, the current frame is still retransmitted repeatedly
using `fps_limit` when set, otherwise `recording_fps`. Downstream receivers
therefore continue to see fresh data for the held frame rather than a silent or
broken stream.
