final int UI_X = 1094;
final int UI_Y = 8;
final int UI_W = 308;
final int UI_H = 704;
final int UI_ROW = 22;
final int STREAM_COL_GAP = 80;

void drawSettingsUi() {
  int x = UI_X;
  int y = UI_Y;
  fill(0, 140);
  noStroke();
  rect(x, y, UI_W, UI_H);

  fill(255);
  textAlign(LEFT, TOP);
  textSize(12);
  y += 10;
  text("settings", x + 12, y); y += 24;

  int oscX = x + 16;
  int syphonX = oscX + STREAM_COL_GAP;
  text("osc streams", x + 12, y);
  text("syphon streams", syphonX - 4, y);
  y += 18;
  drawUiCheckbox("pressure", config.pressure, oscX, y);
  drawUiCheckbox("pressure", config.syphonPressure, syphonX, y); y += UI_ROW;
  drawUiCheckbox("labels", config.labels, oscX, y);
  drawUiCheckbox("labels", config.syphonLabels, syphonX, y); y += UI_ROW;
  drawUiCheckbox("contacts", config.contacts, oscX, y);
  drawUiCheckbox("contacts", config.syphonContacts, syphonX, y); y += UI_ROW + 8;

  text("pressure_res", x + 12, y); y += 18;
  drawUiRadio("high", config.pressureRes.equals("high"), x + 16, y, pressureResSelectable("high")); y += UI_ROW;
  drawUiRadio("med", config.pressureRes.equals("med"), x + 16, y, pressureResSelectable("med")); y += UI_ROW;
  drawUiRadio("low", config.pressureRes.equals("low"), x + 16, y, pressureResSelectable("low")); y += UI_ROW + 8;

  text("pressure_type", x + 12, y); y += 18;
  drawUiRadio("uint8", config.pressureType.equals("uint8"), x + 16, y); y += UI_ROW;
  drawUiRadio("uint16", config.pressureType.equals("uint16"), x + 16, y); y += UI_ROW + 8;

  text("transport", x + 12, y); y += 18;
  drawUiCheckbox("rle", config.rle, x + 16, y); y += UI_ROW + 8;

  if (calibrationAvailable()) {
    text("calibration", x + 12, y); y += 18;
    drawUiCheckbox("use calibration", calibration.enabled, x + 16, y); y += UI_ROW + 8;
  }

  text("compatibility", x + 12, y); y += 18;
  drawUiCheckbox("morphosc", config.compat.contains("morphosc"), x + 16, y); y += UI_ROW;
  drawUiCheckbox("senselosc", config.compat.contains("senselosc"), x + 16, y); y += UI_ROW + 8;

  drawUiButton("Save settings.txt", x + 12, y, 132, 24);
  boolean holdPausedStatus = config.source.equals("recording") && playbackPaused;
  String visibleStatus = holdPausedStatus ? "playback paused" : uiStatus;
  if (visibleStatus.length() > 0 && (holdPausedStatus || millis() - uiStatusMillis < 3000)) {
    fill(210);
    text(visibleStatus, x + 12, y + 30);
  }
  y += 53;

  boolean deviceAvailable = liveDeviceAvailable();
  boolean recordingAvailable = recordingsAvailable();
  String mode = currentRunMode();
  text("mode", x + 12, y); y += 18;
  drawModeRadio("live device", mode.equals("live"), x + 16, y, deviceAvailable, color(0, 255, 0)); y += UI_ROW;
  drawModeRadio("recording", mode.equals("recording"), x + 16, y, deviceAvailable, color(255, 0, 0)); y += UI_ROW;
  drawModeRadio("playback", mode.equals("playback"), x + 16, y, recordingAvailable, playbackPaused ? color(255, 215, 0) : color(0, 255, 0)); y += UI_ROW + 6;
  drawUiButton("load recording", x + 12, y, 132, 24);
}

void drawUiCheckbox(String label, boolean checked, int x, int y) {
  stroke(255);
  strokeWeight(1);
  fill(checked ? color(80, 180, 255) : color(20));
  rect(x, y + 2, 13, 13);
  if (checked) {
    stroke(255);
    line(x + 3, y + 8, x + 6, y + 11);
    line(x + 6, y + 11, x + 11, y + 4);
  }
  noStroke();
  fill(255);
  text(label, x + 22, y);
}

void drawUiRadio(String label, boolean selected, int x, int y) {
  drawUiRadio(label, selected, x, y, true);
}

void drawUiRadio(String label, boolean selected, int x, int y, boolean enabled) {
  drawUiRadio(label, selected, x, y, enabled, enabled ? color(255) : color(115));
}

void drawModeRadio(String label, boolean selected, int x, int y, boolean enabled, int activeColor) {
  drawUiRadio(label, selected, x, y, enabled, selected && enabled ? activeColor : (enabled ? color(255) : color(115)));
}

void drawUiRadio(String label, boolean selected, int x, int y, boolean enabled, int labelColor) {
  int fg = enabled ? color(255) : color(115);
  stroke(fg);
  strokeWeight(1);
  fill(20);
  ellipse(x + 7, y + 8, 14, 14);
  if (selected) {
    noStroke();
    fill(enabled ? color(80, 180, 255) : color(80));
    ellipse(x + 7, y + 8, 8, 8);
  }
  noStroke();
  fill(labelColor);
  text(label, x + 22, y);
}

void drawUiButton(String label, int x, int y, int w, int h) {
  stroke(255);
  strokeWeight(1);
  fill(35);
  rect(x, y, w, h, 3);
  noStroke();
  fill(255);
  textAlign(CENTER, CENTER);
  text(label, x + w * 0.5, y + h * 0.5 - 1);
  textAlign(LEFT, TOP);
}

void handleSettingsUiMouse(int mx, int my) {
  if (mx < UI_X || mx > UI_X + UI_W || my < UI_Y || my > UI_Y + UI_H) {
    return;
  }

  int x = UI_X;
  int y = UI_Y + 10 + 24;
  int oscX = x + 16;
  int syphonX = oscX + STREAM_COL_GAP;
  y += 18;
  if (hitUiCompactRow(mx, my, oscX, y)) {
    config.pressure = !config.pressure;
    handleStreamOutputChanged();
    return;
  }
  if (hitUiCompactRow(mx, my, syphonX, y)) {
    config.syphonPressure = !config.syphonPressure;
    handleStreamOutputChanged();
    return;
  }
  y += UI_ROW;
  if (hitUiCompactRow(mx, my, oscX, y)) {
    config.labels = !config.labels;
    handleStreamOutputChanged();
    return;
  }
  if (hitUiCompactRow(mx, my, syphonX, y)) {
    config.syphonLabels = !config.syphonLabels;
    handleStreamOutputChanged();
    return;
  }
  y += UI_ROW;
  if (hitUiCompactRow(mx, my, oscX, y)) {
    config.contacts = !config.contacts;
    handleStreamOutputChanged();
    return;
  }
  if (hitUiCompactRow(mx, my, syphonX, y)) {
    config.syphonContacts = !config.syphonContacts;
    handleStreamOutputChanged();
    return;
  }
  y += UI_ROW + 8;

  y += 18;
  if (hitUiRow(mx, my, x + 16, y) && pressureResSelectable("high")) {
    setPressureRes("high");
    return;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x + 16, y) && pressureResSelectable("med")) {
    setPressureRes("med");
    return;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x + 16, y) && pressureResSelectable("low")) {
    setPressureRes("low");
    return;
  }
  y += UI_ROW + 8;

  y += 18;
  if (hitUiRow(mx, my, x + 16, y)) {
    setPressureType("uint8");
    return;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x + 16, y)) {
    setPressureType("uint16");
    return;
  }
  y += UI_ROW + 8;

  y += 18;
  if (hitUiRow(mx, my, x + 16, y)) {
    config.rle = !config.rle;
    handleOutputFormatChanged();
    return;
  }
  y += UI_ROW + 8;

  if (calibrationAvailable()) {
    y += 18;
    if (hitUiRow(mx, my, x + 16, y)) {
      setCalibrationEnabled(!calibration.enabled);
      return;
    }
    y += UI_ROW + 8;
  }

  y += 18;
  if (hitUiRow(mx, my, x + 16, y)) {
    toggleCompat("morphosc");
    return;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x + 16, y)) {
    toggleCompat("senselosc");
    return;
  }
  y += UI_ROW + 8;

  if (hitRect(mx, my, x + 12, y, 132, 24)) {
    uiStatus = saveMorphSettings(config) ? "saved" : "save failed";
    uiStatusMillis = millis();
    return;
  }
  y += 53;

  boolean deviceAvailable = liveDeviceAvailable();
  boolean recordingAvailable = recordingsAvailable();
  y += 18;
  if (hitUiRow(mx, my, x + 16, y) && deviceAvailable) {
    setRunMode("live");
    return;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x + 16, y) && deviceAvailable) {
    setRunMode("recording");
    return;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x + 16, y) && recordingAvailable) {
    setRunMode("playback");
    return;
  }
  y += UI_ROW + 6;
  if (hitRect(mx, my, x + 12, y, 132, 24)) {
    openRecordingFileDialog();
    return;
  }
}

boolean hitUiRow(int mx, int my, int x, int y) {
  return hitRect(mx, my, x, y - 2, 170, 20);
}

boolean hitUiCompactRow(int mx, int my, int x, int y) {
  return hitRect(mx, my, x, y - 2, 76, 20);
}

boolean hitRect(int mx, int my, int x, int y, int w, int h) {
  return mx >= x && mx <= x + w && my >= y && my <= y + h;
}

void drawLocalViewControls(int x, int y) {
  fill(255);
  textAlign(LEFT, TOP);
  textSize(12);
  text("local view", x, y); y += 18;
  drawUiCheckbox("pressure", (displayMode & 1) != 0, x, y); y += UI_ROW;
  drawUiCheckbox("labels", (displayMode & 2) != 0, x, y); y += UI_ROW;
  drawUiCheckbox("contacts", (displayMode & 4) != 0, x, y); y += UI_ROW + 8;

  text("display sampling", x, y); y += 18;
  drawUiRadio("bicubic", surfaceSamplingModeIndex == SURFACE_SAMPLING_BICUBIC, x, y); y += UI_ROW;
  drawUiRadio("nearest", surfaceSamplingModeIndex == SURFACE_SAMPLING_NEAREST, x, y);
}

boolean handleLocalViewMouse(int mx, int my) {
  int x = 16;
  int y = localViewControlsY();
  y += 18;
  if (hitUiRow(mx, my, x, y)) {
    toggleDisplayModeBit(1);
    return true;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x, y)) {
    toggleDisplayModeBit(2);
    return true;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x, y)) {
    toggleDisplayModeBit(4);
    return true;
  }
  y += UI_ROW + 8;
  y += 18;
  if (hitUiRow(mx, my, x, y)) {
    surfaceSamplingModeIndex = SURFACE_SAMPLING_BICUBIC;
    pressureSamplingWarmupFramesRemaining = PRESSURE_SAMPLING_WARMUP_FRAMES;
    return true;
  }
  y += UI_ROW;
  if (hitUiRow(mx, my, x, y)) {
    surfaceSamplingModeIndex = SURFACE_SAMPLING_NEAREST;
    pressureSamplingWarmupFramesRemaining = 0;
    return true;
  }
  return false;
}

void toggleDisplayModeBit(int bit) {
  displayMode ^= bit;
  displayMode &= 7;
}

void handleStreamOutputChanged() {
  if (config.source.equals("recording")) {
    if (osc != null && currentPortName.length() > 0) {
      sendStatus(currentPortName);
    }
    uiStatus = "streams updated";
    uiStatusMillis = millis();
    return;
  }
  clearDisabledStreamState();
  requestReconfigure();
}

void handleOutputFormatChanged() {
  if (config.source.equals("recording")) {
    if (osc != null && currentPortName.length() > 0) {
      sendStatus(currentPortName);
    }
    refreshPausedPlaybackFrame();
    uiStatus = "output format updated";
    uiStatusMillis = millis();
    return;
  }
  requestReconfigure();
}

int localViewControlsY() {
  return 452;
}

String currentRunMode() {
  if (config.source.equals("recording")) {
    return "playback";
  }
  return packetRecorder.isActive() || config.recordEnabled ? "recording" : "live";
}

boolean liveDeviceAvailable() {
  if (device != null) {
    return true;
  }
  if (config.device != null && config.device.length() > 0) {
    return new File(config.device).exists();
  }
  try {
    String[] ports = Serial.list();
    for (String port : ports) {
      if (port.indexOf("/dev/cu.usbmodem") >= 0 || port.indexOf("usbmodem") >= 0) {
        return true;
      }
    }
  } catch (Exception e) {
  }
  return false;
}

void setPressureRes(String value) {
  if (!pressureResSelectable(value)) {
    return;
  }
  if (!config.pressureRes.equals(value)) {
    config.pressureRes = value;
    config.labelRes = value;
    requestReconfigure();
  }
}

void setPressureType(String value) {
  if (!config.pressureType.equals(value)) {
    config.pressureType = value;
    handleOutputFormatChanged();
  }
}

void toggleCompat(String mode) {
  if (config.compat.contains(mode)) {
    config.compat.remove(mode);
  } else {
    config.compat.add(mode);
  }
  handleOutputFormatChanged();
}

boolean pressureResSelectable(String value) {
  if (!config.source.equals("recording") || recordingSource == null || recordingSource.recordedPressureRes.length() == 0) {
    return true;
  }
  return recordingSource.recordedPressureRes.equals(value);
}

void clearDisabledStreamState() {
  if (!needsPressureOutput(config)) {
    pressureImage = null;
    latestPressureWidth = 0;
    latestPressureHeight = 0;
  }
  if (!needsLabelOutput(config)) {
    labelsImage = null;
    latestLabelValues = new byte[0];
    latestLabelsFrameId = -1;
    latestLabelsWidth = 0;
    latestLabelsHeight = 0;
  }
  if (!needsContactOutput(config)) {
    contactFrame = new DecodedContactsFrame(-1, new SenselContact[0]);
    contactSummaryFrame = new ContactSummaryFrame();
    displayRasterPeaks = new HashMap<Integer, RasterPeak>();
    displayRasterEllipses = new HashMap<Integer, RasterEllipse>();
  }
  updateSurfaceBuffers(pressureImage, labelsImage);
}
