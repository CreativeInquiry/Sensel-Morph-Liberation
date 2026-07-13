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
- [docs/implementation_details.md](docs/implementation_details.md): lower-level
  notes on contact geometry, calibration application, serial-number handling,
  and contact summary semantics.
- [docs/third_party_archive/](docs/third_party_archive/): small archival copies
  of third-party files that were important to the reverse-engineering work.

## Python Tools

The Python command-line tools live in [`python/`](python/). See
[`python/README.md`](python/README.md) for installation, command summaries,
recording examples, LED control, offline decoding, live OSC broadcast, live
WebSocket broadcast, resolution behavior, RLE/chunking notes, compatibility
modes, and profiling notes.

Installed commands include:

- `sensel_morph_osc`: live USB CDC reader and OSC broadcaster.
- `sensel_morph_ws`: live USB CDC reader and WebSocket broadcaster for browser sketches.
- `sensel_morph_led`: white LED strip control and pressure-responsive modes.
- `sensel_decode_pressure`: offline pressure decoder and PNG/CSV/JSON preview writer.
- `sensel_morph_capture_session`: current JSONL/JSON recording tool.
- `sensel_morph_capture`: older one-shot/diagnostic capture utility.

## Implementation Details

Detailed implementation notes have been moved to
[`docs/implementation_details.md`](docs/implementation_details.md), including
contact geometry source selection, raster-derived ellipses, the pressure+bbox
hybrid contact path, dimensionless firmware bboxes, serial-number handling,
calibration application, and `/sensel_morph/contact_summary` semantics.

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
