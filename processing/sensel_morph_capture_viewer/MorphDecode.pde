class Grid {
  String name;
  int cols;
  int rows;
  int xScale;
  int yScale;

  Grid(String name, int cols, int rows, int xScale, int yScale) {
    this.name = name;
    this.cols = cols;
    this.rows = rows;
    this.xScale = xScale;
    this.yScale = yScale;
  }

  int cells() {
    return cols * rows;
  }
}

class DecodedPressure {
  Grid grid;
  int base;
  int[] values;
  int bytesUsed;

  DecodedPressure(Grid grid, int base, int[] values, int bytesUsed) {
    this.grid = grid;
    this.base = base;
    this.values = values;
    this.bytesUsed = bytesUsed;
  }
}

class DecodedLabels {
  Grid grid;
  byte[] values;
  int bytesUsed;

  DecodedLabels(Grid grid, byte[] values, int bytesUsed) {
    this.grid = grid;
    this.values = values;
    this.bytesUsed = bytesUsed;
  }
}

class LiveFrame {
  int frameSerial = -1;
  int frameId;
  int timestamp;
  int contentMask;
  DecodedPressure pressure;
  DecodedLabels labels;
  DecodedContactsFrame contacts;
  AccelFrame accel;
  float[] pressureValues;
  byte[] pressureBlob;
  int pressureWidth;
  int pressureHeight;
  int pressureBitDepth;
  float pressureMax;
  byte[] labelBlob;
  int labelWidth;
  int labelHeight;
  HashMap<Integer, RasterPeak> rasterPeaks = new HashMap<Integer, RasterPeak>();
  HashMap<Integer, RasterEllipse> rasterEllipses = new HashMap<Integer, RasterEllipse>();
}

class RasterPeak {
  float xMm;
  float yMm;
  float force;

  RasterPeak(float xMm, float yMm, float force) {
    this.xMm = xMm;
    this.yMm = yMm;
    this.force = force;
  }
}

class RasterEllipse {
  float xMm;
  float yMm;
  float orientationDeg;
  float majorAxisMm;
  float minorAxisMm;
  int areaCells;

  RasterEllipse(float xMm, float yMm, float orientationDeg, float majorAxisMm, float minorAxisMm, int areaCells) {
    this.xMm = xMm;
    this.yMm = yMm;
    this.orientationDeg = orientationDeg;
    this.majorAxisMm = majorAxisMm;
    this.minorAxisMm = minorAxisMm;
    this.areaCells = areaCells;
  }
}

ArrayList<Grid> gridsFromMetadata(byte[] metadata) {
  ArrayList<Grid> grids = new ArrayList<Grid>();
  if (metadata != null && metadata.length >= 6 && (metadata[2] & 0xff) > 0 && (metadata[3] & 0xff) > 0) {
    grids.add(new Grid("metadata", metadata[2] & 0xff, metadata[3] & 0xff, metadata[4] & 0xff, metadata[5] & 0xff));
  }
  grids.add(new Grid("medium", 47, 27, 4, 4));
  grids.add(new Grid("high", 93, 53, 2, 2));
  return grids;
}

/** Decode one raw frame payload into optional pressure, labels, contacts, and accel sections. */
LiveFrame decodeLiveFrame(FramePacket packet, ArrayList<Grid> grids, MorphSettings settings) {
  byte[] payload = packet.payload;
  if (payload.length < 6) {
    throw new RuntimeException("truncated frame header");
  }
  LiveFrame frame = new LiveFrame();
  frame.contentMask = payload[0] & 0xff;
  frame.frameId = payload[1] & 0xff;
  frame.timestamp = u32le(payload, 2);
  int pos = 6;

  if ((frame.contentMask & 0x04) != 0) {
    frame.contacts = parseContacts(payload, pos);
    pos += frame.contacts.bytesUsed;
  }

  if ((frame.contentMask & 0x08) != 0) {
    if (pos + 6 > payload.length) {
      throw new RuntimeException("truncated accelerometer section");
    }
    frame.accel = new AccelFrame();
    frame.accel.valid = true;
    frame.accel.frameId = frame.frameId;
    frame.accel.x = i16le(payload, pos);
    frame.accel.y = i16le(payload, pos + 2);
    frame.accel.z = i16le(payload, pos + 4);
    frame.accel.xG = frame.accel.x / settings.accelCountsPerG;
    frame.accel.yG = frame.accel.y / settings.accelCountsPerG;
    frame.accel.zG = frame.accel.z / settings.accelCountsPerG;
    pos += 6;
  }

  if ((frame.contentMask & 0x03) != 0) {
    byte[] body = Arrays.copyOfRange(payload, pos, payload.length);
    Grid expectedGrid = expectedGridForPressureRes(grids, settings.pressureRes);
    if ((frame.contentMask & 0x01) != 0) {
      boolean requireAll = (frame.contentMask & 0x02) == 0;
      frame.pressure = decodePressureBody(body, expectedGrid, requireAll);
      if ((frame.contentMask & 0x02) != 0) {
        byte[] labelBody = Arrays.copyOfRange(body, frame.pressure.bytesUsed, body.length);
        frame.labels = decodeLabels(labelBody, frame.pressure.grid);
      }
    } else if ((frame.contentMask & 0x02) != 0) {
      frame.labels = decodeLabels(body, expectedGrid);
    }
  }

  return frame;
}

Grid expectedGridForPressureRes(ArrayList<Grid> grids, String pressureRes) {
  int wantedCols = pressureRes.equals("high") ? 93 : 47;
  int wantedRows = pressureRes.equals("high") ? 53 : 27;
  int wantedXScale = pressureRes.equals("high") ? 2 : 4;
  int wantedYScale = pressureRes.equals("high") ? 2 : 4;
  for (Grid grid : grids) {
    if (grid.cols == wantedCols && grid.rows == wantedRows) {
      return grid;
    }
  }
  return new Grid(pressureRes.equals("high") ? "high" : "medium", wantedCols, wantedRows, wantedXScale, wantedYScale);
}

/**
 * Prepare public output rasters and optional fresh contact geometry.
 * Contacts-only mode remains lightweight; fresh ellipses require pressure+labels.
 */
void prepareOutputRasters(LiveFrame frame, MorphSettings settings) {
  boolean displayPlayback = settings.source.equals("recording");
  if (frame.pressure != null && (settings.pressure || displayPlayback)) {
    PressureValues out = pressureValuesForOutput(frame.pressure, settings.pressureRes);
    frame.pressureValues = out.values;
    frame.pressureWidth = out.width;
    frame.pressureHeight = out.height;
    frame.pressureBitDepth = settings.pressureType.equals("uint16") ? 16 : 8;
    PressurePack packed = packPressure(out.values, settings);
    frame.pressureBlob = packed.blob;
    frame.pressureMax = packed.maxValue;
  }

  if (frame.labels != null && (settings.labels || displayPlayback)) {
    LabelValues outLabels = labelValuesForOutput(frame.labels, settings.labelRes);
    frame.labelBlob = outLabels.values;
    frame.labelWidth = outLabels.width;
    frame.labelHeight = outLabels.height;
  }

  if (frame.contacts != null && (settings.contacts || displayPlayback)) {
    if (frame.pressure != null && frame.labels != null) {
      frame.rasterEllipses = rasterEllipsesByLabel(frame.pressure, frame.labels);
    }
    frame.rasterPeaks = rasterPeaksByLabel(frame.pressureValues, frame.pressureWidth, frame.pressureHeight, frame.labelBlob, frame.labelWidth, frame.labelHeight, settings.forceScale);
  }
}

DecodedPressure inferPressure(byte[] body, ArrayList<Grid> grids, boolean requireAll) {
  RuntimeException last = null;
  for (Grid grid : grids) {
    try {
      return decodePressureBody(body, grid, requireAll);
    } catch (RuntimeException e) {
      last = e;
    }
  }
  throw new RuntimeException("pressure decode failed: " + (last == null ? "" : last.getMessage()));
}

DecodedLabels inferLabels(byte[] body, ArrayList<Grid> grids) {
  RuntimeException last = null;
  for (Grid grid : grids) {
    try {
      return decodeLabels(body, grid);
    } catch (RuntimeException e) {
      last = e;
    }
  }
  throw new RuntimeException("label decode failed: " + (last == null ? "" : last.getMessage()));
}

DecodedPressure decodePressureBody(byte[] data, Grid grid, boolean requireAll) {
  IntRef pos = new IntRef(0);
  int base = readVarint(data, pos);
  int[] values = new int[grid.cells()];
  int emitted = 0;
  boolean zeroMode = true;
  while (emitted < values.length) {
    int code = readVarint(data, pos);
    if (zeroMode) {
      if (emitted + code > values.length) {
        throw new RuntimeException("zero run overflow");
      }
      emitted += code;
      zeroMode = false;
    } else {
      if (code == 0) {
        zeroMode = true;
      } else {
        values[emitted++] = base + code;
      }
    }
  }
  if (requireAll && pos.value != data.length) {
    throw new RuntimeException("trailing pressure bytes");
  }
  return new DecodedPressure(grid, base, values, pos.value);
}

DecodedLabels decodeLabels(byte[] data, Grid grid) {
  IntRef pos = new IntRef(0);
  byte[] values = new byte[grid.cells()];
  int emitted = 0;
  boolean nullRun = true;
  while (emitted < values.length) {
    int runLength = readVarint(data, pos);
    int label;
    if (nullRun) {
      label = 255;
      nullRun = false;
    } else {
      if (pos.value >= data.length) {
        throw new RuntimeException("truncated label byte");
      }
      int encoded = data[pos.value++] & 0xff;
      label = encoded & 0x7f;
      nullRun = encoded < 0x80;
    }
    if (emitted + runLength > values.length) {
      throw new RuntimeException("label run overflow");
    }
    Arrays.fill(values, emitted, emitted + runLength, (byte) label);
    emitted += runLength;
  }
  if (pos.value != data.length) {
    throw new RuntimeException("trailing label bytes");
  }
  return new DecodedLabels(grid, values, pos.value);
}

int readVarint(byte[] data, IntRef pos) {
  if (pos.value >= data.length) {
    throw new RuntimeException("unexpected end of varint");
  }
  int first = data[pos.value++] & 0xff;
  if ((first & 0x80) != 0) {
    if (pos.value >= data.length) {
      throw new RuntimeException("truncated two-byte varint");
    }
    int second = data[pos.value++] & 0xff;
    return (second << 7) | (first & 0x7f);
  }
  return first;
}

class IntRef {
  int value;
  IntRef(int value) {
    this.value = value;
  }
}

class PressureValues {
  float[] values;
  int width;
  int height;
  PressureValues(float[] values, int width, int height) {
    this.values = values;
    this.width = width;
    this.height = height;
  }
}

class LabelValues {
  byte[] values;
  int width;
  int height;
  LabelValues(byte[] values, int width, int height) {
    this.values = values;
    this.width = width;
    this.height = height;
  }
}

class PressurePack {
  byte[] blob;
  float maxValue;
  PressurePack(byte[] blob, float maxValue) {
    this.blob = blob;
    this.maxValue = maxValue;
  }
}

PressureValues pressureValuesForOutput(DecodedPressure decoded, String resolution) {
  int width = pressureWidthForRes(resolution);
  int height = pressureHeightForRes(resolution);
  if (calibrationActive()) {
    PressureValues high = applyCalibrationToHighPressure(expandPressure(decoded));
    if (resolution.equals("high")) {
      return high;
    }
    return new PressureValues(resizeNearest(high.values, high.width, high.height, width, height), width, height);
  }
  if (resolution.equals("high")) {
    return expandPressure(decoded);
  }
  if (decoded.grid.cols < width || decoded.grid.rows < height) {
    PressureValues expanded = expandPressure(decoded);
    return new PressureValues(resizeNearest(expanded.values, expanded.width, expanded.height, width, height), width, height);
  }
  float[] source = new float[decoded.values.length];
  for (int i = 0; i < source.length; i++) source[i] = decoded.values[i];
  return new PressureValues(resizeNearest(source, decoded.grid.cols, decoded.grid.rows, width, height), width, height);
}

LabelValues labelValuesForOutput(DecodedLabels labels, String resolution) {
  int width = pressureWidthForRes(resolution);
  int height = pressureHeightForRes(resolution);
  if (labels.grid.cols == width && labels.grid.rows == height) {
    return new LabelValues(Arrays.copyOf(labels.values, labels.values.length), width, height);
  }
  byte[] out = new byte[width * height];
  for (int y = 0; y < height; y++) {
    int srcY = min(labels.grid.rows - 1, floor((y + 0.5) * labels.grid.rows / height));
    for (int x = 0; x < width; x++) {
      int srcX = min(labels.grid.cols - 1, floor((x + 0.5) * labels.grid.cols / width));
      out[y * width + x] = labels.values[srcY * labels.grid.cols + srcX];
    }
  }
  return new LabelValues(out, width, height);
}

float[] resizeNearest(float[] values, int srcW, int srcH, int dstW, int dstH) {
  if (srcW == dstW && srcH == dstH) {
    return Arrays.copyOf(values, values.length);
  }
  float[] out = new float[dstW * dstH];
  for (int y = 0; y < dstH; y++) {
    int srcY = min(srcH - 1, floor((y + 0.5) * srcH / dstH));
    for (int x = 0; x < dstW; x++) {
      int srcX = min(srcW - 1, floor((x + 0.5) * srcW / dstW));
      out[y * dstW + x] = values[srcY * srcW + srcX];
    }
  }
  return out;
}

PressurePack packPressure(float[] values, MorphSettings settings) {
  float maxValue = 0;
  float[] scaled = new float[values.length];
  float forceScale = settings.forceScale == 0 ? 1.0 : settings.forceScale;
  for (int i = 0; i < values.length; i++) {
    scaled[i] = max(0, values[i] / forceScale);
    maxValue = max(maxValue, scaled[i]);
  }
  if (settings.pressureType.equals("uint16")) {
    byte[] blob = new byte[values.length * 2];
    for (int i = 0; i < scaled.length; i++) {
      int v = constrain(round(scaled[i]), 0, 65535);
      blob[i * 2] = (byte) (v & 0xff);
      blob[i * 2 + 1] = (byte) ((v >> 8) & 0xff);
    }
    return new PressurePack(blob, maxValue);
  }
  byte[] blob = new byte[values.length];
  float multiplier = 1.0;
  for (int i = 0; i < scaled.length; i++) {
    blob[i] = (byte) constrain(round(scaled[i] * multiplier), 0, 255);
  }
  return new PressurePack(blob, maxValue);
}

/** Encode bytes as simple [run_length, value] pairs for transport compression. */
byte[] rleEncode(byte[] blob) {
  if (blob.length == 0) {
    return new byte[0];
  }
  ByteArrayOutputStream out = new ByteArrayOutputStream();
  int runValue = blob[0] & 0xff;
  int runCount = 1;
  for (int i = 1; i < blob.length; i++) {
    int value = blob[i] & 0xff;
    if (value == runValue && runCount < 255) {
      runCount++;
    } else {
      out.write(runCount);
      out.write(runValue);
      runValue = value;
      runCount = 1;
    }
  }
  out.write(runCount);
  out.write(runValue);
  return out.toByteArray();
}

/** Find the brightest pressure pixel inside each current label mask. */
HashMap<Integer, RasterPeak> rasterPeaksByLabel(float[] pressureValues, int pressureW, int pressureH, byte[] labels, int labelW, int labelH, float forceScale) {
  HashMap<Integer, RasterPeak> peaks = new HashMap<Integer, RasterPeak>();
  if (pressureValues == null || labels == null || pressureW <= 0 || pressureH <= 0 || labelW <= 0 || labelH <= 0) {
    return peaks;
  }
  float scale = forceScale == 0 ? 1.0 : forceScale;
  HashSet<Integer> labelIds = labelIdsInBlob(labels);
  for (Integer labelValue : labelIds) {
    int label = labelValue.intValue();
    float best = -1;
    int bestX = -1;
    int bestY = -1;
    for (int y = 0; y < pressureH; y++) {
      int ly = min(labelH - 1, floor((y + 0.5) * labelH / pressureH));
      for (int x = 0; x < pressureW; x++) {
        int lx = min(labelW - 1, floor((x + 0.5) * labelW / pressureW));
        if ((labels[ly * labelW + lx] & 0xff) != label) continue;
        float v = pressureValues[y * pressureW + x];
        if (v > best) {
          best = v;
          bestX = x;
          bestY = y;
        }
      }
    }
    if (bestX >= 0) {
      peaks.put(Integer.valueOf(label), new RasterPeak((bestX + 0.5) * ACTIVE_W_MM / pressureW, (bestY + 0.5) * ACTIVE_H_MM / pressureH, best / scale));
    }
  }
  return peaks;
}

/** Fit fresh pressure-weighted ellipses for each current label ID. */
HashMap<Integer, RasterEllipse> rasterEllipsesByLabel(DecodedPressure pressureFrame, DecodedLabels labelsFrame) {
  HashMap<Integer, RasterEllipse> ellipses = new HashMap<Integer, RasterEllipse>();
  if (pressureFrame == null || labelsFrame == null) return ellipses;
  int w = pressureFrame.grid.cols;
  int h = pressureFrame.grid.rows;
  int lw = labelsFrame.grid.cols;
  int lh = labelsFrame.grid.rows;
  HashSet<Integer> labelIds = labelIdsInBlob(labelsFrame.values);
  for (Integer labelValue : labelIds) {
    int label = labelValue.intValue();
    RasterEllipse fit = fitPressureMomentEllipse(label, pressureFrame.values, w, h, labelsFrame.values, lw, lh);
    if (fit != null) {
      ellipses.put(Integer.valueOf(label), fit);
    }
  }
  return ellipses;
}

HashSet<Integer> labelIdsInBlob(byte[] labels) {
  HashSet<Integer> ids = new HashSet<Integer>();
  if (labels == null) return ids;
  for (byte b : labels) {
    int value = b & 0xff;
    if (value != 255) {
      ids.add(Integer.valueOf(value));
    }
  }
  return ids;
}

/** Compute one pressure-weighted second-moment ellipse within a label mask. */
RasterEllipse fitPressureMomentEllipse(int label, int[] pressure, int w, int h, byte[] labels, int lw, int lh) {
  int area = 0;
  double total = 0;
  double sx = 0;
  double sy = 0;
  for (int y = 0; y < h; y++) {
    int ly = min(lh - 1, floor((y + 0.5) * lh / h));
    for (int x = 0; x < w; x++) {
      int lx = min(lw - 1, floor((x + 0.5) * lw / w));
      if ((labels[ly * lw + lx] & 0xff) != label) continue;
      double weight = max(0, pressure[y * w + x]);
      if (weight <= 0) continue;
      area++;
      double xMm = (x + 0.5) * ACTIVE_W_MM / w;
      double yMm = (y + 0.5) * ACTIVE_H_MM / h;
      total += weight;
      sx += weight * xMm;
      sy += weight * yMm;
    }
  }
  if (area < 2 || total <= 0) return null;
  double meanX = sx / total;
  double meanY = sy / total;
  double cxx = 0;
  double cxy = 0;
  double cyy = 0;
  for (int y = 0; y < h; y++) {
    int ly = min(lh - 1, floor((y + 0.5) * lh / h));
    for (int x = 0; x < w; x++) {
      int lx = min(lw - 1, floor((x + 0.5) * lw / w));
      if ((labels[ly * lw + lx] & 0xff) != label) continue;
      double weight = max(0, pressure[y * w + x]);
      if (weight <= 0) continue;
      double dx = (x + 0.5) * ACTIVE_W_MM / w - meanX;
      double dy = (y + 0.5) * ACTIVE_H_MM / h - meanY;
      cxx += weight * dx * dx;
      cxy += weight * dx * dy;
      cyy += weight * dy * dy;
    }
  }
  cxx /= total;
  cxy /= total;
  cyy /= total;
  double trace = cxx + cyy;
  double detTerm = Math.sqrt(Math.max(0, (cxx - cyy) * (cxx - cyy) + 4 * cxy * cxy));
  double majorValue = Math.max(0, (trace + detTerm) * 0.5);
  double minorValue = Math.max(0, (trace - detTerm) * 0.5);
  double angle = 0.5 * Math.atan2(2 * cxy, cxx - cyy);
  float orientation = wrapContactDegrees(degrees((float) angle) - 90.0);
  return new RasterEllipse((float) meanX, (float) meanY, orientation, (float) (4.0 * Math.sqrt(majorValue)), (float) (4.0 * Math.sqrt(minorValue)), area);
}

float wrapContactDegrees(float angle) {
  while (angle <= -90) angle += 180;
  while (angle > 90) angle -= 180;
  return angle;
}

int u16le(byte[] data, int pos) {
  return (data[pos] & 0xff) | ((data[pos + 1] & 0xff) << 8);
}

int i16le(byte[] data, int pos) {
  int value = u16le(data, pos);
  return value >= 0x8000 ? value - 0x10000 : value;
}

int u32le(byte[] data, int pos) {
  return (data[pos] & 0xff)
    | ((data[pos + 1] & 0xff) << 8)
    | ((data[pos + 2] & 0xff) << 16)
    | ((data[pos + 3] & 0xff) << 24);
}
