# Survey of Prior Sensel Morph APIs

![sensel_morph_channels](../images/sensel_morph_channels.png)

Golan Levin, July 2026

---

## Overview

**This document is an annotated technical bibliography of third-party Sensel Morph APIs, wrappers, and other engineering work that left useful breadcrumbs for this project.** 
It is meant to acknowledge the public projects, addon libraries, examples, wrappers, 
and shims that helped us recover the Morph's raw pressure frames, and to explain 
where our tools intentionally preserve compatibility with older Morph software ecosystems.

Of special note: our interface, `sensel_morph_osc`, **preserves compatibility** with 
[`morphosc`](https://github.com/ctsexton/morphosc) and [`senselosc`](https://github.com/tai-studio/senselosc) so that older Morph patches and tools can continue to
run against a newly liberated data source.

*The dates below are approximate project-era notes based on the local clones and
public repository history we inspected. They should be read as context, not as a
complete publication history.*

#### Contents

* [Current Protocol Summary](#current-protocol-summary)
* [Compatibility Context](#compatibility-context)
* [High-Value Sources](#high-value-sources)
* [Project Catalog](#project-catalog)
* [Protocol Breadcrumbs From Prior Work](#protocol-breadcrumbs-from-prior-work)
* [Archival Reference Copies of Third-Party Resources](#archival-reference-copies-of-third-party-resources)
* [Bottom Line](#bottom-line)

---

## Current Protocol Summary

*For complete documentation of our understanding of the Sensel Morph's communications protocols, please see [communications_protocol.md](communications_protocol.md).*

The Morph exposes a `185 x 105` uint16 pressure sensor. Our connected device confirmed
this from the `SENSEL_REG_SENSOR_NUM_COLS` and `SENSEL_REG_SENSOR_NUM_ROWS`
registers.

The useful low-level path is USB CDC serial, using the same register-style
framing visible in Sensel's C and Arduino sources: fixed register reads/writes,
variable-size frame reads, additive checksums, and frame payloads beginning with
a content mask, rolling counter, and timestamp. 

The main types of data produced by the Sensel Morph are: 

* **Pressure**, which describes the amount of force across the Morph's dense grid of sensors, in the form of a compressed 185x105 image buffer.
* **Labels**, in the form of a 1-channel image whose pixels indicate the shape and temporally-coherent tracking IDs of distinct connected regions of pressure.
* **Contacts**, describing fingertip presses, in the form of geometric information like ellipse centers, dimensions, orientation, velocity, etc. 
* **Other global/status information**, such as (x,y,z) accelerations, total force, etc. 

The official SDK can request (compressed) pressure and label frames, but its public source delegates their decompression to the closed-source, and apparently abandoned, `LibSenselDecompress` library. Through binary disassembly, we reimplemented this missing path in order to decompress the prize: the raw pressure and label buffers. *Pressure* is a small custom RLE over compressed grids (`93 x 53` at high detail, `47 x 27` at medium detail); custom four-tap interpolation then expands it to `185 x 105`. *Labels* use a separate RLE over categorical contact IDs and should be displayed or expanded with nearest-neighbor sampling.

*Contacts* remain useful for compatibility and quick interaction data, but we discovered that the
firmware's contact ellipses lag the current pressure/label rasters by one frame (~1/30 sec.).
Our live tools compute fresher pressure-weighted ellipses when pressure, labels,
and contacts are all available.

---

## Compatibility Context

Most earlier creative-coding projects exposed Morph contacts rather than raw
pressure frames. That does not make them unimportant: many older patches, apps,
and shims expect those contact schemas. `sensel_morph_osc` therefore supports
compatibility output for two important predecessors:

- `--compat morphosc`: emits the older [`morphosc`](https://github.com/ctsexton/morphosc)-style contact messages.
- `--compat senselosc`: emits the richer [`senselosc`](https://github.com/tai-studio/senselosc)-style contact messages,
  including summary/average-style fields.

This project is therefore doing two things at once: keeping the hardware useful
after Sensel's software disappeared, and keeping older Morph-dependent software
reachable by preserving compatible OSC output where practical.


---

## High-Value Sources

| Project | Era | Language / Platform | Why it mattered |
|---|---:|---|---|
| Official Sensel API | c.2016-2022 | C, Python ctypes, C#; macOS/Linux/Windows | Primary source for register constants, frame framing, content masks, contact parsing, units, and the closed decompression call boundary. |
| Official Morph Docs | c.2016-2022 | Markdown/API docs | Confirms public semantics: `185 x 105` force image, labels, scan detail, Developer Cable baud rate, and expected units. |
| Sensel Arduino API | c.2018 | Arduino C++ / UART | Compact independent reference for the same register protocol over the Developer/Hacker Cable. |
| `sensel_decompress.h` and binaries in PD objects | c.2021 | C header + binary libraries | Identified the decompression API boundary and provided binary artifacts for disassembly. |
| `senselosc` | c.2025 | C++17 OSC bridge | Important OSC prior art; we preserve compatibility with its contact-oriented output style. |
| `morphosc` | c.2020 | C OSC bridge | Earlier command-line OSC bridge; we preserve compatibility with its simpler contact messages. |
| Sensel Morph Linux | c.2026 | Python + vendored LibSensel, Linux | Modern proof that users still need Morph tooling, and that open-source users still hit the missing decompressor wall. |

---

## Project Catalog

### `sensel/sensel-api`

URL: <https://github.com/sensel/sensel-api>  
Archived excerpts: `third_party_archive/sensel-api`

Sensel's official public SDK, active in roughly the Morph product era
(c.2016-2022), is the most important prior source. It contains the C core
library, Python ctypes wrapper, C# wrapper, platform serial backends, examples,
register names, contact parser, and unit-scaling conventions.

For us, its value was not that it solved raw pressure recovery. It did not:
public source calls `senselDecompressFrame(...)`, but the decompressor itself is
absent. The SDK nevertheless shows how to open the device, set frame-content
bits for pressure/labels/contacts/accelerometer, read variable-size frames from
`SENSEL_REG_SCAN_READ_FRAME`, parse contacts, and interpret many registers.

#### Relevant details

- Language/platform: C core library, Python ctypes wrapper, C# wrapper;
  macOS, Linux, Windows.
- License metadata: MIT for the public core source.
- Last known update: 2022-05-12.
- Reuse in this project: foundation for [`communications_protocol.md`](communications_protocol.md), Python CDC access,
  register names, frame-content masks, contact parsing, and unit conventions.

---

### `sensel/morph-docs`

URL: <https://github.com/sensel/morph-docs>  
Published docs: <https://sensel.github.io/morph-docs/>  
Archived excerpts: `third_party_archive/morph-docs`

Sensel's official Morph documentation, roughly c.2016-2022, gives the public
meaning of the API rather than the byte-level implementation. It is useful
because it says what the data is supposed to mean once decoded.

The docs confirm the Morph force image as `185 x 105`, describe labels and
contact IDs, discuss scan detail and frame-rate tradeoffs, document the
Developer/Hacker Cable at 115200 baud, and give expected force/coordinate
semantics. They did not explain the compression format, but they helped us know
what a successful decoder should produce.

#### Relevant details

- Language/platform: Markdown/MkDocs plus generated API HTML.
- Last known update: 2022-05-12.
- Reuse in this project: expected sensor dimensions, label/contact vocabulary,
  scan-detail concepts, and unit expectations. Highly relevant for dimensions and semantics of pressure buffer extraction; not a decompression source.

---

### `sensel/sensel-api-arduino`

URL: <https://github.com/sensel/sensel-api-arduino>  
Archived excerpts: `third_party_archive/sensel-api-arduino`

Sensel's Arduino library, c.2018, targets the Morph Developer/Hacker Cable over
UART. It is a small, readable implementation of register reads/writes and frame
reads without the complexity of the full desktop SDK.

It only demonstrates contact frames, not raw pressure decompression. Its
importance is that it independently confirms the same command bytes used over
USB CDC: fixed read starts with `0x81`, fixed write starts with `0x01`, frame
read uses register `0x26`, and response ack constants include `PT_READ_ACK = 1`,
`PT_RVS_ACK = 3`, and `PT_WRITE_ACK = 5`.

#### Relevant details

- Language/platform: Arduino C++; Arduino Mega/Due via Morph Developer/Hacker
  Cable UART.
- License metadata: MIT.
- Local clone last update: 2018-02-14.
- Reuse in this project: compact cross-check that UART and USB CDC share the
  same core register protocol. For the pressure frame, highly relevant for protocol framing; no pressure decompression.

---

### `tai-studio/senselosc`

URL: <https://github.com/tai-studio/senselosc>  
Archived excerpts: `third_party_archive/senselosc`

`senselosc` is a C++17 desktop OSC bridge for Morph contacts, by Till/Tai Studio
from the local repository history, with a last known activity in 2025. 
It depends on the system-installed Sensel API rather than implementing the
USB protocol itself.

The `senselosc` project is interesting because it provides a rich contact OSC vocabulary:
contact averages, individual contacts, deltas, bounding boxes, peaks, and sync
messages. It does not retrieve or decompress raw pressure frames, but its OSC
schema is exactly the kind of older integration we want to keep alive.

#### Relevant details

- Language/platform: C++17 desktop app, depends on Sensel API.
- License metadata: MIT.
- Last known update: 2025-10-21.
- Compatibility: `sensel_morph_osc --compat senselosc` sends compatible
  contact-oriented messages for receivers built around this style.

---

### `ctsexton/morphosc`

URL: <https://github.com/ctsexton/morphosc>  
Archived excerpts: `third_party_archive/morphosc`

`morphosc` is Cam Sexton's command-line OSC broadcaster for Morph contact data,
with last known activity in 2020. It is a simpler, earlier bridge than
`senselosc`, and includes useful desktop/Linux packaging details such as udev
rules.

It appears to be SDK/contact oriented rather than a raw pressure project. Its
importance here is compatibility: existing patches or applications may already
expect its OSC message style, so our OSC broadcaster can emit morphosc-flavored
messages as a deliberate preservation feature.

#### Relevant details

- Language/platform: C desktop command-line app, CMake.
- License metadata: GPL-3.0.
- Last known update: 2020-03-10.
- Compatibility: `sensel_morph_osc --compat morphosc` sends compatible
  contact-oriented messages.

---

### `sensel/PD-objects`

URL: <https://github.com/sensel/PD-objects>  
Archived excerpts: `third_party_archive/decompression_resources`

Sensel's objects for the [Pure Data](https://puredata.info/) audio toolkit, c.2021, 
expose Morph contact data and LED brightness control to audio/visual patching environments. 
They are useful both as creative-coding prior art and as a source of Sensel binary artifacts.

The README explicitly frames pressure-image output as future work; the external
itself focuses on contact points. For our reverse engineering, the critically important 
find was `sensel_decompress.h` plus Windows `LibSenselDecompress` binaries, which helped
identify the decompression API boundary and supplied a binary for disassembly. The files we used were: 

* [The header we used](third_party_archive/decompression_resources/sensel_decompress.h) (PD-objects/sensel-win-msys-include/sensel_decompress.h)
* [The Windows binary we referenced](third_party_archive/decompression_resources/LibSenselDecompress.dll) (PD-objects/LibSenselDecompress.dll)
* [A related import library](third_party_archive/decompression_resources/LibSenselDecompress.lib) (PD-objects/LibSenselDecompress.lib)


#### Relevant details

- Language/platform: C external for Pure Data / Purr Data; macOS, Linux,
  Windows.
- License metadata: README states GPLv3; includes Sensel binary libraries.
- Last known update: 2021-05-17.
- Reuse in this project: audio-thread architecture ideas, LED-control precedent,
  `sensel_decompress.h`, and binary decompression artifacts. For the pressure frame, highly relevant as a decompression-boundary clue, but not as an open implementation.

---

### `sensel/C74-Max-Examples`

URL: <https://github.com/sensel/C74-Max-Examples>  
Archived excerpts: `third_party_archive/C74-Max-Examples`

Sensel's Max/MSP examples, c.2021, show how the Morph was intended to fit into
Cycling '74 Max workflows: MPE, contact drawing, gesture recognition, and Jitter
examples. They are examples rather than a full protocol implementation.

The most interesting clue is that the docs mention `jit.sensel` providing a
bitmap force image for Jitter. This suggests Sensel had a Max package capable of
surfacing pressure imagery, but this repository contains examples, not the
external implementation we would need to inspect.

#### Relevant details

- Language/platform: Max/MSP patches for Cycling '74 Max.
- License metadata: MIT.
- Last known update: 2021-11-15.
- Reuse in this project: output/visualization ideas and a lead for possible
  future binary inspection.

---

### `sensel/sensel-api-processing`

URL: <https://github.com/sensel/sensel-api-processing>  

Sensel's Processing wrapper, c.2017, is an early desktop creative-coding bridge
for Morph contact ellipses. It is useful mainly as a historical reference for how Sensel
expected Processing users to draw contact data.

It does not expose the full raw pressure image; the examples visualize contact
pressure rather than decoded pressure rasters. That makes it low-value for the
protocol itself, but still relevant to our Processing sketches as design and API
context.

#### Relevant details

- Language/platform: Processing 2-era desktop code.
- License metadata: Apache-2.0.
- Last known update: 2017-02-11.
- Reuse in this project: historical Processing conventions and simple contact
  display precedent.

---

### `laserpilot/ofxSenselMorph`

URL: <https://github.com/laserpilot/ofxSenselMorph>  

Blair Neal's `ofxSenselMorph`, c.2016, is an early openFrameworks addon based on
old/prototype Sensel API behavior. It predates much of the later SDK ecosystem
and is valuable mainly as a historical snapshot.

It contains old serial/contact structures, but no clear raw force-frame path.
Its usefulness is in showing how early creative-coding users integrated the
Morph, and as a possible reference if old protocol behavior needs comparison.

#### Relevant details

- Language/platform: C++ / openFrameworks, macOS, openFrameworks 0.8-era.
- License metadata: MIT.
- Last known update: 2016-08-01.
- Reuse in this project: historical context and early contact-rendering
  conventions; made us aware of the LED control possibilities.

---

### `keijiro/SenselExamples`

URL: <https://github.com/keijiro/SenselExamples>  

Keijiro Takahashi's Unity examples, c.2018, demonstrate Morph-driven visual
interaction in Unity. They are creative examples rather than protocol research.

The project likely wraps the official SDK and does not appear to include
independent USB, pressure, or decompression work. It remains useful as visual
inspiration and as evidence of the kinds of real-time graphics workflows Morph
users wanted.

#### Relevant details

- Language/platform: C# / Unity, Windows-oriented Unity 2018 era.
- License metadata: none obvious in first pass.
- Last known update: 2018-07-04.
- Reuse in this project: visual inspiration only.

---

### `davidroeca/sensel-morph-linux`

URL: <https://github.com/davidroeca/sensel-morph-linux>  

David Roeca's `sensel-morph-linux`, c.2026, is a modern Python/Linux toolkit for
keeping the Morph useful after the official software stack aged out. It includes
tools for info, monitoring, visualization, tablet bridging, MIDI, config, and
recording.

It vendors/builds a no-pressure `libsensel.so` and explicitly omits
`LibSenselDecompress`, which made it a useful contemporary confirmation that the
closed pressure decompressor remained the main blocker. It did not solve raw
pressure recovery, but its architecture and test style are useful references for
contact-layer tooling.

#### Relevant details

- Language/platform: Python with vendored LibSensel C source; Linux.
- License metadata: MIT.
- Last known update: 2026-04-16.
- Reuse in this project: architecture, CLI, tests, and Linux packaging ideas.

---

## Protocol Breadcrumbs From Prior Work

The most important protocol clues collected from these projects were:

- Register reads use `0x81, reg, size`; register writes use `0x01, reg, size`
  followed by payload and additive checksum.
- Variable-size frame reads use `SENSEL_REG_SCAN_READ_FRAME` (`0x26`) with size
  `0`.
- Fixed read responses include ack, register, 16-bit response size, payload,
  and additive checksum over the payload.
- Frame payloads begin with a content bit mask, rolling frame counter, and
  32-bit timestamp.
- Contacts are parseable from public source; pressure and labels were handed to
  `senselDecompressFrame`.
- Relevant registers include sensor dimensions (`0x10`, `0x12`), compression
  metadata (`0x1c`), scan detail (`0x23`), frame content control (`0x24`), scan
  enable (`0x25`), scan read frame (`0x26`), and supported frame content
  (`0x28`).

---

## Archival Reference Copies of Third-Party Resources

[This directory](third_party_archive) preserves a small set of third-party files that were useful to the Sensel Morph reverse-engineering work. *Note that these are archival reference copies, not active dependencies of the current tools.*

- [`decompression_resources/`](third_party_archive/decompression_resources): Sensel decompression header and Windows binary library files used as primary references while identifying the force-frame decompression path. Originally from `github.com/sensel/PD-objects`.
- [`sensel-api/`](third_party_archive/sensel-api): selected files from Sensel's official C API, mainly protocol, register, and type definitions.
- [`sensel-api-arduino/`](third_party_archive/sensel-api-arduino): selected Arduino API headers, useful as a compact cross-check of register names and protocol details.
- [`senselosc/`](third_party_archive/senselosc): source files from the older `senselosc` OSC bridge, retained for OSC compatibility reference.
- [`morphosc/`](third_party_archive/morphosc): source files from the older `morphosc` OSC bridge, retained for OSC compatibility reference.
- [`morph-docs/`](third_party_archive/morph-docs): selected Markdown API documentation from the Morph documentation repository.
- [`C74-Max-Examples/`](third_party_archive/C74-Max-Examples): selected Max patch showing historical Morph force-image usage.


---

## Bottom Line

**No surveyed prior project published an open-source decompressor for the Morph’s raw pressure or label frame streams.**

The official SDK and examples showed how to ask for pressure, labels, contacts, and
accelerometer frames, and the third-party projects showed useful integration
patterns, but the missing decompression step had to be recovered separately. 
That said, these projects matter: they provided the register map, frame structure,
unit conventions, creative-coding patterns, binary clues, and OSC schemas that
made this work practical. 
