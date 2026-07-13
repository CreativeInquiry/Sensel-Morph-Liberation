# sensel_morph_capture_viewer

Processing sketch for inspecting raw Sensel Morph recordings offline. It does
not connect to the device and does not transmit OSC, WebSocket, or Syphon data.

Put `.jsonl` or legacy `.json` recordings in:

```text
data/recordings/
```

The sketch loads the newest recording in that folder at startup. Use `load
recording` to choose another `.jsonl` or `.json` file.

Controls:

- `1`-`7`: choose pressure, labels, contacts, or layer combinations
- `s`: toggle pressure display sampling between linear and nearest
- Spacebar: pause/resume playback
- Left/Right arrows: step one frame while paused
- `h`: hide/show the HUD and controls

The decoder, contact rendering, layer compositing, and JSONL/JSON recording
loader are derived from `sensel_morph_osc_transmitter` so offline playback
matches the transmitter playback view.
