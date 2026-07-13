# Processing Sketches

This folder contains six Processing apps for viewing, transmitting, calibrating, recording
and replaying Sensel Morph data. Open each sketch by launching the `.pde` file
inside its same-named folder.

| Sketch | Primary use | Receives | Sends / Publishes | Notes |
|---|---|---|---|---|
| [`sensel_morph_osc_transmitter`](sensel_morph_osc_transmitter/) | Standalone live device-to-OSC transmitter with local display, calibration, raw recording, and replay. | Raw Morph frames over USB CDC | OSC over UDP, default port `1560`. A [Processing OSC receiver](sensel_morph_osc_receiver/) is provided. | Best Processing starting point for non-Syphon OSC workflows. Uses native Java UDP, not `oscP5`. |
| [`sensel_morph_osc_receiver`](sensel_morph_osc_receiver/) | Live OSC monitor/viewer. | OSC from one of the Processing transmitters, or from the `sensel_morph_osc` Python tool | Nothing; display-only. | Displays pressure, labels, contacts, summary values, and an accelerometer slab view. Useful for checking transmitter output. |
| [`sensel_morph_syphon_osc_transmitter`](sensel_morph_syphon_osc_transmitter/) | Standalone live transmitter for visual/video tools such as TouchDesigner. | Raw Morph frames over USB CDC | OSC over UDP, plus Syphon buffers for the pressure, labels, and contact information. A [sample TouchDesigner project](../touchdesigner/) is provided. | Requires the Processing Syphon library. **NOTE:** Known-good local setup used **Processing 4.3** because of Syphon native-library architecture constraints. |
| [`sensel_morph_websocket_transmitter`](sensel_morph_websocket_transmitter/) | Standalone live device-to-WebSocket transmitter for browser/p5.js clients. | Raw Morph frames over USB CDC | WebSocket server, default `ws://127.0.0.1:1561`. [Sample p5.js WebSocket receivers](../p5js/) are provided. | Mirrors the OSC transmitter UI and recorder/replay behavior, but publishes the WebSocket protocol used by the p5.js receivers. |
| [`sensel_morph_osc_calibrator`](sensel_morph_osc_calibrator/) | Build per-device pressure calibration files. | High-res `uint16` pressure OSC from one of the Processing transmitters. | Calibration JSON/TIFF/PFM files used in the Processing transmitters. | Run with an OSC transmitter sending `185 x 105` uint16 pressure. Produces `calibration_<serial>.json` files consumed by transmitters. |
| [`sensel_morph_capture_viewer`](sensel_morph_capture_viewer/) | Offline recording viewer. | `.jsonl` or `.json` recordings created with one of the Processing transmitters, or with the Python-based `sensel_morph_capture_session` | Nothing; display only. | Uses the same display/decoding logic as the transmitters, but only replays files from `data/recordings/` or a chosen recording path. |


---

## Protocol Families

- **USB CDC serial**: used by the three transmitter sketches to receive data directly
  from the Morph and decode its raw pressure, label, contact, and accelerometer frames.
- **OSC**: used for local UDP communication with Processing, Max, TouchDesigner,
  and other media tools. The native address family is `/sensel_morph/...`.
- **WebSocket**: used for browser clients, especially the included p5.js
  receivers.
- **Syphon**: MacOS graphics-buffer publishing for video tools. Only the
  Syphon+OSC transmitter uses it.


---

## Common Controls

The transmitter/viewer sketches share a similar display model and keypress register:

- `1`-`7`: choose local pressure/labels/contacts layer combinations.
- `s`: toggle pressure display sampling where available.
- `p`: save pressure, labels, and contacts screenshots where available.
- Spacebar: pause/resume recording playback.
- Left/Right arrows: step frames while playback is paused.
- `h`: hide/show HUD and controls.

Most transmitter settings live in each sketch's `data/settings.txt` file. Those
files are tab-separated and can also be updated from the in-sketch UI.
