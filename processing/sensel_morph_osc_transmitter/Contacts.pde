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

/** Send the native /sensel_morph contact stream plus optional compatibility streams. */
void sendContacts(LiveFrame frame) {
  SenselContact[] contacts = frame.contacts.contacts;
  osc.send("/sensel_morph/contacts", new Object[] { Integer.valueOf(frame.frameId), Integer.valueOf(contacts.length) });
  sendContactSummary(frame);
  for (SenselContact c : contacts) {
    RasterPeak peak = contactPeak(c, frame.rasterPeaks);
    RasterEllipse ellipse = contactEllipse(c, frame.rasterEllipses);
    osc.send("/sensel_morph/contact", new Object[] {
      Integer.valueOf(frame.frameId),
      Integer.valueOf(c.id),
      Integer.valueOf(c.state),
      Float.valueOf(ellipse.xMm),
      Float.valueOf(ellipse.yMm),
      Float.valueOf(c.force),
      Float.valueOf(c.area),
      Float.valueOf(ellipse.orientationDeg),
      Float.valueOf(ellipse.majorAxisMm),
      Float.valueOf(ellipse.minorAxisMm),
      Float.valueOf(c.deltaX),
      Float.valueOf(c.deltaY),
      Float.valueOf(c.deltaForce),
      Float.valueOf(c.deltaArea),
      Float.valueOf(c.minX),
      Float.valueOf(c.minY),
      Float.valueOf(c.maxX),
      Float.valueOf(c.maxY),
      Float.valueOf(peak.xMm),
      Float.valueOf(peak.yMm),
      Float.valueOf(peak.force)
    });
  }
  if (config.compat.contains("morphosc")) {
    sendMorphOscContacts(contacts, frame.rasterEllipses);
  }
  if (config.compat.contains("senselosc")) {
    sendSenselOscContacts(frame, frame.rasterPeaks, frame.rasterEllipses);
  }
}

void sendContactSummary(LiveFrame frame) {
  ContactStats stats = contactStats(frame.contacts.contacts, frame.rasterEllipses);
  osc.send("/sensel_morph/contact_summary", new Object[] {
    Integer.valueOf(frame.frameId),
    Integer.valueOf(frame.contacts.contacts.length),
    Float.valueOf(stats.x / ACTIVE_W_MM),
    Float.valueOf(stats.y / ACTIVE_H_MM),
    Float.valueOf(stats.xWeighted / ACTIVE_W_MM),
    Float.valueOf(stats.yWeighted / ACTIVE_H_MM),
    Float.valueOf(stats.totalForce),
    Float.valueOf(stats.avgForce),
    Float.valueOf(stats.area),
    Float.valueOf(stats.avgDistance),
    Float.valueOf(stats.avgWeightedDistance)
  });
}

void sendMorphOscContacts(SenselContact[] contacts, HashMap<Integer, RasterEllipse> ellipses) {
  osc.send("/num_contacts", new Object[] { Integer.valueOf(contacts.length) });
  if (contacts.length == 0) return;
  osc.send("/spread", new Object[] { Float.valueOf(averageContactDistance(contacts, ellipses)) });
  float totalForce = 0;
  for (SenselContact c : contacts) totalForce += c.force;
  osc.send("/total_force", new Object[] { Float.valueOf(totalForce) });
  for (SenselContact c : contacts) {
    RasterEllipse e = contactEllipse(c, ellipses);
    osc.send("/lifecycle", new Object[] { Integer.valueOf(c.id), contactStateName(c.state) });
    osc.send("/x_position", new Object[] { Integer.valueOf(c.id), Float.valueOf(e.xMm) });
    osc.send("/y_position", new Object[] { Integer.valueOf(c.id), Float.valueOf(e.yMm) });
    osc.send("/force", new Object[] { Integer.valueOf(c.id), Float.valueOf(c.force) });
  }
}

void sendSenselOscContacts(LiveFrame frame, HashMap<Integer, RasterPeak> peaks, HashMap<Integer, RasterEllipse> ellipses) {
  SenselContact[] contacts = frame.contacts.contacts;
  ContactStats stats = contactStats(contacts, ellipses);
  osc.send("/contactAvg", new Object[] {
    Integer.valueOf(0), Integer.valueOf(contacts.length), Float.valueOf(stats.x), Float.valueOf(stats.y),
    Float.valueOf(stats.avgForce), Float.valueOf(stats.avgDistance), Integer.valueOf(round(stats.area)),
    Float.valueOf(stats.xWeighted), Float.valueOf(stats.yWeighted), Float.valueOf(stats.totalForce),
    Float.valueOf(stats.avgWeightedDistance)
  });
  int[] updated = new int[16];
  for (SenselContact c : contacts) {
    if (c.id >= 0 && c.id < updated.length) updated[c.id] = 1;
    RasterEllipse e = contactEllipse(c, ellipses);
    RasterPeak p = contactPeak(c, peaks);
    float dist = dist(e.xMm, e.yMm, stats.x, stats.y);
    float wdist = dist(e.xMm, e.yMm, stats.xWeighted, stats.yWeighted);
    osc.send("/contact", new Object[] {
      Integer.valueOf(0), Integer.valueOf(c.id), Integer.valueOf(c.state), Float.valueOf(e.xMm), Float.valueOf(e.yMm),
      Float.valueOf(c.force), Integer.valueOf(round(c.area)), Float.valueOf(dist), Float.valueOf(wdist),
      Float.valueOf(e.orientationDeg), Float.valueOf(e.majorAxisMm), Float.valueOf(e.minorAxisMm)
    });
    osc.send("/contactDelta", new Object[] {
      Integer.valueOf(0), Integer.valueOf(c.id), Integer.valueOf(c.state), Float.valueOf(c.deltaX), Float.valueOf(c.deltaY),
      Float.valueOf(c.deltaForce), Integer.valueOf(round(c.deltaArea))
    });
    osc.send("/contactBB", new Object[] {
      Integer.valueOf(0), Integer.valueOf(c.id), Integer.valueOf(c.state), Float.valueOf(c.minX), Float.valueOf(c.minY),
      Float.valueOf(c.maxX), Float.valueOf(c.maxY)
    });
    osc.send("/contactPeak", new Object[] {
      Integer.valueOf(0), Integer.valueOf(c.id), Integer.valueOf(c.state), Float.valueOf(p.xMm), Float.valueOf(p.yMm), Float.valueOf(p.force)
    });
  }
  Object[] sync = new Object[17];
  sync[0] = Integer.valueOf(0);
  for (int i = 0; i < 16; i++) sync[i + 1] = Integer.valueOf(updated[i]);
  osc.send("/sync", sync);
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
