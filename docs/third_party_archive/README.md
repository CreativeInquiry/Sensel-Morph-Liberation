# Third-party archive

This directory preserves a small set of third-party files that were useful to the Sensel Morph reverse-engineering work.

*These are archival reference copies, not active dependencies of the current tools.*
They retain their original upstream copyright and license terms; the top-level
MIT license for this repository does not relicense these third-party files.

---

## Contents

- `decompression_resources/`: Sensel decompression header and Windows binary library files used as primary references while identifying the force-frame decompression path. Originally from `github.com/sensel/PD-objects`.
- `sensel-api/`: selected files from Sensel's official C API, mainly protocol, register, and type definitions.
- `sensel-api-arduino/`: selected Arduino API headers, useful as a compact cross-check of register names and protocol details.
- `senselosc/`: source files from the older senselosc OSC bridge, retained for OSC compatibility reference.
- `morphosc/`: source files from the older morphosc OSC bridge, retained for OSC compatibility reference.
- `morph-docs/`: selected Markdown API documentation from the Morph documentation repository.
- `C74-Max-Examples/`: selected Max patch showing historical Morph force-image usage.

