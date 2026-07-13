class DecodedContactsFrame {
  int frameId;
  SenselContact[] contacts;
  int bytesUsed;
  int contactMask;

  DecodedContactsFrame(int frameId, SenselContact[] contacts) {
    this.frameId = frameId;
    this.contacts = contacts;
  }
}

class SenselContact {
  int id;
  int state;
  float x;
  float y;
  float force;
  float area;
  float orientation;
  float majorAxis;
  float minorAxis;
  boolean axesAreMm = true;
  float deltaX;
  float deltaY;
  float deltaForce;
  float deltaArea;
  float minX;
  float minY;
  float maxX;
  float maxY;
  float peakX;
  float peakY;
  float peakForce;
  boolean hasDelta;
  boolean hasBounds;
  boolean hasPeak;

  SenselContact(int id, int state, float x, float y, float force, float area) {
    this.id = id;
    this.state = state;
    this.x = x;
    this.y = y;
    this.force = force;
    this.area = area;
    this.orientation = 0;
    this.majorAxis = sqrt(max(1, area) / PI) * 2;
    this.minorAxis = this.majorAxis;
  }
}

class ContactSummaryFrame {
  boolean valid = false;
  int frameId = -1;
  int count = 0;
  float xAvg = 0;
  float yAvg = 0;
  float xForceAvg = 0;
  float yForceAvg = 0;
  float forceTotal = 0;
  float forceAvg = 0;
  float areaAvg = 0;
  float spread = 0;
  float avgWeightedDistance = 0;
}

class AccelFrame {
  boolean valid = false;
  int frameId = -1;
  int x = 0;
  int y = 0;
  int z = 0;
  float xG = 0;
  float yG = 0;
  float zG = 0;
}

DecodedContactsFrame parseContacts(byte[] data, int pos) {
  if (pos + 2 > data.length) {
    throw new RuntimeException("truncated contact header");
  }
  int start = pos;
  int contactMask = data[pos] & 0xff;
  int n = data[pos + 1] & 0xff;
  pos += 2;
  if (n > 16) {
    throw new RuntimeException("too many contacts: " + n);
  }
  SenselContact[] contacts = new SenselContact[n];
  for (int i = 0; i < n; i++) {
    if (pos + 10 > data.length) {
      throw new RuntimeException("truncated contact data");
    }
    SenselContact c = new SenselContact(
      data[pos] & 0xff,
      data[pos + 1] & 0xff,
      u16le(data, pos + 2) / 256.0,
      u16le(data, pos + 4) / 256.0,
      u16le(data, pos + 6) / 8.0,
      (float) u16le(data, pos + 8)
    );
    pos += 10;
    if ((contactMask & 0x01) != 0) {
      c.orientation = i16le(data, pos) / 16.0;
      c.majorAxis = u16le(data, pos + 2) / 256.0;
      c.minorAxis = u16le(data, pos + 4) / 256.0;
      pos += 6;
    }
    if ((contactMask & 0x02) != 0) {
      c.deltaX = i16le(data, pos) / 256.0;
      c.deltaY = i16le(data, pos + 2) / 256.0;
      c.deltaForce = i16le(data, pos + 4) / 8.0;
      c.deltaArea = (float) i16le(data, pos + 6);
      c.hasDelta = abs(c.deltaX) > 0.0001 || abs(c.deltaY) > 0.0001;
      pos += 8;
    }
    if ((contactMask & 0x04) != 0) {
      c.minX = u16le(data, pos) / 256.0;
      c.minY = u16le(data, pos + 2) / 256.0;
      c.maxX = u16le(data, pos + 4) / 256.0;
      c.maxY = u16le(data, pos + 6) / 256.0;
      c.hasBounds = c.maxX > c.minX && c.maxY > c.minY;
      pos += 8;
    }
    if ((contactMask & 0x08) != 0) {
      c.peakX = (float) u16le(data, pos);
      c.peakY = (float) u16le(data, pos + 2);
      c.peakForce = u16le(data, pos + 4) / 8.0;
      normalizePeakCoordinates(c);
      c.hasPeak = hasDrawablePeak(c);
      pos += 6;
    }
    contacts[i] = c;
  }
  DecodedContactsFrame frame = new DecodedContactsFrame(-1, contacts);
  frame.bytesUsed = pos - start;
  frame.contactMask = contactMask;
  return frame;
}

class RasterBounds {
  float minX;
  float minY;
  float maxX;
  float maxY;

  RasterBounds(float minX, float minY, float maxX, float maxY) {
    this.minX = minX;
    this.minY = minY;
    this.maxX = maxX;
    this.maxY = maxY;
  }
}

/** Send contact JSON messages over WebSocket text frames. */
void sendContacts(LiveFrame frame) {
  if (ws == null) return;
  SenselContact[] contacts = frame.contacts.contacts;
  HashMap<Integer, RasterBounds> bounds = rasterBoundsByLabel(frame.labelBlob, frame.labelWidth, frame.labelHeight);
  ws.broadcastText("{" + wsJsonPair("address", "/sensel_morph/contacts") + "," + wsJsonPair("frame_id", frame.frameId) + "," + wsJsonPair("count", contacts.length) + "}");
  sendContactSummary(frame);
  for (SenselContact c : contacts) {
    RasterPeak peak = contactPeak(c, frame.rasterPeaks);
    RasterEllipse ellipse = contactEllipse(c, frame.rasterEllipses);
    RasterBounds bbox = contactBounds(c, bounds);
    ws.broadcastText("{"
      + wsJsonPair("address", "/sensel_morph/contact") + ","
      + wsJsonPair("frame_id", frame.frameId) + ","
      + wsJsonPair("id", c.id) + ","
      + wsJsonPair("state", c.state) + ","
      + wsJsonPair("x_mm", ellipse.xMm) + ","
      + wsJsonPair("y_mm", ellipse.yMm) + ","
      + wsJsonPair("x_norm", ellipse.xMm / ACTIVE_W_MM) + ","
      + wsJsonPair("y_norm", ellipse.yMm / ACTIVE_H_MM) + ","
      + wsJsonPair("force", c.force) + ","
      + wsJsonPair("area", c.area) + ","
      + wsJsonPair("orientation_deg", ellipse.orientationDeg) + ","
      + wsJsonPair("major_axis_mm", ellipse.majorAxisMm) + ","
      + wsJsonPair("minor_axis_mm", ellipse.minorAxisMm) + ","
      + wsJsonPair("delta_x_mm", c.deltaX) + ","
      + wsJsonPair("delta_y_mm", c.deltaY) + ","
      + wsJsonPair("delta_force", c.deltaForce) + ","
      + wsJsonPair("delta_area", c.deltaArea) + ","
      + wsJsonPair("min_x_mm", bbox.minX) + ","
      + wsJsonPair("min_y_mm", bbox.minY) + ","
      + wsJsonPair("max_x_mm", bbox.maxX) + ","
      + wsJsonPair("max_y_mm", bbox.maxY) + ","
      + wsJsonPair("peak_x_mm", peak.xMm) + ","
      + wsJsonPair("peak_y_mm", peak.yMm) + ","
      + wsJsonPair("peak_x_norm", peak.xMm / ACTIVE_W_MM) + ","
      + wsJsonPair("peak_y_norm", peak.yMm / ACTIVE_H_MM) + ","
      + wsJsonPair("peak_force", peak.force)
      + "}");
  }
}

void sendContactSummary(LiveFrame frame) {
  ContactStats stats = contactStats(frame.contacts.contacts, frame.rasterEllipses);
  ws.broadcastText("{"
    + wsJsonPair("address", "/sensel_morph/contact_summary") + ","
    + wsJsonPair("frame_id", frame.frameId) + ","
    + wsJsonPair("count", frame.contacts.contacts.length) + ","
    + wsJsonPair("x_avg", stats.x / ACTIVE_W_MM) + ","
    + wsJsonPair("y_avg", stats.y / ACTIVE_H_MM) + ","
    + wsJsonPair("x_force_avg", stats.xWeighted / ACTIVE_W_MM) + ","
    + wsJsonPair("y_force_avg", stats.yWeighted / ACTIVE_H_MM) + ","
    + wsJsonPair("force_total", stats.totalForce) + ","
    + wsJsonPair("force_avg", stats.avgForce) + ","
    + wsJsonPair("area_avg", stats.area) + ","
    + wsJsonPair("spread", stats.avgDistance) + ","
    + wsJsonPair("weighted_spread", stats.avgWeightedDistance)
    + "}");
}

/** Compute pixel-edge bounding boxes from the current label raster. */
HashMap<Integer, RasterBounds> rasterBoundsByLabel(byte[] labels, int labelW, int labelH) {
  HashMap<Integer, int[]> raw = new HashMap<Integer, int[]>();
  if (labels == null || labelW <= 0 || labelH <= 0 || labels.length < labelW * labelH) {
    return new HashMap<Integer, RasterBounds>();
  }
  for (int i = 0; i < labelW * labelH; i++) {
    int label = labels[i] & 0xff;
    if (label == 255) continue;
    int x = i % labelW;
    int y = i / labelW;
    Integer key = Integer.valueOf(label);
    int[] b = raw.get(key);
    if (b == null) {
      raw.put(key, new int[] { x, y, x, y });
    } else {
      if (x < b[0]) b[0] = x;
      if (y < b[1]) b[1] = y;
      if (x > b[2]) b[2] = x;
      if (y > b[3]) b[3] = y;
    }
  }
  HashMap<Integer, RasterBounds> out = new HashMap<Integer, RasterBounds>();
  for (Integer key : raw.keySet()) {
    int[] b = raw.get(key);
    out.put(key, new RasterBounds(
      b[0] * ACTIVE_W_MM / labelW,
      b[1] * ACTIVE_H_MM / labelH,
      (b[2] + 1) * ACTIVE_W_MM / labelW,
      (b[3] + 1) * ACTIVE_H_MM / labelH
    ));
  }
  return out;
}

RasterBounds contactBounds(SenselContact c, HashMap<Integer, RasterBounds> bounds) {
  RasterBounds b = bounds.get(Integer.valueOf(c.id));
  if (b != null) return b;
  return new RasterBounds(c.minX, c.minY, c.maxX, c.maxY);
}

class ContactStats {
  float x;
  float y;
  float avgForce;
  float avgDistance;
  float area;
  float xWeighted;
  float yWeighted;
  float totalForce;
  float avgWeightedDistance;
}

ContactStats contactStats(SenselContact[] contacts, HashMap<Integer, RasterEllipse> ellipses) {
  ContactStats stats = new ContactStats();
  if (contacts.length == 0) return stats;
  for (SenselContact c : contacts) {
    RasterEllipse e = contactEllipse(c, ellipses);
    stats.x += e.xMm;
    stats.y += e.yMm;
    stats.totalForce += c.force;
    stats.area += c.area;
  }
  stats.x /= contacts.length;
  stats.y /= contacts.length;
  stats.avgForce = stats.totalForce / contacts.length;
  stats.area /= contacts.length;
  if (stats.totalForce > 0) {
    for (SenselContact c : contacts) {
      RasterEllipse e = contactEllipse(c, ellipses);
      stats.xWeighted += e.xMm * c.force;
      stats.yWeighted += e.yMm * c.force;
    }
    stats.xWeighted /= stats.totalForce;
    stats.yWeighted /= stats.totalForce;
  } else {
    stats.xWeighted = stats.x;
    stats.yWeighted = stats.y;
  }
  for (SenselContact c : contacts) {
    RasterEllipse e = contactEllipse(c, ellipses);
    stats.avgDistance += dist(e.xMm, e.yMm, stats.x, stats.y);
    stats.avgWeightedDistance += dist(e.xMm, e.yMm, stats.xWeighted, stats.yWeighted);
  }
  stats.avgDistance /= contacts.length;
  stats.avgWeightedDistance /= contacts.length;
  return stats;
}

float averageContactDistance(SenselContact[] contacts, HashMap<Integer, RasterEllipse> ellipses) {
  if (contacts.length < 2) return 0;
  ContactStats stats = contactStats(contacts, ellipses);
  return stats.avgDistance;
}

/** Prefer fresh raster peaks, falling back to firmware-provided peak fields. */
RasterPeak contactPeak(SenselContact c, HashMap<Integer, RasterPeak> peaks) {
  RasterPeak p = peaks.get(Integer.valueOf(c.id));
  if (p != null) return p;
  return new RasterPeak(c.peakX, c.peakY, c.peakForce);
}

/** Prefer fresh raster ellipses, falling back to firmware-provided ellipse fields. */
RasterEllipse contactEllipse(SenselContact c, HashMap<Integer, RasterEllipse> ellipses) {
  RasterEllipse e = ellipses.get(Integer.valueOf(c.id));
  if (e != null) return e;
  return new RasterEllipse(c.x, c.y, c.orientation, c.majorAxis, c.minorAxis, round(c.area));
}

String contactStateName(int state) {
  if (state == 1) return "start";
  if (state == 2) return "move";
  if (state == 3) return "end";
  return state == 0 ? "invalid" : str(state);
}

boolean hasDrawablePeak(SenselContact c) {
  boolean hasCoordinate = abs(c.peakX) > 0.0001 || abs(c.peakY) > 0.0001;
  return c.peakForce > 0
    && hasCoordinate
    && c.peakX >= 0
    && c.peakX <= ACTIVE_W_MM
    && c.peakY >= 0
    && c.peakY <= ACTIVE_H_MM;
}

void normalizePeakCoordinates(SenselContact c) {
  if (peakLooksFixedPoint(c)) {
    c.peakX *= 256.0;
    c.peakY *= 256.0;
  }
}

boolean peakLooksFixedPoint(SenselContact c) {
  if (c.peakForce <= 0 || c.peakX < 0 || c.peakY < 0) return false;
  float scaledX = c.peakX * 256.0;
  float scaledY = c.peakY * 256.0;
  if (scaledX < 0 || scaledX > ACTIVE_W_MM || scaledY < 0 || scaledY > ACTIVE_H_MM) return false;
  if (c.hasBounds) {
    float margin = 2.5;
    boolean scaledInBounds = pointInBox(scaledX, scaledY, c.minX - margin, c.minY - margin, c.maxX + margin, c.maxY + margin);
    boolean rawInBounds = pointInBox(c.peakX, c.peakY, c.minX - margin, c.minY - margin, c.maxX + margin, c.maxY + margin);
    return scaledInBounds && !rawInBounds;
  }
  return dist(c.peakX, c.peakY, c.x, c.y) > 25.0 && dist(scaledX, scaledY, c.x, c.y) < 25.0;
}

boolean pointInBox(float x, float y, float left, float top, float right, float bottom) {
  return x >= left && x <= right && y >= top && y <= bottom;
}
