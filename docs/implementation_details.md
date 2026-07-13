# Implementation Details

*This document collects some lower-level implementation notes that are useful for
maintainers and advanced users, but too detailed for the top-level README.*

---

## Contact Geometry Nuances

There are a number of quirks to how the Sensel Morph computes contact geometries (i.e. ellipses). To take one example: the ellipses produced by the device are one frame stale compared to the pressure image. It is possible to compute more accurate/timely ellipses, but doing so requires accessing additional information layers, which can slow down the overall device frame rate. For this reason, in making new utilities to capture and broadcast Sensel Morph data, the computation of contact ellipses depends on which streams are available, as follows: 

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
