# sensel_morph_osc_receiver

This is a Processing sketch that receives Sensel Morph data over OSC, and displays it. The app does not connect directly to the device and does not make any outgoing transmissions. It can receive OSC data from any of the following transmitter apps in this repository: 
* `sensel_morph_osc_transmitter` (Processing)* `sensel_morph_syphon_osc_transmitter` (Processing)
* `sensel_morph_osc` (Python)

---

## Operation

Open this sketch in Processing:

```text
processing/sensel_morph_osc_receiver/sensel_morph_osc_receiver.pde
```

The sketch listens on UDP port `1560` for the native `/sensel_morph/...` OSC messages
from the transmitter app. It uses native Java UDP/OSC parsing, not oscP5.
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


---

## API for Making Custom Apps

The OSC receiver sketch also exposes user-facing getter functions for reuse in
your own custom Processing sketches:

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
