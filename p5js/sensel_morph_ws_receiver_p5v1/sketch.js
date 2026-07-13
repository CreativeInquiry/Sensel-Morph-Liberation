/**
 * Sensel Morph WebSocket receiver for p5.js 1.11.13.
 * Golan Levin, July 2026
 *
 * Receives raster and JSON data from `sensel_morph_ws`.
 * Binary raster message shape:
 *
 *   32-byte little-endian header + row-major uint8 pixels
 *
 * Header:
 *   magic "SMPR" pressure or "SMLB" labels,
 *   version, kind, header_size, frame_id, timestamp,
 *   width, height, bit_depth, flags, reserved, payload_len, max_value
 *
 * flags bit 2 means the payload is byte-RLE [count,value] pairs.
 */

const WS_URL = "ws://127.0.0.1:1561";
const MAGIC_PRESSURE = "SMPR";
const MAGIC_LABELS = "SMLB";
const HEADER_SIZE = 32;
const KIND_PRESSURE = 1;
const KIND_LABELS = 2;
const FLAG_CALIBRATED = 0x01;
const FLAG_NORMALIZED = 0x02;
const FLAG_RLE = 0x04;
const RECONNECT_AFTER_MS = 1000;
const UI_FONT = "Arial";
const PRESSURE_DISPLAY_Y = 120;
const PRESSURE_DISPLAY_W = 640;
const PRESSURE_DISPLAY_H = 360;
const ACTIVE_W_MM = 230.0;
const ACTIVE_H_MM = 130.0;
const LABEL_ALPHA = 64;
const DISPLAY_UI_X = 16;
const DISPLAY_UI_Y = 88;
const CHANNEL_TIMEOUT_MS = 2000;

const LABEL_PALETTE = [
  [255, 0, 0], [0, 255, 0], [0, 96, 255], [255, 255, 0],
  [0, 255, 255], [255, 0, 255], [255, 128, 0], [128, 255, 0],
  [0, 128, 255], [255, 0, 128], [128, 255, 255], [255, 128, 255],
  [192, 192, 192], [255, 255, 255], [128, 128, 128], [128, 0, 0],
];

let socket = null;
let wsConnected = false;
let statusText = "ws disconnected";
let setupMillis = 0;

let pressureImage = null;
let pressurePixels = null;
let pressureWidth = 0;
let pressureHeight = 0;
let pressureFrameId = 0;
let pressureTimestamp = 0;
let pressureMaxValue = 0;
let pressureFlags = 0;
let labelsImage = null;
let labelsPixels = null;
let labelsWidth = 0;
let labelsHeight = 0;
let labelsFrameId = 0;
let messageCount = 0;
let binaryCount = 0;
let rxFrameCount = 0;
let lastRxFrameId = null;
let lastMessageMillis = 0;
let lastFrameMillis = 0;
let lastPressureMillis = 0;
let lastLabelsMillis = 0;
let lastContactsMillis = 0;
let rxFps = 0;
let serialNumber = "unknown";
let calibrated = false;
let rleEnabled = false;
let pressureRes = "-";
let pressureAvailable = false;
let labelsAvailable = false;
let contactsAvailable = false;
let displayPressure = true;
let displayLabels = true;
let displayContacts = true;
let contactCount = 0;
let contacts = [];
let contactSummary = null;
let accel = null;
let contactGeometryDirty = true;
let contactGeometryFrameId = null;
let rasterPeaksById = new Map();
let displayUi = null;
let displayLabel = null;
let pressureCheckbox = null;
let labelsCheckbox = null;
let contactsCheckbox = null;

function setup() {
  createCanvas(640, 480);
  pixelDensity(2);
  textFont(UI_FONT);
  frameRate(60);
  setupMillis = millis();
  createDisplayControls();
  updateDisplayControls();
  connectWebSocket();
}

function draw() {
  clearStaleChannels();
  background(50);
  drawHeader();

  push(); 
  translate(0, PRESSURE_DISPLAY_Y);
  drawImageBackdrop();
  drawPressureImage();
  drawLabelsImage();
  drawContacts();
  pop(); 

  drawWsButton();
}

function mousePressed() {
  const button = wsButtonRect();
  if (shouldShowWsButton() && mouseX >= button.x && mouseX <= button.x + button.w && mouseY >= button.y && mouseY <= button.y + button.h) {
    connectWebSocket();
  }
}

/** Create p5 DOM checkboxes for locally showing/hiding received streams. */
function createDisplayControls() {
  displayUi = createDiv();
  displayUi.id("display-controls");
  displayUi.position(DISPLAY_UI_X, DISPLAY_UI_Y);
  displayUi.style("display", "flex");
  displayUi.style("align-items", "center");
  displayUi.style("gap", "10px");
  displayUi.style("font", `10px ${UI_FONT}`);
  displayUi.style("font-size", "10px");
  displayUi.style("color", "#d8d8d8");
  displayUi.style("line-height", "13px");
  displayUi.style("user-select", "none");

  displayLabel = createSpan("Display:");
  displayLabel.addClass("display-control-text");
  displayLabel.parent(displayUi);

  pressureCheckbox = createCheckbox("pressure", displayPressure);
  labelsCheckbox = createCheckbox("labels", displayLabels);
  contactsCheckbox = createCheckbox("contacts", displayContacts);
  pressureCheckbox.addClass("display-control-checkbox");
  labelsCheckbox.addClass("display-control-checkbox");
  contactsCheckbox.addClass("display-control-checkbox");
  pressureCheckbox.parent(displayUi);
  labelsCheckbox.parent(displayUi);
  contactsCheckbox.parent(displayUi);
  styleDisplayControlElement(displayLabel);
  styleDisplayControlElement(pressureCheckbox);
  styleDisplayControlElement(labelsCheckbox);
  styleDisplayControlElement(contactsCheckbox);

  pressureCheckbox.changed(() => {
    displayPressure = pressureCheckbox.checked();
  });
  labelsCheckbox.changed(() => {
    displayLabels = labelsCheckbox.checked();
  });
  contactsCheckbox.changed(() => {
    displayContacts = contactsCheckbox.checked();
  });
}

function updateDisplayControls() {
  updateDisplayCheckbox(pressureCheckbox, pressureAvailable, displayPressure);
  updateDisplayCheckbox(labelsCheckbox, labelsAvailable, displayLabels);
  updateDisplayCheckbox(contactsCheckbox, contactsAvailable, displayContacts);
}

function updateDisplayCheckbox(checkbox, available, checked) {
  if (!checkbox) {
    return;
  }
  styleDisplayControlElement(checkbox);
  checkbox.checked(Boolean(checked));
  const input = checkbox.elt.querySelector("input");
  if (input) {
    input.disabled = !available;
  }
  checkbox.style("opacity", available ? "1.0" : "0.35");
  checkbox.style("pointer-events", available ? "auto" : "none");
}

function styleDisplayControlElement(element) {
  if (!element || !element.elt) {
    return;
  }
  element.style("font", `10px ${UI_FONT}`);
  element.style("font-size", "10px");
  element.style("line-height", "13px");
  element.elt.style.font = `10px ${UI_FONT}`;
  element.elt.style.fontSize = "10px";
  element.elt.style.lineHeight = "13px";
  for (const child of element.elt.querySelectorAll("*")) {
    child.style.font = `10px ${UI_FONT}`;
    child.style.fontSize = "10px";
    child.style.lineHeight = "13px";
  }
}

function setStreamAvailability(pressure, labels, contactsStream) {
  pressureAvailable = Boolean(pressure);
  labelsAvailable = Boolean(labels);
  contactsAvailable = Boolean(contactsStream);
  if (!pressureAvailable) {
    clearPressureChannel();
  }
  if (!labelsAvailable) {
    clearLabelsChannel();
  }
  if (!contactsAvailable) {
    clearContactsChannel();
  }
  updateDisplayControls();
}

function clearStaleChannels() {
  const now = millis();
  let changed = false;
  if (lastPressureMillis > 0 && now - lastPressureMillis > CHANNEL_TIMEOUT_MS) {
    clearPressureChannel();
    changed = true;
  }
  if (lastLabelsMillis > 0 && now - lastLabelsMillis > CHANNEL_TIMEOUT_MS) {
    clearLabelsChannel();
    changed = true;
  }
  if (lastContactsMillis > 0 && now - lastContactsMillis > CHANNEL_TIMEOUT_MS) {
    clearContactsChannel();
    changed = true;
  }
  if (changed) {
    updateDisplayControls();
  }
}

function clearPressureChannel() {
  pressureAvailable = false;
  pressureImage = null;
  pressurePixels = null;
  pressureWidth = 0;
  pressureHeight = 0;
  pressureFrameId = 0;
  pressureTimestamp = 0;
  pressureMaxValue = 0;
  pressureFlags = 0;
  lastPressureMillis = 0;
  markContactGeometryDirty();
}

function clearLabelsChannel() {
  labelsAvailable = false;
  labelsImage = null;
  labelsPixels = null;
  labelsWidth = 0;
  labelsHeight = 0;
  labelsFrameId = 0;
  lastLabelsMillis = 0;
  markContactGeometryDirty();
}

function clearContactsChannel() {
  contactsAvailable = false;
  contactCount = 0;
  contacts = [];
  contactSummary = null;
  lastContactsMillis = 0;
  markContactGeometryDirty();
}

/** Open the browser WebSocket and route binary rasters vs JSON messages. */
function connectWebSocket() {
  closeWebSocket();
  statusText = "connecting";
  const nextSocket = new WebSocket(WS_URL);
  nextSocket.binaryType = "arraybuffer";
  socket = nextSocket;

  nextSocket.onopen = () => {
    if (socket !== nextSocket) {
      return;
    }
    wsConnected = true;
    statusText = `connected ${WS_URL}`;
  };

  nextSocket.onmessage = (event) => {
    if (socket !== nextSocket) {
      return;
    }
    handleWsMessage(event.data);
  };

  nextSocket.onerror = () => {
    if (socket !== nextSocket) {
      return;
    }
    statusText = "ws error";
  };

  nextSocket.onclose = () => {
    if (socket !== nextSocket) {
      return;
    }
    wsConnected = false;
    setStreamAvailability(false, false, false);
    statusText = "ws closed";
  };
}

function closeWebSocket() {
  if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) {
    socket.close();
  }
  socket = null;
  wsConnected = false;
  setStreamAvailability(false, false, false);
}

/** Dispatch one received WebSocket message by payload type. */
function handleWsMessage(data) {
  messageCount += 1;
  lastMessageMillis = millis();

  if (typeof data === "string") {
    handleJsonMessage(data);
    return;
  }

  if (data instanceof ArrayBuffer) {
    handleRasterFrame(data);
    return;
  }

  statusText = "unexpected ws payload";
}

/** Handle status, accelerometer, contact summary, and per-contact JSON. */
function handleJsonMessage(text) {
  let message;
  try {
    message = JSON.parse(text);
  } catch (_error) {
    statusText = "bad status json";
    return;
  }

  if (message.type === "status") {
    serialNumber = message.serial_number || "unknown";
    pressureRes = message.pressure_res || "-";
    calibrated = Boolean(message.calibrated);
    rleEnabled = Boolean(message.rle);
    setStreamAvailability(message.pressure, message.labels, message.contacts);
    statusText = `connected ${WS_URL}`;
    return;
  }

  if (message.address === "/sensel_morph/contacts") {
    contactsAvailable = true;
    lastContactsMillis = millis();
    updateDisplayControls();
    contactCount = Number(message.count) || 0;
    contacts = [];
    markContactGeometryDirty();
    return;
  }

  if (message.address === "/sensel_morph/contact") {
    contacts.push(message);
    lastContactsMillis = millis();
    markContactGeometryDirty();
    return;
  }

  if (message.address === "/sensel_morph/contact_summary") {
    contactSummary = message;
    lastContactsMillis = millis();
    return;
  }

  if (message.address === "/sensel_morph/accelerometer") {
    accel = message;
    return;
  }

  statusText = "unexpected json";
}

/** Decode one SMPR/SMLB binary raster packet from the transmitter. */
function handleRasterFrame(buffer) {
  if (buffer.byteLength < HEADER_SIZE) {
    statusText = "short binary frame";
    return;
  }

  const view = new DataView(buffer);
  const magic = readAscii(view, 0, 4);
  const version = view.getUint8(4);
  const kind = view.getUint8(5);
  const headerSize = view.getUint16(6, true);
  const frameId = view.getUint32(8, true);
  const timestamp = view.getUint32(12, true);
  const w = view.getUint16(16, true);
  const h = view.getUint16(18, true);
  const bitDepth = view.getUint8(20);
  const flags = view.getUint8(21);
  const payloadLen = view.getUint32(24, true);
  const maxValue = view.getFloat32(28, true);

  if (version !== 1 || headerSize !== HEADER_SIZE || bitDepth !== 8) {
    statusText = "bad raster header";
    return;
  }
  const isRle = (flags & FLAG_RLE) !== 0;
  const expectedLen = w * h;
  if (buffer.byteLength < headerSize + payloadLen) {
    statusText = "bad raster payload";
    return;
  }

  let payload = new Uint8Array(buffer, headerSize, payloadLen);
  if (isRle) {
    payload = decodeByteRle(payload, expectedLen);
    if (!payload) {
      statusText = "bad rle payload";
      return;
    }
  } else if (payloadLen !== expectedLen) {
    statusText = "bad raster payload";
    return;
  }

  if (magic === MAGIC_PRESSURE && kind === KIND_PRESSURE) {
    pressureAvailable = true;
    ensurePressureImage(w, h);
    pressurePixels.set(payload);
    updatePressureImage();
    pressureWidth = w;
    pressureHeight = h;
    pressureFrameId = frameId;
    pressureTimestamp = timestamp;
    pressureMaxValue = maxValue;
    pressureFlags = flags;
    lastPressureMillis = millis();
    markContactGeometryDirty();
  } else if (magic === MAGIC_LABELS && kind === KIND_LABELS) {
    labelsAvailable = true;
    ensureLabelsImage(w, h);
    labelsPixels.set(payload);
    updateLabelsImage();
    labelsWidth = w;
    labelsHeight = h;
    labelsFrameId = frameId;
    lastLabelsMillis = millis();
    updateDisplayControls();
    markContactGeometryDirty();
  } else {
    statusText = "unknown raster stream";
    return;
  }

  binaryCount += 1;
  updateDisplayControls();
  noteFrameReceived(frameId);
  statusText = `connected ${WS_URL}`;
}

function ensurePressureImage(w, h) {
  if (pressureImage && pressureWidth === w && pressureHeight === h && pressurePixels && pressurePixels.length === w * h) {
    return;
  }
  pressureImage = createImage(w, h);
  pressurePixels = new Uint8Array(w * h);
  pressureWidth = w;
  pressureHeight = h;
}

/** Copy uint8 pressure pixels into a p5 image with opaque grayscale pixels. */
function updatePressureImage() {
  pressureImage.loadPixels();
  for (let i = 0; i < pressurePixels.length; i += 1) {
    const v = pressurePixels[i];
    const p = i * 4;
    pressureImage.pixels[p] = v;
    pressureImage.pixels[p + 1] = v;
    pressureImage.pixels[p + 2] = v;
    pressureImage.pixels[p + 3] = 255;
  }
  pressureImage.updatePixels();
}

function ensureLabelsImage(w, h) {
  if (labelsImage && labelsWidth === w && labelsHeight === h && labelsPixels && labelsPixels.length === w * h) {
    return;
  }
  labelsImage = createImage(w, h);
  labelsPixels = new Uint8Array(w * h);
  labelsWidth = w;
  labelsHeight = h;
}

/** Copy label IDs into an opaque color image; label 255 is transparent. */
function updateLabelsImage() {
  labelsImage.loadPixels();
  for (let i = 0; i < labelsPixels.length; i += 1) {
    const label = labelsPixels[i];
    const p = i * 4;
    if (label === 255) {
      labelsImage.pixels[p] = 0;
      labelsImage.pixels[p + 1] = 0;
      labelsImage.pixels[p + 2] = 0;
      labelsImage.pixels[p + 3] = 0;
      continue;
    }
    const col = labelColor(label);
    labelsImage.pixels[p] = col[0];
    labelsImage.pixels[p + 1] = col[1];
    labelsImage.pixels[p + 2] = col[2];
    labelsImage.pixels[p + 3] = 255;
  }
  labelsImage.updatePixels();
}

function drawPressureImage() {
  if (!displayPressure || !pressureAvailable) {
    return;
  }
  if (!pressureImage) {
    return;
  }

  image(pressureImage, 0, 0, PRESSURE_DISPLAY_W, PRESSURE_DISPLAY_H);
}

function drawImageBackdrop() {
  fill(0);
  noStroke();
  rect(0, 0, PRESSURE_DISPLAY_W, PRESSURE_DISPLAY_H);
}

function drawLabelsImage() {
  if (!displayLabels || !labelsAvailable || !labelsImage) {
    return;
  }

  noSmooth();
  if (labelsAreSoloed()) {
    image(labelsImage, 0, 0, PRESSURE_DISPLAY_W, PRESSURE_DISPLAY_H);
    return;
  }

  tint(255, LABEL_ALPHA);
  image(labelsImage, 0, 0, PRESSURE_DISPLAY_W, PRESSURE_DISPLAY_H);
  noTint();
}

function labelsAreSoloed() {
  const pressureVisible = displayPressure && pressureAvailable;
  const contactsVisible = displayContacts && contactsAvailable;
  return !pressureVisible && !contactsVisible;
}

/** Draw contact ellipses, bboxes, peaks, deltas, and ID markers. */
function drawContacts() {
  if (!displayContacts || !contactsAvailable || contacts.length === 0) {
    return;
  }
  ensureContactGeometryCache();

  push();
  textFont(UI_FONT);
  textAlign(CENTER, CENTER);
  textSize(11);
  noFill();
  for (const c of contacts) {
    const id = Number(c.id) || 0;
    const col = labelColor(id);
    const x = contactScreenX(Number(c.x_mm || 0));
    const y = contactScreenY(Number(c.y_mm || 0));
    const major = max(2, Number(c.major_axis_mm || 0) / ACTIVE_H_MM * PRESSURE_DISPLAY_H);
    const minor = max(2, Number(c.minor_axis_mm || 0) / ACTIVE_W_MM * PRESSURE_DISPLAY_W);
    const orientation = radians(Number(c.orientation_deg || 0));

    stroke(col[0], col[1], col[2], 255);
    strokeWeight(1);
    push();
    translate(x, y);
    rotate(orientation);
    ellipse(0, 0, minor, major);
    pop();

    drawContactBBox(c, col);

    if (Number(c.delta_x_mm || 0) !== 0 || Number(c.delta_y_mm || 0) !== 0) {
      const dx = Number(c.delta_x_mm || 0) / ACTIVE_W_MM * PRESSURE_DISPLAY_W * 3.0;
      const dy = Number(c.delta_y_mm || 0) / ACTIVE_H_MM * PRESSURE_DISPLAY_H * 3.0;
      stroke(255, 220);
      line(x, y, x + dx, y + dy);
    }

    const peak = contactDisplayPeak(c);
    if (peak) {
      stroke(0);
      strokeWeight(3);
      line(peak.x - 4, peak.y, peak.x + 4, peak.y);
      line(peak.x, peak.y - 4, peak.x, peak.y + 4);

      stroke(255);
      strokeWeight(1); 
      line(peak.x - 4, peak.y, peak.x + 4, peak.y);
      line(peak.x, peak.y - 4, peak.x, peak.y + 4);
    }

    noStroke();
    fill(0, 160);
    circle(x, y, 18);
    fill(255);
    text(String(id), x, y);
    noFill();
  }
  pop();
}

function drawContactBBox(c, col) {
  const bounds = contactDisplayBounds(c);
  if (!bounds) {
    return;
  }
  stroke(col[0], col[1], col[2], 140);
  strokeWeight(0.5);
  noFill();
  rect(bounds.x0, bounds.y0, bounds.x1 - bounds.x0, bounds.y1 - bounds.y0);
  strokeWeight(1.0); 
}

function contactDisplayBounds(c) {
  return contactJsonDisplayBounds(c);
}

function contactDisplayPeak(c) {
  const rasterPeak = contactRasterDisplayPeak(Number(c.id) || 0, Number(c.frame_id));
  if (rasterPeak) {
    return rasterPeak;
  }

  const peakX = Number(c.peak_x_mm || 0);
  const peakY = Number(c.peak_y_mm || 0);
  const peakForce = Number(c.peak_force || 0);
  if (peakForce <= 0 || peakX < 0 || peakX > ACTIVE_W_MM || peakY < 0 || peakY > ACTIVE_H_MM) {
    return null;
  }
  return {
    x: contactScreenX(peakX),
    y: contactScreenY(peakY),
  };
}

function contactRasterDisplayPeak(label, frameId) {
  if (Number.isFinite(frameId) && frameId !== contactGeometryFrameId) {
    return null;
  }
  return rasterPeaksById.get(label & 0xff) || null;
}

function markContactGeometryDirty() {
  contactGeometryDirty = true;
}

/** Rebuild cached raster peaks only when pressure/label frame IDs change. */
function ensureContactGeometryCache() {
  if (!contactGeometryDirty) {
    return;
  }
  rebuildContactGeometryCache();
  contactGeometryDirty = false;
}

function rebuildContactGeometryCache() {
  rasterPeaksById = new Map();
  contactGeometryFrameId = labelsFrameId;

  if (!labelsPixels || labelsWidth <= 0 || labelsHeight <= 0 || labelsPixels.length < labelsWidth * labelsHeight) {
    return;
  }

  if (pressurePixels && pressureWidth > 0 && pressureHeight > 0 && pressureFrameId === labelsFrameId) {
    rebuildRasterPeakCache();
  }
}

/** Find each contact's brightest pressure pixel inside its label mask. */
function rebuildRasterPeakCache() {
  const rawPeaks = new Map();
  for (let y = 0; y < pressureHeight; y += 1) {
    const labelY = min(labelsHeight - 1, floor((y + 0.5) * labelsHeight / pressureHeight));
    const pressureRow = y * pressureWidth;
    const labelRow = labelY * labelsWidth;
    for (let x = 0; x < pressureWidth; x += 1) {
      const labelX = min(labelsWidth - 1, floor((x + 0.5) * labelsWidth / pressureWidth));
      const label = labelsPixels[labelRow + labelX];
      if (label === 255) {
        continue;
      }
      const value = pressurePixels[pressureRow + x];
      const peak = rawPeaks.get(label);
      if (!peak || value > peak.value) {
        rawPeaks.set(label, { value, x, y });
      }
    }
  }

  for (const [label, peak] of rawPeaks.entries()) {
    if (peak.value <= 0) {
      continue;
    }
    rasterPeaksById.set(label, {
      x: (peak.x + 0.5) / pressureWidth * PRESSURE_DISPLAY_W,
      y: (peak.y + 0.5) / pressureHeight * PRESSURE_DISPLAY_H,
    });
  }
}

function contactJsonDisplayBounds(c) {
  const minXmm = Number(c.min_x_mm || 0);
  const minYmm = Number(c.min_y_mm || 0);
  const maxXmm = Number(c.max_x_mm || 0);
  const maxYmm = Number(c.max_y_mm || 0);
  if (!(maxXmm > minXmm && maxYmm > minYmm)) {
    return null;
  }
  return {
    x0: contactScreenX(minXmm),
    y0: contactScreenY(minYmm),
    x1: contactScreenX(maxXmm),
    y1: contactScreenY(maxYmm),
  };
}

function drawHeader() {
  noStroke();

  fill(245);
  textAlign(LEFT, BASELINE);
  textSize(16);
  text("Sensel Morph WebSocket Receiver (p5 v1)", 16, 26);

  const age = lastMessageMillis === 0 ? "-" : `${nf((millis() - lastMessageMillis) / 1000.0, 0, 2)}s`;
  const dims = pressureImage ? `${pressureWidth}x${pressureHeight}` : "-";
  const flags = [];
  if ((pressureFlags & FLAG_CALIBRATED) !== 0 || calibrated) flags.push("calibrated");
  if ((pressureFlags & FLAG_NORMALIZED) !== 0) flags.push("normalized");
  if ((pressureFlags & FLAG_RLE) !== 0 || rleEnabled) flags.push("rle");

  fill(210);
  textSize(10);
  text(`rx ${nf(rxFps, 0, 1)} fps   frames ${rxFrameCount}   packets ${binaryCount}   contacts ${contactCount}   latest ${age}   ${statusText}`, 16, 48);
  text(`timestamp ${pressureTimestamp}   pressure ${dims} (${pressureRes})   labels ${labelsImage ? `${labelsWidth}x${labelsHeight}` : "-"}   max ${nf(pressureMaxValue, 0, 1)}   serial ${serialNumber} ${flags.join(" ")}`, 16, 64);
  if (accel) {
    text(`accel_g (${nf(Number(accel.x_g || 0), 0, 3)}, ${nf(Number(accel.y_g || 0), 0, 3)}, ${nf(Number(accel.z_g || 0), 0, 3)})`, 16, 80);
  }
}

function drawWsButton() {
  if (!shouldShowWsButton()) {
    return;
  }

  const button = wsButtonRect();
  const hover = mouseX >= button.x && mouseX <= button.x + button.w && mouseY >= button.y && mouseY <= button.y + button.h;

  noStroke();
  fill(hover ? color(70, 120, 170) : color(54, 78, 104));
  rect(button.x, button.y, button.w, button.h, 4);

  fill(245);
  textSize(12);
  textAlign(CENTER, CENTER);
  text(wsConnected ? "Reconnect WS" : "Connect WS", button.x + button.w * 0.5, button.y + button.h * 0.5);
  textAlign(LEFT, BASELINE);
}

function shouldShowWsButton() {
  if (!wsConnected) {
    return true;
  }
  if (lastMessageMillis === 0) {
    return millis() - setupMillis > RECONNECT_AFTER_MS;
  }
  return millis() - lastMessageMillis > RECONNECT_AFTER_MS;
}

function wsButtonRect() {
  return { x: width - 126, y: 16, w: 110, h: 28 };
}

function noteFrameReceived(frameId) {
  if (lastRxFrameId === frameId) {
    return;
  }
  const now = millis();
  if (lastFrameMillis > 0) {
    const instant = 1000.0 / max(1, now - lastFrameMillis);
    rxFps = rxFps <= 0 ? instant : rxFps * 0.85 + instant * 0.15;
  }
  lastRxFrameId = frameId;
  rxFrameCount += 1;
  lastFrameMillis = now;
}

function readAscii(view, offset, count) {
  let out = "";
  for (let i = 0; i < count; i += 1) {
    out += String.fromCharCode(view.getUint8(offset + i));
  }
  return out;
}

/** Decode byte-level [count,value] RLE used by pressure and label rasters. */
function decodeByteRle(encoded, expectedLen) {
  if ((encoded.length & 1) !== 0) {
    return null;
  }
  const out = new Uint8Array(expectedLen);
  let pos = 0;
  for (let i = 0; i < encoded.length; i += 2) {
    const count = encoded[i];
    const value = encoded[i + 1];
    if (count === 0 || pos + count > expectedLen) {
      return null;
    }
    out.fill(value, pos, pos + count);
    pos += count;
  }
  return pos === expectedLen ? out : null;
}

function labelColor(label) {
  const index = ((label % LABEL_PALETTE.length) + LABEL_PALETTE.length) % LABEL_PALETTE.length;
  return LABEL_PALETTE[index];
}

function contactScreenX(xMm) {
  return xMm / ACTIVE_W_MM * PRESSURE_DISPLAY_W;
}

function contactScreenY(yMm) {
  return yMm / ACTIVE_H_MM * PRESSURE_DISPLAY_H;
}
