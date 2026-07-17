# Sensel Morph Liberation


![sensel_loop.gif](images/sensel_loop_1.gif)<br />*Get Sensel Morph touchpad data over OSC in 2026+ —* ***without*** *the obsolete and unsupported Sensel SDK.*

By Golan Levin, July 2026

Status: working raw-pressure decoder and visualizer, July 2026.

This repo is a reverse-engineering workspace and tool collection for liberating
the Sensel Morph's full pressure image. The practical result so far is that
Morph pressure frames can be captured over USB CDC serial, decompressed without
Sensel's closed library, and expanded into the Morph's `185 x 105` force image.

The pressure stream is compressed, not encrypted.

#### Contents: 

* [Overview](#overview)
* [Processing Utilities for Sensel Morph](#processing-utilities-for-sensel-morph)
* [Python Utilities for Sensel Morph](#python-utilities-for-sensel-morph)
* [Other Documentation](#other-documentation)


---

## Overview

![sensel_morph_channels.png](images/sensel_morph_channels.png)

We can:

- Communicate with the Morph over `/dev/cu.usbmodem*` using the same register
  protocol used by the public SDK.
- Request raw pressure frames with `frame_content_control = 0x01`.
- Decode the compressed pressure payload into a low-resolution pressure grid.
- Expand high-detail pressure frames into `185 x 105` force images.
- Decode pressure+labels mixed frames for label reverse engineering.
- Play captured recordings in a Processing sketch.


---

## Processing Utilities for Sensel Morph

![sensel_loop_2](images/sensel_loop_2.gif)

[Processing](https://processing.org/) is a popular open-source, Java-based toolkit for creative coding. [**This repository presents six Processing utilities**](processing/README.md) for viewing, transmitting, calibrating, recording and replaying data from the Sensel Morph touchpad. 

It's likely that the Processing app you want is [**`sensel_morph_osc_transmitter`**](processing/sensel_morph_osc_transmitter/README.md), which connects directly to the Sensel Morph over USB serial, decodes live frames, displays the pressure/label/contact layers, and emits this device data over [OSC](https://en.wikipedia.org/wiki/Open_Sound_Control). However, there are also other Processing apps which transmit Sensel Morph data over [WebSockets](https://en.wikipedia.org/wiki/WebSocket) or [Syphon](https://syphon.info/), as summarized [here](processing/README.md) and in the table below. 

Unless otherwise noted, these apps are compatible with Processing 4.5.5. To minimize dependencies, these apps use the Processing's built-in Serial Library to communicate with the device, and native Java UDP code for OSC; they do not depend on `oscP5`, the Sensel SDK, or any of the Python code in this repository.


### Summary of Processing Apps

| Processing App | Intended Use |
|---|---|
| [**sensel_morph_osc_transmitter**](processing/sensel_morph_osc_transmitter/README.md) | Standalone live USB reader and OSC broadcaster. Features local display, performance recording, and playback. | 
| [**sensel_morph_osc_receiver**](processing/sensel_morph_osc_receiver/README.md) | Live OSC monitor (receiver) and viewer. |
| [**sensel_morph_syphon_osc_transmitter**](processing/sensel_morph_syphon_osc_transmitter/README.md) | Standalone live USB reader, which transmits device data over both OSC and Syphon (for audiovisual tools like TouchDesigner). Note: compatible up to Processing 4.3 owing to the Syphon library. | 
| [**sensel_morph_websocket_transmitter**](processing/sensel_morph_websocket_transmitter/README.md) | Standalone live USB read and WebSocket transmitter (for browser-based/p5 clients). | 
| [**sensel_morph_capture_viewer**](processing/sensel_morph_capture_viewer/README.md) | Offline recording viewer. |
| [**sensel_morph_osc_calibrator**](processing/sensel_morph_osc_calibrator/README.md) | Creates optional pressure calibration files to compensate for fixed noise patterns. |

![processing_to_touchdesigner_via_syphon.png](images/processing_to_touchdesigner_via_syphon.png)


---

## Python Utilities for Sensel Morph

![sensel_lights.gif](images/sensel_lights.gif)

The Python command-line tools live in [`python/`](python/). For installation instructons, command summaries, recording examples, and notes about behavior and implementation, please see: [**`python/README.md`**](python/README.md)


### Summary of Python Tools

| Python Program | Intended Use |
|---|---|
| [`sensel_morph_osc.py`](python/tools/sensel_morph_osc.py) | Live USB reader and OSC broadcaster. |
| [`sensel_morph_ws.py`](python/tools/sensel_morph_ws.py) | Live USB reader and WebSocket broadcaster. |
| [`sensel_morph_capture_session.py`](python/tools/morph_capture_session.py) | Performance recording tool; produces JSONL/JSON. |
| [`sensel_morph_led.py`](python/tools/sensel_morph_led.py) | Controls the Morph's 24 white LEDs. |


---

## Other Documentation

This repository aims to be a thorough resource for anyone hacking the Sensel Morph in the future. To that end, more information than necessary is available in the following documents:

- [**Communications Protocol**](docs/communications_protocol.md): low-level
  USB CDC/register protocol, frame formats, compression, labels, contacts,
  accelerometer, and LED notes.
- [**Implementation Details**](docs/implementation_details.md): lower-level
  notes on contact geometry, calibration application, serial-number handling,
  and contact summary semantics.
- [**Reverse Engineering Lab Notes**](docs/narrative.md): how the raw pressure, labels, contact geometry, accelerometer, calibration, and output bridges were recovered.
- [**Prior Art Survey**](docs/prior_art_survey.md): annotated technical
  bibliography of SDKs, examples, OSC bridges, decompression resources, and
  other prior work.
- [**Third Party Archive**](docs/third_party_archive/): small archival copies
  of third-party files that were important to the reverse-engineering work.

---
