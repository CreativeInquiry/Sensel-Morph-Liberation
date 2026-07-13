void createSurfaceBuffers() {
  pressureBuffer = createGraphics(width, height, P2D);
  labelsBuffer = createGraphics(width, height, P2D);
  contactsBuffer = createGraphics(width, height, P2D);
  configureSurfaceBuffer(pressureBuffer);
  configureSurfaceBuffer(labelsBuffer);
  configureSurfaceBuffer(contactsBuffer);
}

void configureSurfaceBuffer(PGraphics pg) {
  pg.noSmooth();
  pg.beginDraw();
  pg.clear();
  pg.endDraw();
}

/** Copy the latest decoded frame into display buffers without changing transport state. */
void acceptLiveFrameForDisplay(LiveFrame frame) {
  boolean displayPlayback = config.source.equals("recording");
  if ((config.pressure || displayPlayback) && frame.pressureBlob != null) {
    pressureImage = pressureImageFromBlob(frame.pressureBlob, frame.pressureWidth, frame.pressureHeight, frame.pressureBitDepth, frame.pressureMax);
    latestPressureWidth = frame.pressureWidth;
    latestPressureHeight = frame.pressureHeight;
  }
  if (frame.labelBlob != null) {
    labelsImage = (config.labels || displayPlayback) ? labelsImageFromBlob(frame.labelBlob, frame.labelWidth, frame.labelHeight) : null;
    latestLabelValues = Arrays.copyOf(frame.labelBlob, frame.labelBlob.length);
    latestLabelsWidth = frame.labelWidth;
    latestLabelsHeight = frame.labelHeight;
    latestLabelsFrameId = frame.frameId;
  }
  if ((config.contacts || displayPlayback) && frame.contacts != null) {
    contactFrame = frame.contacts;
    contactFrame.frameId = frame.frameId;
    displayRasterPeaks = frame.rasterPeaks;
    displayRasterEllipses = frame.rasterEllipses;
  } else if (!config.contacts && !displayPlayback) {
    contactFrame = new DecodedContactsFrame(-1, new SenselContact[0]);
    contactSummaryFrame = new ContactSummaryFrame();
    displayRasterPeaks = new HashMap<Integer, RasterPeak>();
    displayRasterEllipses = new HashMap<Integer, RasterEllipse>();
  }
  if (frame.accel != null) {
    accelFrame = frame.accel;
  }
  if ((config.contacts || displayPlayback) && frame.contacts != null) {
    contactSummaryFrame = summaryFrameForDisplay(frame);
  }
  updateSurfaceBuffers(pressureImage, labelsImage);
}

PImage pressureImageFromBlob(byte[] blob, int w, int h, int bitDepth, float maxValue) {
  if (w <= 0 || h <= 0) return null;
  PImage img = createImage(w, h, RGB);
  img.loadPixels();
  int cells = w * h;
  for (int i = 0; i < cells; i++) {
    int value;
    if (bitDepth == 16) {
      int lo = blob[i * 2] & 0xff;
      int hi = blob[i * 2 + 1] & 0xff;
      value = lo | (hi << 8);
    } else {
      value = blob[i] & 0xff;
    }
    int gray = pressureGray(value, maxValue);
    img.pixels[i] = 0xff000000 | (gray << 16) | (gray << 8) | gray;
  }
  img.updatePixels();
  return img;
}

PImage labelsImageFromBlob(byte[] blob, int w, int h) {
  if (w <= 0 || h <= 0) return null;
  PImage img = createImage(w, h, RGB);
  img.loadPixels();
  for (int i = 0; i < w * h; i++) {
    img.pixels[i] = labelColor(blob[i] & 0xff);
  }
  img.updatePixels();
  return img;
}

int pressureGray(int value, float maxValue) {
  return constrain(value, 0, 255);
}

int labelColor(int label) {
  if (label == 255) {
    return 0xff000000;
  }
  return labelPalette[label % labelPalette.length];
}

void updateSurfaceBuffers(PImage pressure, PImage labels) {
  drawPressureBuffer(pressure);
  drawLabelsBuffer(labels);
  drawContactsBuffer();
}

void drawPressureBuffer(PImage pressure) {
  applyPressureSampling(pressureBuffer);
  pressureBuffer.beginDraw();
  pressureBuffer.clear();
  pressureBuffer.background(0);
  if (pressure != null) {
    SurfaceRect r = sensorSurfaceRect(pressureBuffer);
    pressureBuffer.image(pressure, r.x, r.y, r.w, r.h);
  }
  pressureBuffer.endDraw();
}

void drawLabelsBuffer(PImage labels) {
  applyNearestSampling(labelsBuffer);
  labelsBuffer.beginDraw();
  labelsBuffer.clear();
  if (labels != null) {
    SurfaceRect r = sensorSurfaceRect(labelsBuffer);
    labelsBuffer.image(labels, r.x, r.y, r.w, r.h);
  }
  labelsBuffer.endDraw();
}

void drawContactsBuffer() {
  contactsBuffer.beginDraw();
  contactsBuffer.clear();
  drawContactsInto(contactsBuffer);
  contactsBuffer.endDraw();
}

void drawCurrentLayers() {
  if ((displayMode & 1) != 0) image(pressureBuffer, 0, 0);
  if ((displayMode & 2) != 0) {
    blendMode(BLEND);
    tint(255, displayMode == 2 ? 255 : 102);
    image(labelsBuffer, 0, 0);
    noTint();
  }
  if ((displayMode & 4) != 0) image(contactsBuffer, 0, 0);
  blendMode(BLEND);
}

void drawPlaybackProgressBar() {
  if (!config.source.equals("recording") || recordingSource == null) {
    return;
  }
  float p = recordingSource.progress();
  noStroke();
  fill(150, 150, 150, 90);
  rect(0, height - 10, width, 10);
  fill(220, 220, 220, 165);
  rect(0, height - 10, width * p, 10);
}

/** Draw contacts with corrected raster ellipses when present, firmware fallback otherwise. */
void drawContactsInto(PGraphics pg) {
  if (contactFrame == null || contactFrame.contacts == null) return;
  pg.pushStyle();
  pg.noFill();
  pg.textAlign(CENTER, CENTER);
  pg.textSize(12);
  for (SenselContact c : contactFrame.contacts) {
    int col = labelPalette[c.id % labelPalette.length];
    RasterEllipse ellipse = contactEllipse(c, displayRasterEllipses);
    float cx = contactScreenX(ellipse.xMm, pg);
    float cy = contactScreenY(ellipse.yMm, pg);
    SurfaceRect r = sensorSurfaceRect(pg);
    float w = ellipse.minorAxisMm / ACTIVE_W_MM * r.w;
    float h = ellipse.majorAxisMm / ACTIVE_H_MM * r.h;

    pg.stroke(col);
    pg.strokeWeight(1);
    pg.pushMatrix();
    pg.translate(cx, cy);
    pg.rotate(radians(ellipse.orientationDeg));
    pg.ellipse(0, 0, max(2, w), max(2, h));
    pg.popMatrix();

    float[] bbox = contactDisplayBounds(c, pg);
    if (bbox != null) {
      pg.stroke(col, 160);
      pg.strokeWeight(1);
      pg.rect(bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]);
    }

    RasterPeak peak = contactPeak(c, displayRasterPeaks);
    boolean hasPeak = peak != null && peak.force > 0 && peak.xMm >= 0 && peak.xMm <= ACTIVE_W_MM && peak.yMm >= 0 && peak.yMm <= ACTIVE_H_MM;
    if (hasPeak) {
      drawPeakCrosshair(pg, peak.xMm, peak.yMm);
    }

    if (c.hasDelta) {
      PVector d = contactDeltaScreenVector(c.deltaX * 3.0, c.deltaY * 3.0, pg);
      drawVector(pg, cx, cy, cx + d.x, cy + d.y, color(255, 220));
    }

    pg.noStroke();
    pg.fill(0, 150);
    pg.ellipse(cx, cy, 18, 18);
    pg.fill(255);
    pg.text(str(c.id), cx, cy - 1);
    pg.noFill();
  }
  pg.popStyle();
}

float[] contactDisplayBounds(SenselContact c, PGraphics pg) {
  float[] labelBounds = contactLabelScreenBounds(c.id, pg);
  if (labelBounds != null) {
    return labelBounds;
  }
  if (c.hasBounds) {
    return contactFirmwareScreenBounds(c, pg);
  }
  return null;
}

/** Compute display bboxes from the current label raster so boxes wrap actual blobs. */
float[] contactLabelScreenBounds(int label, PGraphics pg) {
  if (contactFrame.frameId != latestLabelsFrameId || latestLabelValues.length < latestLabelsWidth * latestLabelsHeight) {
    return null;
  }

  int wanted = label & 0xff;
  int minX = latestLabelsWidth;
  int minY = latestLabelsHeight;
  int maxX = -1;
  int maxY = -1;
  for (int y = 0; y < latestLabelsHeight; y++) {
    int row = y * latestLabelsWidth;
    for (int x = 0; x < latestLabelsWidth; x++) {
      if ((latestLabelValues[row + x] & 0xff) == wanted) {
        minX = min(minX, x);
        minY = min(minY, y);
        maxX = max(maxX, x);
        maxY = max(maxY, y);
      }
    }
  }

  if (maxX < minX || maxY < minY) {
    return null;
  }
  return rasterBoundsToScreen(minX, minY, maxX + 1, maxY + 1, latestLabelsWidth, latestLabelsHeight, pg);
}

float[] contactFirmwareScreenBounds(SenselContact c, PGraphics pg) {
  int rasterW = latestLabelsWidth > 0 ? latestLabelsWidth : (latestPressureWidth > 0 ? latestPressureWidth : SENSOR_W);
  int rasterH = latestLabelsHeight > 0 ? latestLabelsHeight : (latestPressureHeight > 0 ? latestPressureHeight : SENSOR_H);
  int minX = constrain(floor(c.minX / ACTIVE_W_MM * rasterW - 0.5), 0, rasterW);
  int minY = constrain(floor(c.minY / ACTIVE_H_MM * rasterH - 0.5), 0, rasterH);
  int maxX = constrain(ceil(c.maxX / ACTIVE_W_MM * rasterW + 0.5), 0, rasterW);
  int maxY = constrain(ceil(c.maxY / ACTIVE_H_MM * rasterH + 0.5), 0, rasterH);
  return rasterBoundsToScreen(minX, minY, maxX, maxY, rasterW, rasterH, pg);
}

float[] rasterBoundsToScreen(int minX, int minY, int maxX, int maxY, int rasterW, int rasterH, PGraphics pg) {
  SurfaceRect r = sensorSurfaceRect(pg);
  return new float[] {
    r.x + minX / (float) rasterW * r.w,
    r.y + minY / (float) rasterH * r.h,
    r.x + maxX / (float) rasterW * r.w,
    r.y + maxY / (float) rasterH * r.h
  };
}

void drawPeakCrosshair(PGraphics pg, float xMm, float yMm) {
  float x = contactScreenX(xMm, pg);
  float y = contactScreenY(yMm, pg);
  pg.pushStyle();
  pg.stroke(0);
  pg.strokeWeight(4);
  pg.line(x - 5, y+0.5, x + 5, y+0.5);
  pg.line(x+0.5, y - 5, x+0.5, y + 5);
  
  pg.stroke(255);
  pg.strokeWeight(1);
  pg.line(x - 5, y, x + 5, y);
  pg.line(x, y - 5, x, y + 5);
  pg.popStyle();
}

void drawVector(PGraphics pg, float x0, float y0, float x1, float y1, int col) {
  pg.pushStyle();
  pg.stroke(col);
  pg.strokeWeight(1);
  pg.line(x0, y0, x1, y1);
  pg.popStyle();
}

float contactScreenX(float xMm, PGraphics pg) {
  SurfaceRect r = sensorSurfaceRect(pg);
  return r.x + xMm / ACTIVE_W_MM * r.w;
}

float contactScreenY(float yMm, PGraphics pg) {
  SurfaceRect r = sensorSurfaceRect(pg);
  return r.y + yMm / ACTIVE_H_MM * r.h;
}

PVector contactDeltaScreenVector(float dxMm, float dyMm, PGraphics pg) {
  SurfaceRect r = sensorSurfaceRect(pg);
  return new PVector(dxMm / ACTIVE_W_MM * r.w, dyMm / ACTIVE_H_MM * r.h);
}

SurfaceRect sensorSurfaceRect(PGraphics pg) {
  float scale = max(pg.width / (float) SENSOR_W, pg.height / (float) SENSOR_H);
  float w = SENSOR_W * scale;
  float h = SENSOR_H * scale;
  return new SurfaceRect((pg.width - w) * 0.5, 0, w, h);
}

class SurfaceRect {
  float x;
  float y;
  float w;
  float h;

  SurfaceRect(float x, float y, float w, float h) {
    this.x = x;
    this.y = y;
    this.w = w;
    this.h = h;
  }
}

void applyPressureSampling(PGraphics pg) {
  if (surfaceSamplingModeIndex == SURFACE_SAMPLING_LINEAR) {
    pg.smooth(2);
  } else {
    pg.noSmooth();
  }
  if (pg instanceof PGraphicsOpenGL) {
    ((PGraphicsOpenGL) pg).textureSampling(surfaceSamplingModeIndex == SURFACE_SAMPLING_NEAREST ? TEXTURE_SAMPLING_NEAREST : TEXTURE_SAMPLING_LINEAR);
  }
}

void applyNearestSampling(PGraphics pg) {
  pg.noSmooth();
  if (pg instanceof PGraphicsOpenGL) {
    ((PGraphicsOpenGL) pg).textureSampling(TEXTURE_SAMPLING_NEAREST);
  }
}

void drawHud() {
  fill(0, 180);
  noStroke();
  rect(8, 8, 190, 582);
  fill(255);
  textAlign(LEFT, TOP);
  textSize(12);
  int y = 16;
  text("sensel_morph_osc_transmitter", 16, y); y += 16;
  text(statusLine, 16, y); y += 16;
  text("serial_number: " + deviceSerial, 16, y); y += 16;
  y += 4;
  text("tx_fps: " + nf(txFps, 0, 1), 16, y); y += 16;
  text("frames: " + transmittedFrames, 16, y); y += 16;
  text("mode: " + currentRunMode(), 16, y); y += 16;
  text("source: " + (config.source.equals("recording") ? "recording" : "device"), 16, y); y += 16;
  if (packetRecorder.isActive()) {
    text("recording: " + packetRecorder.framesWritten() + " frames", 16, y); y += 16;
  } else if (config.source.equals("recording") && recordingSource != null) {
    text("playback: " + (playbackPaused ? "paused " : "running ") + recordingSource.playbackFrameIndex() + "/" + recordingSource.playbackFrameCount(), 16, y); y += 16;
  } else {
    text("recording: off", 16, y); y += 16;
  }
  text("chunk_size: " + config.chunkSize, 16, y); y += 16;
  text("display_mode: " + displayMode, 16, y); y += 16;
  y += 4;
  if (accelFrame.valid) {
    text("accel_x_g: " + nf(accelFrame.xG, 0, 3), 16, y); y += 16;
    text("accel_y_g: " + nf(accelFrame.yG, 0, 3), 16, y); y += 16;
    text("accel_z_g: " + nf(accelFrame.zG, 0, 3), 16, y); y += 16;
  } else {
    text("accel_x_g: no data", 16, y); y += 16;
    text("accel_y_g: no data", 16, y); y += 16;
    text("accel_z_g: no data", 16, y); y += 16;
  }
  y += 4;
  if (contactSummaryFrame.valid) {
    text("contact_count: " + contactSummaryFrame.count, 16, y); y += 16;
    text("force_total: " + nf(contactSummaryFrame.forceTotal, 0, 1), 16, y); y += 16;
    text("force_avg: " + nf(contactSummaryFrame.forceAvg, 0, 1), 16, y); y += 16;
    text("area_avg: " + nf(contactSummaryFrame.areaAvg, 0, 1), 16, y); y += 16;
    text("x_avg: " + nf(contactSummaryFrame.xAvg, 0, 3), 16, y); y += 16;
    text("y_avg: " + nf(contactSummaryFrame.yAvg, 0, 3), 16, y); y += 16;
    text("x_force_avg: " + nf(contactSummaryFrame.xForceAvg, 0, 3), 16, y); y += 16;
    text("y_force_avg: " + nf(contactSummaryFrame.yForceAvg, 0, 3), 16, y); y += 16;
  } else {
    text("contact_count: no data", 16, y); y += 16;
  }
  y += 4;
  text("keys: 1-7 layers, s sampling", 16, y); y += 16;
  text("      space pause, arrows step", 16, y); y += 16;
  text("      h hide/show controls", 16, y);
  drawLocalViewControls(16, localViewControlsY());
}

ContactSummaryFrame summaryFrameForDisplay(LiveFrame frame) {
  ContactSummaryFrame summary = new ContactSummaryFrame();
  ContactStats stats = contactStats(frame.contacts.contacts, frame.rasterEllipses);
  summary.valid = true;
  summary.frameId = frame.frameId;
  summary.count = frame.contacts.contacts.length;
  summary.xAvg = stats.x / ACTIVE_W_MM;
  summary.yAvg = stats.y / ACTIVE_H_MM;
  summary.xForceAvg = stats.xWeighted / ACTIVE_W_MM;
  summary.yForceAvg = stats.yWeighted / ACTIVE_H_MM;
  summary.forceTotal = stats.totalForce;
  summary.forceAvg = stats.avgForce;
  summary.areaAvg = stats.area;
  summary.spread = stats.avgDistance;
  summary.avgWeightedDistance = stats.avgWeightedDistance;
  return summary;
}
