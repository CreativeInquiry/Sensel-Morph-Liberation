final int VIEWER_UI_ROW = 22;
final int VIEWER_LOCAL_X = 16;
final int VIEWER_LOCAL_Y = 452;
final int VIEWER_LOAD_W = 132;
final int VIEWER_LOAD_H = 24;

void drawViewerUi() {
  int x = VIEWER_LOCAL_X;
  int y = drawLocalViewControls(x, VIEWER_LOCAL_Y);

  y += 10;
  fill(255);
  textAlign(LEFT, TOP);
  textSize(12);
  text(recordingSource == null ? "no recording loaded" : (playbackPaused ? "paused" : "playing"), x, y);
  y += 20;

  drawUiButton("load recording", x, y, VIEWER_LOAD_W, VIEWER_LOAD_H);
  y += 32;
  if (uiStatus.length() > 0 && millis() - uiStatusMillis < 3000) {
    fill(210);
    text(uiStatus, x, y);
  }
}

void drawViewerHud() {
  fill(0, 155);
  noStroke();
  rect(8, 8, 360, 78);
  fill(235);
  textAlign(LEFT, TOP);
  textSize(12);
  String name = recordingSource == null ? "none" : new File(config.recordingFile).getName();
  text("sensel_morph_capture_viewer", 16, 16);
  text("recording: " + name, 16, 32);
  if (recordingSource != null) {
    text("frame: " + recordingSource.playbackFrameIndex() + "/" + recordingSource.playbackFrameCount()
      + "   mode " + displayMode + " " + displayModeName(displayMode)
      + "   sampling " + surfaceSamplingName(), 16, 48);
  } else {
    text(statusLine, 16, 48);
  }
  text("keys: 1-7 layers, s sampling, space pause, arrows step, h hide", 16, 64);
}

int drawLocalViewControls(int x, int y) {
  fill(255);
  textAlign(LEFT, TOP);
  textSize(12);
  text("local view", x, y); y += 18;
  drawUiCheckbox("pressure", (displayMode & 1) != 0, x, y); y += VIEWER_UI_ROW;
  drawUiCheckbox("labels", (displayMode & 2) != 0, x, y); y += VIEWER_UI_ROW;
  drawUiCheckbox("contacts", (displayMode & 4) != 0, x, y); y += VIEWER_UI_ROW + 8;

  text("display sampling", x, y); y += 18;
  drawUiRadio("linear", surfaceSamplingModeIndex == SURFACE_SAMPLING_LINEAR, x, y); y += VIEWER_UI_ROW;
  drawUiRadio("nearest", surfaceSamplingModeIndex == SURFACE_SAMPLING_NEAREST, x, y);
  return y + VIEWER_UI_ROW;
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
  stroke(255);
  strokeWeight(1);
  fill(20);
  ellipse(x + 7, y + 8, 14, 14);
  if (selected) {
    noStroke();
    fill(80, 180, 255);
    ellipse(x + 7, y + 8, 8, 8);
  }
  noStroke();
  fill(255);
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

boolean handleViewerUiMouse(int mx, int my) {
  int buttonY = loadRecordingButtonY();
  if (hitRect(mx, my, VIEWER_LOCAL_X, buttonY, VIEWER_LOAD_W, VIEWER_LOAD_H)) {
    openRecordingFileDialog();
    return true;
  }
  return handleLocalViewMouse(mx, my);
}

boolean handleLocalViewMouse(int mx, int my) {
  int x = VIEWER_LOCAL_X;
  int y = VIEWER_LOCAL_Y + 18;
  if (hitUiRow(mx, my, x, y)) {
    toggleDisplayModeBit(1);
    return true;
  }
  y += VIEWER_UI_ROW;
  if (hitUiRow(mx, my, x, y)) {
    toggleDisplayModeBit(2);
    return true;
  }
  y += VIEWER_UI_ROW;
  if (hitUiRow(mx, my, x, y)) {
    toggleDisplayModeBit(4);
    return true;
  }
  y += VIEWER_UI_ROW + 8 + 18;
  if (hitUiRow(mx, my, x, y)) {
    surfaceSamplingModeIndex = SURFACE_SAMPLING_LINEAR;
    refreshSurfaceSampling();
    return true;
  }
  y += VIEWER_UI_ROW;
  if (hitUiRow(mx, my, x, y)) {
    surfaceSamplingModeIndex = SURFACE_SAMPLING_NEAREST;
    refreshSurfaceSampling();
    return true;
  }
  return false;
}

void mousePressed() {
  if (showHelp) {
    handleViewerUiMouse(mouseX, mouseY);
  }
}

void toggleDisplayModeBit(int bit) {
  displayMode ^= bit;
  displayMode &= 7;
}

String displayModeName(int mode) {
  String name = "";
  if ((mode & 1) != 0) {
    name += "pressure";
  }
  if ((mode & 2) != 0) {
    name += (name.length() == 0 ? "" : "+") + "labels";
  }
  if ((mode & 4) != 0) {
    name += (name.length() == 0 ? "" : "+") + "contacts";
  }
  return name.length() == 0 ? "none" : name;
}

String surfaceSamplingName() {
  return surfaceSamplingModeIndex == SURFACE_SAMPLING_LINEAR ? "linear" : "nearest";
}

boolean hitUiRow(int mx, int my, int x, int y) {
  return hitRect(mx, my, x, y - 2, 170, 20);
}

boolean hitRect(int mx, int my, int x, int y, int w, int h) {
  return mx >= x && mx <= x + w && my >= y && my <= y + h;
}

int localViewControlsY() {
  return VIEWER_LOCAL_Y;
}

int loadRecordingButtonY() {
  int y = VIEWER_LOCAL_Y + 18;
  y += VIEWER_UI_ROW * 3;
  y += 8 + 18;
  y += VIEWER_UI_ROW * 2;
  y += 10 + 20;
  return y;
}
