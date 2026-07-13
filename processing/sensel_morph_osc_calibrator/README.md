# Sensel Morph OSC Calibrator

This Processing sketch builds per-pixel calibration maps for the Sensel Morph's
`185 x 105` pressure image. It is intended for compensating persistent sensor
element differences: small pressure pixels that consistently read too bright or
too dark regardless of the hand/finger gesture moving across them.

The sketch receives live pressure frames from `sensel_morph_osc` over OSC. It
does not talk to the Morph directly.

The camera-calibration vocabulary for this problem is fixed-pattern noise,
photo-response non-uniformity (PRNU), and flat-field correction / shading
correction. The Morph is not a camera, but the math maps well: estimate
persistent per-pixel offset and sensitivity differences, then compensate them.

## Run It

Start the OSC transmitter from another shell:

```sh
sensel_morph_osc --pressure --pressure-res high --pressure-type uint16
```

Open this sketch in Processing:

```text
processing/sensel_morph_osc_calibrator/sensel_morph_osc_calibrator.pde
```

The sketch listens on UDP port `1560`, matching the default
`sensel_morph_osc` port.

## How To Use It

1. Press `0` and leave the Morph untouched for 10 seconds. This records the
   dark/no-touch baseline.
2. Press `1`, then brush across the Morph with a clean, even, wide brush. The
   selected slot accumulates the maximum value observed at every pressure pixel.
3. Press `2` through `9` for additional brush passes. Use a slightly different
   path or angle when useful.
4. Press Return to compute and save the calibration.

Controls:

- `0`: capture a 10-second dark/no-touch baseline average
- `1`..`9`: select one of nine brush-pass slots
- Space: clear the current dark/pass slot
- Return: compute the calibration and save files
- `+` / `-`: manually raise/lower the coverage threshold
- `h`: toggle HUD

You do not need perfect 100% coverage. Pixels that are never covered above the
threshold are marked uncalibrated and get gain `1.0`.

The HUD shows two pressure maxima while you work:

- `current_max`: highest pressure pixel in the latest incoming frame
- `scene_global_max`: highest pressure pixel accumulated in the selected scene;
  for scene `0` this is the dark/no-touch maximum, and for scenes `1..9` this
  is the brush-pass maximum

## What It Computes

The dark slot stores an average no-touch pressure image. This catches persistent
offset/noise-floor differences.

Each brush slot stores a per-pixel maximum:

```text
pass_max[pixel] = max(pass_max[pixel], incoming_pressure[pixel])
```

When Return is pressed, the sketch:

1. Subtracts the dark map from each brush-pass maximum, if a dark map exists.
2. Ignores corrected values below the coverage threshold, treating them as
   uncovered/noise.
3. Computes one selected per-pixel aggregate across all covered brush passes.
   This is controlled by `CALIBRATION_MIDDLE_COUNT` in the sketch: `1` means
   median, `3` means average the center three sorted values, and `5` means
   average the center five sorted values. The current default is `5`.
4. Computes the global target as the median of all covered aggregate pixels.
5. Computes gain as `target / pixel_value`, clamped to `0.5..2.0`.
6. Leaves uncovered pixels at gain `1.0`.

Median or middle-value averaging is used instead of a straight mean so that one
unusually strong or weak pass has less effect on the final calibration.

The standard camera model is:

```text
corrected = (raw - dark) * gain
gain[pixel] = target / (flat[pixel] - dark[pixel])
```

For the Morph, the brush pass is a practical substitute for a true uniform flat
field:

```text
corrected_pressure[pixel] = max(0, raw_pressure[pixel] - dark_offset[pixel]) * gain[pixel]
gain[pixel] = target_response / measured_brush_response[pixel]
```

The coverage threshold is deduced from the dark/no-touch pass. During the dark
capture, the sketch tracks both average and maximum no-touch values. It computes
`dark_noise_p99` as the 99th percentile of `dark_max - dark_average`, then uses:

```text
coverage_threshold = max(bit_depth_floor, dark_noise_p99 * 3)
```

The bit-depth floor is `1` for `uint8` pressure and `8` for `uint16` pressure.
Use `+` or `-` if a particular brush/device session needs manual adjustment.
Gain clamping prevents under-covered or bad pixels from exploding.

The dark/no-touch preview is shown on an absolute pressure scale, not normalized
per image. This keeps one noisy sensor element from being stretched to white.

## Output Files

Saved calibrations are written under:

```text
processing/sensel_morph_osc_calibrator/calibrations/<timestamp>/
```

Files:

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

The TIFF files are written directly by the sketch, without Java `ImageIO`
plugins. They are uncompressed little-endian grayscale TIFFs. The float TIFF
uses TIFF `SampleFormat=IEEEFP`.

PFM is included because it is a simple single-channel 32-bit float image format.
It is useful for technical pipelines, but editor support is less universal than
TIFF.

The raw pass maxima are intentionally saved, not only the final gain map. That
lets us recompute later with a different threshold, a winsorized mean instead of
median, different gain clamps, or a different treatment of edge coverage.

The detected USB serial number from `/sensel_morph/status` is stored in the JSON
`device_serial` field, in each TIFF's `ImageDescription`, and in the PFM header
comment. If the serial cannot be detected, the files are tagged as `unknown`.
`sensel_morph_osc --calibration` refuses to apply `unknown` or non-matching
calibrations.

## Applying The Calibration Later

The live transmitter applies calibration before expanding the compressed source
pressure grid when the JSON includes a `light` map:

```text
source_dark, source_gain = kernel_fit(dark_185x105, light_185x105, coverage_185x105)
corrected_source = max(0, raw_source - source_dark) * source_gain
corrected_185x105 = sensel_4tap_expand(corrected_source)
```

This lets calibrated expanded pixels inform neighboring compressed source cells
through the same four-tap Sensel interpolation matrices used by the decoder.
Calibration JSONs without a `light` map fall back to expanded-pixel correction:
`corrected = max(0, expanded_raw - dark) * gain`.

The current sketch creates calibration files. `sensel_morph_osc` can apply the
JSON file to live outgoing pressure with `--calibration`; the OSC receiver and
Syphon paths consume the already-corrected pressure stream.

## Useful Confirmations

This procedure is adapted from standard imaging calibration practice, translated
from cameras to the Morph pressure sensor.

- [Flat-field correction](https://en.wikipedia.org/wiki/Flat-field_correction)
  is the general idea: compensate pixel-to-pixel gain differences so a uniform
  input produces a uniform output. The same practice commonly uses per-pixel
  gain plus dark/bias correction, and multiple flat frames can be combined to
  reject non-persistent artifacts.
- [Dark-frame subtraction](https://en.wikipedia.org/wiki/Dark-frame_subtraction)
  is the matching offset/noise-floor idea: capture the sensor with no external
  signal, then subtract that baseline.
- [Fixed-pattern noise](https://en.wikipedia.org/wiki/Fixed-pattern_noise) is
  the visible persistent pattern we are trying to suppress. The relevant camera
  concepts are dark-signal non-uniformity for offsets and photo-response
  non-uniformity for gain differences: temporally stable pixel variation under
  the same stimulus.
- [Photo-response non-uniformity](https://en.wikipedia.org/wiki/Photo_response_non-uniformity)
  describes the per-pixel sensitivity differences that make different sensor
  cells respond differently to the same input. High-end and metrology cameras
  often apply a 2D correction-factor table for this.
- [TIFF](https://en.wikipedia.org/wiki/TIFF) is appropriate for calibration
  maps because it supports arbitrary bit depths and, via extension tags,
  IEEE-754 floating-point samples.
- [Netpbm / PFM](https://en.wikipedia.org/wiki/Netpbm) documents PFM as an
  unofficial four-byte IEEE 754 single-precision floating-point image format;
  `Pf` is the grayscale form.

The Morph-specific compromise is that we cannot easily apply a truly uniform
pressure field to the whole surface at once. The brush-pass method approximates
a flat field by moving a locally uniform stripe across the sensor, accumulating
each pixel's best observed response, and using multiple passes plus a median to
reduce gesture/coverage artifacts.
