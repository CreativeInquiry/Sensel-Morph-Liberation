# sensel_morph_ws_receiver_p5v1

p5.js 1.11.13 browser receiver for `sensel_morph_ws` or the Processing
`sensel_morph_websocket_transmitter` sketch.

Run the Python transmitter:

```sh
sensel_morph_ws --pressure --pressure-res high --fps-limit 30
```

Or run the Processing transmitter:

```text
processing/sensel_morph_websocket_transmitter/sensel_morph_websocket_transmitter.pde
```

To include labels and contact overlays:

```sh
sensel_morph_ws --pressure --labels --contacts --pressure-res high --fps-limit 30
```

To use byte-RLE compression for pressure and label rasters:

```sh
sensel_morph_ws --pressure --labels --contacts --pressure-res high --rle
```

To send only contact JSON plus accelerometer JSON:

```sh
sensel_morph_ws --contacts
```

Then serve this folder and open `index.html` in a browser:

```sh
cd /Users/gl/Desktop/sensel/p5js/sensel_morph_ws_receiver_p5v1
python3 -m http.server 8000
```

Open:

```txt
http://127.0.0.1:8000
```

The sketch connects to:

```txt
ws://127.0.0.1:1561
```

It receives binary `SMPR` pressure frames and `SMLB` label frames, decodes raw
or RLE row-major `uint8` payloads into p5 images, and draws them in a fixed
viewport below the header area. It also receives JSON accelerometer messages.
When `--contacts` is enabled, it draws colored ellipse, bbox, peak, vector, and
ID overlays using the same contact-label color order as the Processing
sketches.

If a stream is disabled in the transmitter, the receiver clears that channel as
soon as the next status message arrives. If a stream silently stops sending
fresh packets, the channel is cleared after a short timeout so stale pressure,
label, or contact data does not freeze onscreen.
