# sensel_morph_capture_viewer

This is a Processing sketch for inspecting raw Sensel Morph recordings offline. It does not connect to the device and does not transmit OSC, WebSocket, Syphon, or other data. The recordings it plays can be created using any of the following apps provided in this repository:
* `sensel_morph_osc_transmitter` (Processing)* `sensel_morph_syphon_osc_transmitter` (Processing)* `sensel_morph_websocket_transmitter` (Processing)
* `sensel_morph_capture_session` (Python)

---

## Operation

Put `.jsonl` or legacy `.json` recordings in:

```text
data/recordings/
```

Then open this sketch in Processing 4.x:

```text
processing/sensel_morph_capture_viewer/sensel_morph_capture_viewer.pde
```


The sketch auto-loads the newest `.jsonl` or legacy `.json` recording from its
`data/recordings/` folder, decodes the raw packets in Java/Processing, and
displays pressure, labels, and contacts on a `1280 x 720` canvas. Use the `load recording` button to choose another `.jsonl` or `.json` file.

The UI is intentionally small: `local view` checkboxes choose pressure, labels,
and contacts; `display sampling` switches pressure upscaling between nearest and
linear. 

--- 

## Controls:

- `1`-`7`: choose pressure, labels, contacts, or layer combinations
- `s`: toggle pressure display sampling between linear and nearest
- Spacebar: pause/resume playback
- Left/Right arrows: step one frame while paused
- `h`: hide/show the HUD and controls

The decoder, contact rendering, layer compositing, and JSONL/JSON recording
loader are derived from `sensel_morph_osc_transmitter` so offline playback
matches the transmitter playback view.
