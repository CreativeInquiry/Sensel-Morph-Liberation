import processing.opengl.PGraphicsOpenGL;
import java.io.File;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.concurrent.ConcurrentLinkedQueue;

// Receives live OSC from sensel_morph_osc / Processing transmitters and displays
// the Sensel Morph pressure, label, contact, and accelerometer channels.

final int SENSOR_W = 185;
final int SENSOR_H = 105;
final float ACTIVE_W_MM = 230.0;
final float ACTIVE_H_MM = 130.0;
final int OSC_PORT = 1560;
final int TEXTURE_SAMPLING_NEAREST = POINT;
final int TEXTURE_SAMPLING_LINEAR = 4;
final int SURFACE_SAMPLING_NEAREST = 0;
final int SURFACE_SAMPLING_LINEAR = 1;
final int CHANNEL_TIMEOUT_MS = 2000;

boolean showHelp = true;
boolean normalizePressureDisplay = false;
boolean accelerometerView = false;
int surfaceSamplingModeIndex = SURFACE_SAMPLING_NEAREST;
int displayMode = 7;

PGraphics pressureBuffer;
PGraphics labelsBuffer;
PGraphics contactsBuffer;

PImage pressureImage;
PImage labelsImage;
DecodedContactsFrame contactFrame = new DecodedContactsFrame(-1, new SenselContact[0]);
ContactSummaryFrame contactSummaryFrame = new ContactSummaryFrame();
AccelFrame accelFrame = new AccelFrame();
byte[] latestPressureBytes = new byte[0];
byte[] latestLabelValues = new byte[0];

int latestFrameId = -1;
int latestPressureFrameId = -1;
int latestLabelsFrameId = -1;
int latestLabelsWidth = 0;
int latestLabelsHeight = 0;
int latestContactsFrameId = -1;
int latestPressureWidth = 0;
int latestPressureHeight = 0;
int latestPressureBitDepth = 0;
float latestPressureMax = 0;
int lastPressureMillis = 0;
int lastLabelsMillis = 0;
int lastContactsMillis = 0;
String latestStatus = "waiting for OSC on UDP " + OSC_PORT;
String latestRasterTransport = "unknown";
String deviceSerial = "";
boolean outputCalibrated = false;
int packetsReceived = 0;
int messagesReceived = 0;
int droppedPackets = 0;
long lastMessageMillis = 0;
int lastFrameFpsMillis = 0;
int lastFpsFrameId = -1;
float frameReceiveFps = 0;
PVector smoothedAccelUp = null;
PVector[] slabLabelPositions = new PVector[4];
float accelCameraYaw = radians(3.0);
float accelCameraPitch = radians(-15.0);
AccelFrame[] accelHistory = new AccelFrame[1280];
int accelHistoryWrite = 0;
int accelHistoryCount = 0;

final float ACCEL_SLAB_W = 560.0;
final float ACCEL_SLAB_D = ACCEL_SLAB_W * 9.0 / 16.0;
final float ACCEL_SLAB_T = ACCEL_SLAB_W * 0.166 / 16.0;
final float ACCEL_AXIS_LENGTH = ACCEL_SLAB_W * 0.70;
final float ACCEL_CAMERA_DISTANCE = 885.44;
final float ACCEL_WORLD_G_COUNTS = 15600.0;
final float ACCEL_SLAB_SMOOTHING = 0.333;

DatagramSocket oscSocket;
Thread oscThread;
volatile boolean oscRunning = false;
ConcurrentLinkedQueue<OscMessage> oscQueue = new ConcurrentLinkedQueue<OscMessage>();

HashMap<Integer, RasterChunks> pressureChunks = new HashMap<Integer, RasterChunks>();
HashMap<Integer, RasterChunks> labelsChunks = new HashMap<Integer, RasterChunks>();
HashMap<Integer, ArrayList<SenselContact>> contactsByFrame = new HashMap<Integer, ArrayList<SenselContact>>();
HashMap<Integer, Integer> expectedContactsByFrame = new HashMap<Integer, Integer>();

int[] labelPalette = {
  0xffff0000, 0xff00ff00, 0xff0060ff, 0xffffff00,
  0xff00ffff, 0xffff00ff, 0xffff8000, 0xff80ff00,
  0xff0080ff, 0xffff0080, 0xff80ffff, 0xffff80ff,
  0xffc0c0c0, 0xffffffff, 0xff808080, 0xff800000
};

void setup() {
  size(1280, 720, P3D);
  surface.setTitle("Sensel Morph OSC Receiver");
  noSmooth();
  frameRate(60);
  createSurfaceBuffers();
  startOscReceiver();
}

void draw() {
  drainOscMessages();
  clearStaleChannels();
  updateSurfaceBuffers(getPressureImage(), getLabelImage());

  background(30);
  if (accelerometerView) {
    drawAccelerometerView();
  } else {
    camera();
    hint(DISABLE_DEPTH_TEST);
    drawCurrentLayers();
    hint(ENABLE_DEPTH_TEST);
  }
  if (showHelp) {
    drawHud();
  }
}

// User-facing accessors for sketches that want to reuse the received data.
// Raw images are the received raster size, not the 1280x720 display buffers.
PImage getPressureImage() {
  return pressureImage;
}

PImage getRawPressureImage() {
  return pressureImage;
}

int getFrameId() {
  return latestFrameId;
}

int getPressureFrameId() {
  return latestPressureFrameId;
}

int getPressureWidth() {
  return latestPressureWidth;
}

int getPressureHeight() {
  return latestPressureHeight;
}

int getPressureBitDepth() {
  return latestPressureBitDepth;
}

float getPressureMax() {
  return latestPressureMax;
}

byte[] getPressureBytes() {
  return Arrays.copyOf(latestPressureBytes, pressureByteCount());
}

byte[] getRawPressureBytes() {
  return getPressureBytes();
}

int getPressureValueAt(int x, int y) {
  if (x < 0 || y < 0 || x >= latestPressureWidth || y >= latestPressureHeight) {
    return 0;
  }
  int i = y * latestPressureWidth + x;
  if (latestPressureBitDepth == 16) {
    int byteIndex = i * 2;
    if (byteIndex + 1 >= latestPressureBytes.length) {
      return 0;
    }
    return (latestPressureBytes[byteIndex] & 0xff) | ((latestPressureBytes[byteIndex + 1] & 0xff) << 8);
  }
  if (i >= latestPressureBytes.length) {
    return 0;
  }
  return latestPressureBytes[i] & 0xff;
}

PImage getLabelImage() {
  return labelsImage;
}

PImage getRawLabelImage() {
  return labelsImage;
}

int getLabelFrameId() {
  return latestLabelsFrameId;
}

int getLabelWidth() {
  return latestLabelsWidth;
}

int getLabelHeight() {
  return latestLabelsHeight;
}

byte[] getLabelIds() {
  return Arrays.copyOf(latestLabelValues, latestLabelsWidth * latestLabelsHeight);
}

byte[] getRawLabelIds() {
  return getLabelIds();
}

int getLabelIdAt(int x, int y) {
  if (x < 0 || y < 0 || x >= latestLabelsWidth || y >= latestLabelsHeight) {
    return 0;
  }
  int i = y * latestLabelsWidth + x;
  if (i >= latestLabelValues.length) {
    return 0;
  }
  return latestLabelValues[i] & 0xff;
}

SenselContact[] getRawContacts() {
  return Arrays.copyOf(contactFrame.contacts, contactFrame.contacts.length);
}

int getContactsFrameId() {
  return latestContactsFrameId;
}

int getContactCount() {
  return contactFrame.contacts.length;
}

float getReceiveFps() {
  return frameReceiveFps;
}

String getDeviceSerial() {
  return deviceSerial.length() > 0 ? deviceSerial : "unknown";
}

boolean isOutputCalibrated() {
  return outputCalibrated;
}

SenselContactInfo[] getContacts() {
  return getContactObjects(g);
}

SenselContactInfo[] getContacts(PGraphics pg) {
  return getContactObjects(pg);
}

SenselContactInfo[] getContactObjects() {
  return getContactObjects(g);
}

SenselContactInfo[] getContactObjects(PGraphics pg) {
  SenselContactInfo[] out = new SenselContactInfo[contactFrame.contacts.length];
  for (int i = 0; i < contactFrame.contacts.length; i++) {
    SenselContact c = contactFrame.contacts[i];
    out[i] = new SenselContactInfo(c, getContactBoundingBox(c, pg), getFirmwareBoundingBox(c, pg), pg);
  }
  return out;
}

SenselContactInfo getContact(int id) {
  return getContact(id, g);
}

SenselContactInfo getContact(int id, PGraphics pg) {
  for (SenselContact c : contactFrame.contacts) {
    if (c.id == id) {
      return new SenselContactInfo(c, getContactBoundingBox(c, pg), getFirmwareBoundingBox(c, pg), pg);
    }
  }
  return null;
}

SenselBoundingBox[] getBoundingBoxes() {
  return getBoundingBoxes(g);
}

SenselBoundingBox[] getBoundingBoxes(PGraphics pg) {
  ArrayList<SenselBoundingBox> boxes = new ArrayList<SenselBoundingBox>();
  for (SenselContact c : contactFrame.contacts) {
    SenselBoundingBox bbox = getContactBoundingBox(c, pg);
    if (bbox != null) {
      boxes.add(bbox);
    }
  }
  return boxes.toArray(new SenselBoundingBox[boxes.size()]);
}

SenselBoundingBox getBoundingBox(int id) {
  return getBoundingBox(id, g);
}

SenselBoundingBox getBoundingBox(int id, PGraphics pg) {
  for (SenselContact c : contactFrame.contacts) {
    if (c.id == id) {
      return getContactBoundingBox(c, pg);
    }
  }
  return null;
}

void createSurfaceBuffers() {
  pressureBuffer = createGraphics(width, height, P2D);
  labelsBuffer = createGraphics(width, height, P2D);
  contactsBuffer = createGraphics(width, height, P2D);
  configureSurfaceBuffer(pressureBuffer);
  configureSurfaceBuffer(labelsBuffer);
  configureSurfaceBuffer(contactsBuffer);
}

void configureSurfaceBuffer(PGraphics pg) {
  pg.beginDraw();
  applyPressureSampling(pg);
  pg.noSmooth();
  pg.clear();
  pg.endDraw();
}

void updateSurfaceBuffers(PImage pressure, PImage labels) {
  drawPressureBuffer(pressure);
  drawLabelsBuffer(labels);
  drawContactsBuffer();
}

void drawPressureBuffer(PImage pressure) {
  pressureBuffer.beginDraw();
  applyPressureSampling(pressureBuffer);
  pressureBuffer.clear();
  if (pressure != null) {
    pressureBuffer.image(pressure, 0, 0, pressureBuffer.width, pressureBuffer.height);
  }
  pressureBuffer.endDraw();
}

void drawLabelsBuffer(PImage labels) {
  labelsBuffer.beginDraw();
  applyNearestSampling(labelsBuffer);
  labelsBuffer.clear();
  if (labels != null) {
    labelsBuffer.image(labels, 0, 0, labelsBuffer.width, labelsBuffer.height);
  }
  labelsBuffer.endDraw();
}

void drawContactsBuffer() {
  contactsBuffer.beginDraw();
  applyNearestSampling(contactsBuffer);
  contactsBuffer.clear();
  if (getContactsFrameId() >= 0) {
    drawContactsInto(contactsBuffer);
  }
  contactsBuffer.endDraw();
}

void drawCurrentLayers() {
  blendMode(BLEND);
  noTint();

  if ((displayMode & 1) != 0 && getPressureImage() != null) {
    applyPressureSampling(g);
    image(pressureBuffer, 0, 0);
  }

  if ((displayMode & 2) != 0 && getLabelImage() != null) {
    applyNearestSampling(g);
    blendMode(BLEND);
    tint(255, displayMode == 2 ? 255 : 102);
    image(labelsBuffer, 0, 0);
    noTint();
    blendMode(BLEND);
  }

  if ((displayMode & 4) != 0 && getContactCount() > 0) {
    applyNearestSampling(g);
    blendMode(BLEND);
    image(contactsBuffer, 0, 0);
    blendMode(BLEND);
  }

}

void drawContactsInto(PGraphics pg) {
  pg.noFill();
  pg.strokeWeight(1);
  pg.textAlign(CENTER, CENTER);
  pg.textSize(10);

  for (SenselContactInfo c : getContacts(pg)) {
    int col = labelColor(c.id);
    float px = c.screenPosition.x;
    float py = c.screenPosition.y;
    float axisW = c.axisScreenWidth;
    float axisH = c.axisScreenHeight;

    if (c.bbox != null) {
      pg.noFill();
      pg.stroke(red(col), green(col), blue(col), 150);
      pg.rectMode(CORNERS);
      pg.rect(c.bbox.left, c.bbox.top, c.bbox.right, c.bbox.bottom);
      pg.rectMode(CORNER);
    }

    if (c.hasDelta) {
      float dx = c.deltaScreen.x * 3.0;
      float dy = c.deltaScreen.y * 3.0;
      drawVector(pg, px, py, px + dx, py + dy, col);
    }

    pg.stroke(red(col), green(col), blue(col), 255);

    pg.noFill();
    pg.pushMatrix();
    pg.translate(px, py);
    pg.rotate(radians(c.orientation));
    pg.ellipse(0, 0, max(2, axisW), max(2, axisH));
    pg.popMatrix();

    if (c.hasPeak) {
      drawPeakCrosshair(pg, c.peakScreen.x, c.peakScreen.y);
    }

    pg.noStroke();
    pg.fill(0, 170);
    pg.ellipse(px, py, 16, 16);
    pg.fill(255);
    pg.text(str(c.id), px, py);
  }
  pg.noFill();
}

float contactScreenX(float xMm, PGraphics pg) {
  return xMm / ACTIVE_W_MM * pg.width;
}

float contactScreenY(float yMm, PGraphics pg) {
  return yMm / ACTIVE_H_MM * pg.height;
}

PVector contactScreenVector(float xMm, float yMm, PGraphics pg) {
  if (pg == null) {
    return new PVector(0, 0);
  }
  return new PVector(contactScreenX(xMm, pg), contactScreenY(yMm, pg));
}

PVector contactDeltaScreenVector(float dxMm, float dyMm, PGraphics pg) {
  if (pg == null) {
    return new PVector(0, 0);
  }
  return new PVector(dxMm / ACTIVE_W_MM * pg.width, dyMm / ACTIVE_H_MM * pg.height);
}

int contactOverlayRasterWidth() {
  if (latestLabelsWidth > 0) {
    return latestLabelsWidth;
  }
  if (latestPressureWidth > 0) {
    return latestPressureWidth;
  }
  return SENSOR_W;
}

int contactOverlayRasterHeight() {
  if (latestLabelsHeight > 0) {
    return latestLabelsHeight;
  }
  if (latestPressureHeight > 0) {
    return latestPressureHeight;
  }
  return SENSOR_H;
}

SenselBoundingBox getContactBoundingBox(SenselContact c, PGraphics pg) {
  if (pg == null) {
    return null;
  }
  float[] labelBounds = contactLabelScreenBounds(c.id, pg);
  if (labelBounds != null) {
    return new SenselBoundingBox(c.id, labelBounds[0], labelBounds[1], labelBounds[2], labelBounds[3], "label", true);
  }
  return getFirmwareBoundingBox(c, pg);
}

SenselBoundingBox getFirmwareBoundingBox(SenselContact c, PGraphics pg) {
  if (pg == null || !c.hasBounds) {
    return null;
  }
  float[] bounds = contactFirmwareScreenBounds(c, pg);
  return new SenselBoundingBox(c.id, bounds[0], bounds[1], bounds[2], bounds[3], "firmware", false);
}

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
  int rasterW = contactOverlayRasterWidth();
  int rasterH = contactOverlayRasterHeight();
  int minX = constrain(floor(c.minX / ACTIVE_W_MM * rasterW - 0.5), 0, rasterW);
  int minY = constrain(floor(c.minY / ACTIVE_H_MM * rasterH - 0.5), 0, rasterH);
  int maxX = constrain(ceil(c.maxX / ACTIVE_W_MM * rasterW + 0.5), 0, rasterW);
  int maxY = constrain(ceil(c.maxY / ACTIVE_H_MM * rasterH + 0.5), 0, rasterH);
  return rasterBoundsToScreen(minX, minY, maxX, maxY, rasterW, rasterH, pg);
}

float[] rasterBoundsToScreen(int minX, int minY, int maxX, int maxY, int rasterW, int rasterH, PGraphics pg) {
  return new float[] {
    minX / (float) rasterW * pg.width,
    minY / (float) rasterH * pg.height,
    maxX / (float) rasterW * pg.width,
    maxY / (float) rasterH * pg.height
  };
}

void drawPeakCrosshair(PGraphics pg, float x, float y) {
  pg.stroke(0, 200);
  pg.strokeWeight(4);
  pg.line(x - 5, y, x + 5, y);
  pg.line(x, y - 5, x, y + 5);
  
  pg.stroke(255);
  pg.strokeWeight(1);
  pg.line(x - 5, y, x + 5, y);
  pg.line(x, y - 5, x, y + 5);
}

void drawVector(PGraphics pg, float x0, float y0, float x1, float y1, int col) {
  pg.stroke(red(col), green(col), blue(col), 210);
  pg.strokeWeight(1);
  pg.line(x0, y0, x1, y1);
  float angle = atan2(y1 - y0, x1 - x0);
  float lengthPx = dist(x0, y0, x1, y1);
  if (lengthPx > 3) {
    float arrow = min(8, lengthPx * 0.45);
    pg.line(x1, y1, x1 - cos(angle - PI / 6.0) * arrow, y1 - sin(angle - PI / 6.0) * arrow);
    pg.line(x1, y1, x1 - cos(angle + PI / 6.0) * arrow, y1 - sin(angle + PI / 6.0) * arrow);
  }
}

void drawAccelerometerView() {
  background(0);

  if (!accelFrame.valid) {
    camera();
    hint(DISABLE_DEPTH_TEST);
    fill(220);
    textAlign(LEFT, TOP);
    textSize(12);
    text("accelerometer view: waiting for /sensel_morph/accelerometer", 12, 12);
    hint(ENABLE_DEPTH_TEST);
    return;
  }

  PVector upLocal = displayAccelUpVector(accelFrame);
  perspective(PI / 3.0, float(width) / float(height), 1, 5000);
  applyAccelOrbitCamera();

  pushMatrix();
  translate(width / 2.0, height / 2.0, 0);
  drawAccelReferenceAxes(ACCEL_AXIS_LENGTH);
  applyAccelOrientation(upLocal);
  drawAccelWireSlab(ACCEL_SLAB_W, ACCEL_SLAB_D, ACCEL_SLAB_T);
  captureSlabLabelPositions(ACCEL_SLAB_W, ACCEL_SLAB_D, ACCEL_SLAB_T);
  popMatrix();

  camera();
  hint(DISABLE_DEPTH_TEST);
  drawSlabCornerLabels();
  drawAccelTimelines();
  hint(ENABLE_DEPTH_TEST);
}

PVector displayAccelUpVector(AccelFrame frame) {
  PVector rawUp = new PVector(frame.x, -frame.y, frame.z);
  if (rawUp.magSq() < 0.000001) {
    rawUp.set(0, 0, 1);
  } else {
    rawUp.normalize();
  }

  if (smoothedAccelUp == null) {
    smoothedAccelUp = rawUp.copy();
  } else {
    smoothedAccelUp.lerp(rawUp, ACCEL_SLAB_SMOOTHING);
    smoothedAccelUp.normalize();
  }
  return smoothedAccelUp.copy();
}

void applyAccelOrbitCamera() {
  float cx = width / 2.0;
  float cy = height / 2.0;
  float cz = 0;
  float cp = cos(accelCameraPitch);
  float eyeX = cx + ACCEL_CAMERA_DISTANCE * sin(accelCameraYaw) * cp;
  float eyeY = cy + ACCEL_CAMERA_DISTANCE * sin(accelCameraPitch);
  float eyeZ = cz + ACCEL_CAMERA_DISTANCE * cos(accelCameraYaw) * cp;
  camera(eyeX, eyeY, eyeZ, cx, cy, cz, 0, 1, 0);
}

/** Orient the slab so its local up vector aligns with measured gravity. */
void applyAccelOrientation(PVector upLocal) {
  PVector up = upLocal.copy();
  if (up.magSq() < 0.000001) {
    return;
  }
  up.normalize();

  PVector localReference = new PVector(1, 0, 0);
  PVector worldReference = new PVector(1, 0, 0);
  if (abs(up.dot(localReference)) > 0.96) {
    localReference = new PVector(0, 1, 0);
    worldReference = new PVector(0, 0, 1);
  }

  PVector localRight = rejectFromAxis(localReference, up);
  PVector localForward = cross(up, localRight);
  PVector worldUp = new PVector(0, -1, 0);
  PVector worldRight = rejectFromAxis(worldReference, worldUp);
  PVector worldForward = cross(worldUp, worldRight);

  applyBasisMatrix(localRight, localForward, up, worldRight, worldForward, worldUp);
}

PVector rejectFromAxis(PVector vector, PVector axis) {
  PVector out = vector.copy();
  out.sub(PVector.mult(axis, vector.dot(axis)));
  if (out.magSq() < 0.000001) {
    out.set(1, 0, 0);
  }
  out.normalize();
  return out;
}

PVector cross(PVector a, PVector b) {
  return new PVector(
    a.y * b.z - a.z * b.y,
    a.z * b.x - a.x * b.z,
    a.x * b.y - a.y * b.x
  );
}

void applyBasisMatrix(
  PVector localRight,
  PVector localForward,
  PVector localUp,
  PVector worldRight,
  PVector worldForward,
  PVector worldUp
) {
  float m00 = worldRight.x * localRight.x + worldForward.x * localForward.x + worldUp.x * localUp.x;
  float m01 = worldRight.x * localRight.y + worldForward.x * localForward.y + worldUp.x * localUp.y;
  float m02 = worldRight.x * localRight.z + worldForward.x * localForward.z + worldUp.x * localUp.z;

  float m10 = worldRight.y * localRight.x + worldForward.y * localForward.x + worldUp.y * localUp.x;
  float m11 = worldRight.y * localRight.y + worldForward.y * localForward.y + worldUp.y * localUp.y;
  float m12 = worldRight.y * localRight.z + worldForward.y * localForward.z + worldUp.y * localUp.z;

  float m20 = worldRight.z * localRight.x + worldForward.z * localForward.x + worldUp.z * localUp.x;
  float m21 = worldRight.z * localRight.y + worldForward.z * localForward.y + worldUp.z * localUp.y;
  float m22 = worldRight.z * localRight.z + worldForward.z * localForward.z + worldUp.z * localUp.z;

  applyMatrix(
    m00, m01, m02, 0,
    m10, m11, m12, 0,
    m20, m21, m22, 0,
    0,   0,   0,   1
  );
}

void drawAccelWireSlab(float w, float d, float t) {
  float x = w / 2.0;
  float y = d / 2.0;
  float z = t / 2.0;

  stroke(255);
  strokeWeight(1);
  noFill();

  beginShape(LINES);
  slabEdge(-x, -y, -z,  x, -y, -z);
  slabEdge( x, -y, -z,  x,  y, -z);
  slabEdge( x,  y, -z, -x,  y, -z);
  slabEdge(-x,  y, -z, -x, -y, -z);

  slabEdge(-x, -y,  z,  x, -y,  z);
  slabEdge( x, -y,  z,  x,  y,  z);
  slabEdge( x,  y,  z, -x,  y,  z);
  slabEdge(-x,  y,  z, -x, -y,  z);

  slabEdge(-x, -y, -z, -x, -y,  z);
  slabEdge( x, -y, -z,  x, -y,  z);
  slabEdge( x,  y, -z,  x,  y,  z);
  slabEdge(-x,  y, -z, -x,  y,  z);
  endShape();
}

void slabEdge(float x0, float y0, float z0, float x1, float y1, float z1) {
  vertex(x0, y0, z0);
  vertex(x1, y1, z1);
}

/** Draw fixed world axes; the slab rotates relative to these axes. */
void drawAccelReferenceAxes(float axisLength) {
  noStroke();

  pushMatrix();
  rotateY(HALF_PI);
  fill(255, 64, 64, 135);
  drawCylinderAxis(3, axisLength, 24);
  popMatrix();

  pushMatrix();
  rotateX(-HALF_PI);
  fill(64, 255, 64, 135);
  drawCylinderAxis(3, axisLength, 24);
  popMatrix();

  fill(80, 150, 255, 135);
  drawCylinderAxis(3, axisLength, 24);
}

void drawCylinderAxis(float radius, float length, int sides) {
  beginShape(QUAD_STRIP);
  for (int i = 0; i <= sides; i++) {
    float a = TWO_PI * i / sides;
    float x = cos(a) * radius;
    float y = sin(a) * radius;
    vertex(x, y, 0);
    vertex(x, y, length);
  }
  endShape();

  beginShape(TRIANGLE_FAN);
  vertex(0, 0, 0);
  for (int i = sides; i >= 0; i--) {
    float a = TWO_PI * i / sides;
    vertex(cos(a) * radius, sin(a) * radius, 0);
  }
  endShape();

  beginShape(TRIANGLE_FAN);
  vertex(0, 0, length);
  for (int i = 0; i <= sides; i++) {
    float a = TWO_PI * i / sides;
    vertex(cos(a) * radius, sin(a) * radius, length);
  }
  endShape();
}

void captureSlabLabelPositions(float w, float d, float t) {
  float x = w / 2.0;
  float y = d / 2.0;
  float z = t / 2.0;
  captureSlabLabelPosition(0, -x,  y, z); // FL
  captureSlabLabelPosition(1, -x, -y, z); // BL
  captureSlabLabelPosition(2,  x,  y, z); // FR
  captureSlabLabelPosition(3,  x, -y, z); // BR
}

void captureSlabLabelPosition(int index, float x, float y, float z) {
  slabLabelPositions[index] = new PVector(screenX(x, y, z), screenY(x, y, z));
}

void drawSlabCornerLabels() {
  String[] labels = { "FL", "BL", "FR", "BR" };
  textAlign(CENTER, CENTER);
  textSize(11);
  for (int i = 0; i < labels.length; i++) {
    PVector p = slabLabelPositions[i];
    if (p == null) {
      continue;
    }
    fill(0, 185);
    noStroke();
    rect(p.x - 10, p.y - 7, 20, 14);
    fill(255);
    text(labels[i], p.x, p.y - 1);
  }
}

/** Draw scrolling X/Y/Z acceleration timelines near the top HUD. */
void drawAccelTimelines() {
  int plotX = 52;
  int plotY = 64;
  int plotW = width - plotX - 16;
  int laneH = 24;
  int laneGap = 5;
  int[] colors = { color(255, 64, 64), color(64, 255, 64), color(80, 150, 255) };
  String[] names = { "X", "Y", "Z" };

  noStroke();
  fill(0, 205);
  rect(0, plotY - 6, width, laneH * 3 + laneGap * 2 + 12);

  for (int axis = 0; axis < 3; axis++) {
    int y = plotY + axis * (laneH + laneGap);
    drawAccelLane(axis, names[axis], colors[axis], plotX, y, plotW, laneH);
  }
}

void drawAccelLane(int axis, String name, int col, int x, int y, int w, int h) {
  int midY = y + h / 2;
  strokeWeight(1);
  stroke(55);
  line(x, midY, x + w, midY);

  fill(col);
  noStroke();
  textAlign(RIGHT, CENTER);
  text(name, x - 8, midY);

  if (accelHistoryCount <= 0) {
    return;
  }

  stroke(col);
  strokeWeight(1);
  noFill();
  beginShape();
  for (int px = 0; px < w; px++) {
    int offset = w - 1 - px;
    AccelFrame f = accelHistoryFromLatest(offset);
    if (f == null) {
      continue;
    }
    float value = axisValue(f, axis);
    float yy = map(constrain(value, -ACCEL_WORLD_G_COUNTS, ACCEL_WORLD_G_COUNTS), -ACCEL_WORLD_G_COUNTS, ACCEL_WORLD_G_COUNTS, y + h - 2, y + 2);
    vertex(x + px, yy);
  }
  endShape();

  stroke(255, 160);
  line(x + w - 1, y, x + w - 1, y + h);
}

float axisValue(AccelFrame frame, int axis) {
  if (axis == 0) {
    return frame.x;
  }
  if (axis == 1) {
    return frame.y;
  }
  return frame.z;
}

AccelFrame accelHistoryFromLatest(int offset) {
  if (offset < 0 || offset >= accelHistoryCount) {
    return null;
  }
  int index = (accelHistoryWrite - 1 - offset + accelHistory.length) % accelHistory.length;
  return accelHistory[index];
}

void drawHud() {
  if (accelerometerView) {
    drawAccelerometerHud();
    return;
  }
  camera();
  hint(DISABLE_DEPTH_TEST);
  fill(0, 175);
  noStroke();
  rect(0, 0, width, 44);
  fill(220);
  textAlign(LEFT, TOP);
  textSize(12);
  String age = lastMessageMillis == 0 ? "never" : nf((millis() - lastMessageMillis) / 1000.0, 1, 2) + "s ago";
  String line1 = "OSC UDP " + OSC_PORT
    + "   frame " + latestFrameId
    + "   rx " + nf(frameReceiveFps, 1, 1) + " fps"
    + "   view " + (accelerometerView ? "accelerometer" : "surface")
    + "   mode " + displayMode + " " + displayModeName(displayMode)
    + "   pressure sampling " + surfaceSamplingName()
    + "   pressure " + pressureDisplayScaleName()
    + "   transport " + latestRasterTransport
    + "   last " + age;
  String line2 = latestStatus
    + "   pressure " + latestPressureWidth + "x" + latestPressureHeight + " " + latestPressureBitDepth + "-bit max " + nf(latestPressureMax, 1, 1)
    + "   labels frame " + latestLabelsFrameId
    + "   contacts " + getContactCount()
    + "   packets " + packetsReceived + " messages " + messagesReceived + " dropped " + droppedPackets;
  text(line1, 12, 7);
  text(line2, 12, 25);
  drawHudNumbers();
  hint(ENABLE_DEPTH_TEST);
}

void drawAccelerometerHud() {
  camera();
  hint(DISABLE_DEPTH_TEST);
  fill(0, 175);
  noStroke();
  rect(0, 0, width, 44);
  fill(220);
  textAlign(LEFT, TOP);
  textSize(12);
  String age = lastMessageMillis == 0 ? "never" : nf((millis() - lastMessageMillis) / 1000.0, 1, 2) + "s ago";
  if (accelFrame.valid) {
    float totalG = sqrt(sq(accelFrame.xG) + sq(accelFrame.yG) + sq(accelFrame.zG));
    text("OSC UDP " + OSC_PORT
      + "   frame " + latestFrameId
      + "   rx " + nf(frameReceiveFps, 1, 1) + " fps"
      + "   view accelerometer"
      + "   last " + age, 12, 7);
    text("raw [" + accelFrame.x + ", " + accelFrame.y + ", " + accelFrame.z + "]"
      + "   g [" + nf(accelFrame.xG, 1, 2) + ", " + nf(accelFrame.yG, 1, 2) + ", " + nf(accelFrame.zG, 1, 2) + "]"
      + "   total accel " + nf(totalG, 1, 2) + "g", 12, 25);
  } else {
    text("OSC UDP " + OSC_PORT + "   view accelerometer   waiting for accelerometer data", 12, 7);
  }
  hint(ENABLE_DEPTH_TEST);
}

void drawHudNumbers() {
  ArrayList<String> lines = new ArrayList<String>();
  lines.add("serial_number: " + getDeviceSerial());
  lines.add("calibrated: " + (isOutputCalibrated() ? "yes" : "no"));
  lines.add("");

  if (accelFrame.valid) {
    lines.add("accel_x: " + accelFrame.x);
    lines.add("accel_y: " + accelFrame.y);
    lines.add("accel_z: " + accelFrame.z);
  } else {
    lines.add("accel: no data");
  }

  lines.add("");
  if (contactSummaryFrame.valid) {
    lines.add("summary_frame_id: " + contactSummaryFrame.frameId);
    lines.add("summary_count: " + contactSummaryFrame.count);
    lines.add("x_avg: " + fmt(contactSummaryFrame.xAvg));
    lines.add("y_avg: " + fmt(contactSummaryFrame.yAvg));
    lines.add("x_force_avg: " + fmt(contactSummaryFrame.xForceAvg));
    lines.add("y_force_avg: " + fmt(contactSummaryFrame.yForceAvg));
    lines.add("force_total: " + fmt(contactSummaryFrame.forceTotal));
    lines.add("force_avg: " + fmt(contactSummaryFrame.forceAvg));
    lines.add("area_avg: " + fmt(contactSummaryFrame.areaAvg));
    lines.add("spread_mm: " + fmt(contactSummaryFrame.spread));
    lines.add("avg_weighted_distance_mm: " + fmt(contactSummaryFrame.avgWeightedDistance));
  } else {
    lines.add("contact_summary: no data");
  }

  textSize(12);
  float lineH = 14;
  float panelW = 300;
  float panelH = lines.size() * lineH + 12;
  float panelX = 0;
  float textX = 12;
  float panelY = 54;
  fill(0, 165);
  noStroke();
  rect(panelX, panelY, panelW, panelH);
  fill(230);
  textAlign(LEFT, TOP);
  for (int i = 0; i < lines.size(); i++) {
    text(lines.get(i), textX, panelY + 6 + i * lineH);
  }
  textAlign(LEFT, TOP);
}

String fmt(float value) {
  return nf(value, 1, 3);
}

void keyPressed() {
  if (key == 'h' || key == 'H') {
    showHelp = !showHelp;
  } else if (key == 'a' || key == 'A') {
    accelerometerView = !accelerometerView;
    if (accelerometerView) {
      smoothedAccelUp = null;
    }
  } else if (key == 's' || key == 'S') {
    surfaceSamplingModeIndex = (surfaceSamplingModeIndex + 1) % 2;
  } else if (key == 'n' || key == 'N') {
    normalizePressureDisplay = !normalizePressureDisplay;
  } else if (key >= '1' && key <= '7') {
    displayMode = key - '0';
  } else if (key == 'p' || key == 'P') {
    saveLayerScreenshots();
  }
}

void mouseDragged() {
  if (!accelerometerView) {
    return;
  }
  accelCameraYaw -= (mouseX - pmouseX) * 0.01;
  accelCameraPitch -= (mouseY - pmouseY) * 0.01;
  accelCameraPitch = constrain(accelCameraPitch, -HALF_PI + 0.08, HALF_PI - 0.08);
}

void saveLayerScreenshots() {
  File dir = new File(sketchPath("screenshots"));
  if (!dir.exists()) {
    dir.mkdirs();
  }

  String prefix = "sensel_" + frameCount;
  pressureBuffer.save(new File(dir, prefix + "_pressure.png").getAbsolutePath());
  labelsBuffer.save(new File(dir, prefix + "_labels.png").getAbsolutePath());
  saveContactsScreenshotWithBackground(new File(dir, prefix + "_contacts.png").getAbsolutePath());
  println("saved screenshots/" + prefix + "_pressure.png, _labels.png, _contacts.png");
}

void saveContactsScreenshotWithBackground(String path) {
  PGraphics composited = createGraphics(contactsBuffer.width, contactsBuffer.height, P2D);
  composited.beginDraw();
  composited.background(20, 20, 20);
  composited.image(contactsBuffer, 0, 0);
  composited.endDraw();
  composited.save(path);
}

void applyPressureSampling(PGraphics pg) {
  if (pg instanceof PGraphicsOpenGL) {
    ((PGraphicsOpenGL) pg).textureSampling(textureSamplingMode());
  }
}

void applyNearestSampling(PGraphics pg) {
  if (pg instanceof PGraphicsOpenGL) {
    ((PGraphicsOpenGL) pg).textureSampling(TEXTURE_SAMPLING_NEAREST);
  }
}

int textureSamplingMode() {
  return surfaceSamplingModeIndex == SURFACE_SAMPLING_LINEAR ? TEXTURE_SAMPLING_LINEAR : TEXTURE_SAMPLING_NEAREST;
}

String surfaceSamplingName() {
  return surfaceSamplingModeIndex == SURFACE_SAMPLING_LINEAR ? "linear" : "nearest";
}

String pressureDisplayScaleName() {
  return normalizePressureDisplay ? "normalized" : "absolute";
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
  return name;
}

void startOscReceiver() {
  try {
    oscSocket = new DatagramSocket(OSC_PORT);
    oscSocket.setReuseAddress(true);
  } catch (SocketException e) {
    latestStatus = "OSC socket error: " + e.getMessage();
    println(latestStatus);
    return;
  }

  oscRunning = true;
  oscThread = new Thread(new Runnable() {
    public void run() {
      byte[] buffer = new byte[65535];
      while (oscRunning) {
        DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
        try {
          oscSocket.receive(packet);
          packetsReceived++;
          parseOscPacket(packet.getData(), packet.getLength(), oscQueue);
        } catch (Exception e) {
          if (oscRunning) {
            droppedPackets++;
            println("OSC receive error: " + e.getMessage());
          }
        }
      }
    }
  });
  oscThread.setDaemon(true);
  oscThread.start();
}

void stop() {
  oscRunning = false;
  if (oscSocket != null) {
    oscSocket.close();
  }
  super.stop();
}

void drainOscMessages() {
  OscMessage msg;
  while ((msg = oscQueue.poll()) != null) {
    messagesReceived++;
    lastMessageMillis = millis();
    handleOscMessage(msg);
  }
}

void handleOscMessage(OscMessage msg) {
  if (msg.address.equals("/sensel_morph/status")) {
    String device = msg.args.size() > 0 ? asString(msg.args.get(0)) : "";
    int contentMask = msg.args.size() > 1 ? asInt(msg.args.get(1)) : 0;
    String pressureRes = msg.args.size() > 2 ? asString(msg.args.get(2)) : "";
    String pressureType = msg.args.size() > 3 ? asString(msg.args.get(3)) : "";
    boolean rle = msg.args.size() > 4 && asInt(msg.args.get(4)) != 0;
    if (msg.args.size() > 5) {
      String serial = asString(msg.args.get(5));
      if (serial.length() > 0 && !serial.equals("unknown")) {
        deviceSerial = serial;
      }
    }
    outputCalibrated = msg.args.size() > 6 && asInt(msg.args.get(6)) != 0;
    if (msg.args.size() > 1 && (contentMask & 0x01) == 0) {
      clearPressureChannel();
    }
    if (msg.args.size() > 1 && (contentMask & 0x02) == 0) {
      clearLabelChannel();
    }
    if (msg.args.size() > 1 && (contentMask & 0x04) == 0) {
      clearContactChannel();
    }
    latestStatus = device
      + "   content 0x" + hex(contentMask, 2)
      + "   " + pressureRes + "/" + pressureType
      + "   rle " + (rle ? "on" : "off");
    return;
  }

  if (msg.address.equals("/sensel_morph/frame")) {
    int frameId = asInt(msg.args.get(0));
    latestFrameId = frameId;
    noteFrameReceived(frameId);
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure")) {
    latestRasterTransport = "raw";
    if (msg.args.size() >= 6) {
      int frameId = asInt(msg.args.get(0));
      int w = asInt(msg.args.get(1));
      int h = asInt(msg.args.get(2));
      int bitDepth = asInt(msg.args.get(3));
      float maxValue = asFloat(msg.args.get(4));
      byte[] blob = asBytes(msg.args.get(5));
      acceptPressureRaster(frameId, w, h, bitDepth, maxValue, blob);
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure_rle")) {
    latestRasterTransport = "rle";
    if (msg.args.size() >= 7) {
      int frameId = asInt(msg.args.get(0));
      int w = asInt(msg.args.get(1));
      int h = asInt(msg.args.get(2));
      int bitDepth = asInt(msg.args.get(3));
      float maxValue = asFloat(msg.args.get(4));
      int decodedBytes = asInt(msg.args.get(5));
      byte[] blob = rleDecode(asBytes(msg.args.get(6)), decodedBytes);
      if (blob != null) {
        acceptPressureRaster(frameId, w, h, bitDepth, maxValue, blob);
      }
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure/start")) {
    latestRasterTransport = "raw";
    if (msg.args.size() >= 7) {
      int frameId = asInt(msg.args.get(0));
      pressureChunks.put(frameId, new RasterChunks(
        frameId,
        asInt(msg.args.get(1)),
        asInt(msg.args.get(2)),
        asInt(msg.args.get(3)),
        asFloat(msg.args.get(4)),
        asInt(msg.args.get(5)),
        asInt(msg.args.get(6))
      ));
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure_rle/start")) {
    latestRasterTransport = "rle";
    if (msg.args.size() >= 8) {
      int frameId = asInt(msg.args.get(0));
      pressureChunks.put(frameId, new RasterChunks(
        frameId,
        asInt(msg.args.get(1)),
        asInt(msg.args.get(2)),
        asInt(msg.args.get(3)),
        asFloat(msg.args.get(4)),
        asInt(msg.args.get(6)),
        asInt(msg.args.get(7)),
        true,
        asInt(msg.args.get(5))
      ));
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure/chunk")) {
    latestRasterTransport = "raw";
    acceptRasterChunk(msg, pressureChunks, true);
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure_rle/chunk")) {
    latestRasterTransport = "rle";
    acceptRasterChunk(msg, pressureChunks, true);
    return;
  }

  if (msg.address.equals("/sensel_morph/labels")) {
    latestRasterTransport = "raw";
    if (msg.args.size() >= 4) {
      acceptLabelRaster(asInt(msg.args.get(0)), asInt(msg.args.get(1)), asInt(msg.args.get(2)), asBytes(msg.args.get(3)));
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/labels_rle")) {
    latestRasterTransport = "rle";
    if (msg.args.size() >= 5) {
      int frameId = asInt(msg.args.get(0));
      int w = asInt(msg.args.get(1));
      int h = asInt(msg.args.get(2));
      int decodedBytes = asInt(msg.args.get(3));
      byte[] blob = rleDecode(asBytes(msg.args.get(4)), decodedBytes);
      if (blob != null) {
        acceptLabelRaster(frameId, w, h, blob);
      }
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/labels/start")) {
    latestRasterTransport = "raw";
    if (msg.args.size() >= 5) {
      int frameId = asInt(msg.args.get(0));
      labelsChunks.put(frameId, new RasterChunks(
        frameId,
        asInt(msg.args.get(1)),
        asInt(msg.args.get(2)),
        8,
        0,
        asInt(msg.args.get(3)),
        asInt(msg.args.get(4))
      ));
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/labels_rle/start")) {
    latestRasterTransport = "rle";
    if (msg.args.size() >= 6) {
      int frameId = asInt(msg.args.get(0));
      labelsChunks.put(frameId, new RasterChunks(
        frameId,
        asInt(msg.args.get(1)),
        asInt(msg.args.get(2)),
        8,
        0,
        asInt(msg.args.get(4)),
        asInt(msg.args.get(5)),
        true,
        asInt(msg.args.get(3))
      ));
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/labels/chunk")) {
    latestRasterTransport = "raw";
    acceptRasterChunk(msg, labelsChunks, false);
    return;
  }

  if (msg.address.equals("/sensel_morph/labels_rle/chunk")) {
    latestRasterTransport = "rle";
    acceptRasterChunk(msg, labelsChunks, false);
    return;
  }

  if (msg.address.equals("/sensel_morph/contacts")) {
    int frameId = asInt(msg.args.get(0));
    int count = msg.args.size() > 1 ? asInt(msg.args.get(1)) : 0;
    contactsByFrame.put(frameId, new ArrayList<SenselContact>());
    expectedContactsByFrame.put(frameId, count);
    pruneOldContactFrames(frameId);
    return;
  }

  if (msg.address.equals("/sensel_morph/contact")) {
    acceptContactMessage(msg);
    return;
  }

  if (msg.address.equals("/sensel_morph/contact_summary")) {
    acceptContactSummaryMessage(msg);
    return;
  }

  if (msg.address.equals("/sensel_morph/accelerometer")) {
    acceptAccelerometerMessage(msg);
    return;
  }

  if (msg.address.equals("/sensel_morph/sync")) {
    int frameId = asInt(msg.args.get(0));
    latestFrameId = frameId;
    publishContactFrameIfComplete(frameId, true);
  }
}

void noteFrameReceived(int frameId) {
  if (frameId == lastFpsFrameId) {
    return;
  }
  lastFpsFrameId = frameId;
  int now = millis();
  frameReceiveFps = updateFps(frameReceiveFps, lastFrameFpsMillis, now);
  lastFrameFpsMillis = now;
}

float updateFps(float currentFps, int previousMillis, int now) {
  if (previousMillis <= 0) {
    return currentFps;
  }
  int elapsed = max(1, now - previousMillis);
  float instant = 1000.0 / elapsed;
  if (currentFps <= 0) {
    return instant;
  }
  return currentFps * 0.85 + instant * 0.15;
}

void publishContactFrameIfComplete(int frameId, boolean syncArrived) {
  ArrayList<SenselContact> contacts = contactsByFrame.get(frameId);
  Integer expected = expectedContactsByFrame.get(frameId);
  if (expected == null) {
    if (contacts != null) {
      publishContactFrame(frameId, contacts);
    }
    return;
  }

  if (expected.intValue() == 0) {
    if (syncArrived) {
      publishContactFrame(frameId, new ArrayList<SenselContact>());
    }
    return;
  }

  if (contacts != null && contacts.size() >= expected.intValue()) {
    publishContactFrame(frameId, contacts);
  }
}

void publishContactFrame(int frameId, ArrayList<SenselContact> contacts) {
  contactFrame = new DecodedContactsFrame(frameId, contacts.toArray(new SenselContact[contacts.size()]));
  latestContactsFrameId = frameId;
  latestFrameId = frameId;
  lastContactsMillis = millis();
}

void acceptRasterChunk(OscMessage msg, HashMap<Integer, RasterChunks> stores, boolean pressure) {
  if (msg.args.size() < 4) {
    return;
  }
  int frameId = asInt(msg.args.get(0));
  RasterChunks chunks = stores.get(frameId);
  if (chunks == null) {
    return;
  }
  int chunkIndex = asInt(msg.args.get(1));
  byte[] blob = asBytes(msg.args.get(3));
  if (chunks.accept(chunkIndex, blob)) {
    byte[] complete = chunks.join();
    stores.remove(frameId);
    if (chunks.rle) {
      complete = rleDecode(complete, chunks.decodedBytes);
      if (complete == null) {
        return;
      }
    }
    if (pressure) {
      acceptPressureRaster(frameId, chunks.width, chunks.height, chunks.bitDepth, chunks.maxValue, complete);
    } else {
      acceptLabelRaster(frameId, chunks.width, chunks.height, complete);
    }
  }
}

byte[] rleDecode(byte[] encoded, int decodedBytes) {
  if (decodedBytes < 0 || (encoded.length % 2) != 0) {
    return null;
  }
  byte[] out = new byte[decodedBytes];
  int pos = 0;
  for (int i = 0; i < encoded.length; i += 2) {
    int count = encoded[i] & 0xff;
    byte value = encoded[i + 1];
    if (count == 0 || pos + count > out.length) {
      return null;
    }
    for (int j = 0; j < count; j++) {
      out[pos++] = value;
    }
  }
  return pos == out.length ? out : null;
}

void acceptPressureRaster(int frameId, int w, int h, int bitDepth, float maxValue, byte[] blob) {
  if (w <= 0 || h <= 0) {
    return;
  }
  int cells = w * h;
  if (bitDepth == 8 && blob.length < cells) {
    return;
  }
  if (bitDepth == 16 && blob.length < cells * 2) {
    return;
  }

  PImage img = createImage(w, h, RGB);
  img.loadPixels();
  for (int i = 0; i < cells; i++) {
    int gray;
    if (bitDepth == 16) {
      int lo = blob[i * 2] & 0xff;
      int hi = blob[i * 2 + 1] & 0xff;
      int value = lo | (hi << 8);
      gray = pressureGray(value, maxValue);
    } else {
      int value = blob[i] & 0xff;
      gray = pressureGray(value, maxValue);
    }
    img.pixels[i] = 0xff000000 | (gray << 16) | (gray << 8) | gray;
  }
  img.updatePixels();

  latestPressureBytes = Arrays.copyOf(blob, cells * (bitDepth == 16 ? 2 : 1));
  pressureImage = img;
  latestPressureFrameId = frameId;
  latestFrameId = frameId;
  latestPressureWidth = w;
  latestPressureHeight = h;
  latestPressureBitDepth = bitDepth;
  latestPressureMax = maxValue;
  lastPressureMillis = millis();
}

int pressureByteCount() {
  if (latestPressureBitDepth == 16) {
    return latestPressureWidth * latestPressureHeight * 2;
  }
  if (latestPressureBitDepth == 8) {
    return latestPressureWidth * latestPressureHeight;
  }
  return 0;
}

int pressureGray(int value, float maxValue) {
  if (normalizePressureDisplay) {
    float maxForScale = max(1, maxValue);
    return floor(pow(constrain(value / maxForScale, 0, 1), 0.55) * 255.0 + 0.5);
  }
  return constrain(value, 0, 255);
}

void acceptLabelRaster(int frameId, int w, int h, byte[] blob) {
  if (w <= 0 || h <= 0 || blob.length < w * h) {
    return;
  }
  PImage img = createImage(w, h, RGB);
  img.loadPixels();
  for (int i = 0; i < w * h; i++) {
    img.pixels[i] = labelColor(blob[i] & 0xff);
  }
  img.updatePixels();
  latestLabelValues = Arrays.copyOf(blob, w * h);
  labelsImage = img;
  latestLabelsFrameId = frameId;
  latestFrameId = frameId;
  latestLabelsWidth = w;
  latestLabelsHeight = h;
  lastLabelsMillis = millis();
}

void clearStaleChannels() {
  int now = millis();
  if (lastPressureMillis > 0 && now - lastPressureMillis > CHANNEL_TIMEOUT_MS) {
    clearPressureChannel();
  }
  if (lastLabelsMillis > 0 && now - lastLabelsMillis > CHANNEL_TIMEOUT_MS) {
    clearLabelChannel();
  }
  if (lastContactsMillis > 0 && now - lastContactsMillis > CHANNEL_TIMEOUT_MS) {
    clearContactChannel();
  }
}

void clearPressureChannel() {
  pressureImage = null;
  latestPressureBytes = new byte[0];
  latestPressureFrameId = -1;
  latestPressureWidth = 0;
  latestPressureHeight = 0;
  latestPressureBitDepth = 0;
  latestPressureMax = 0;
  pressureChunks.clear();
  lastPressureMillis = 0;
}

void clearLabelChannel() {
  labelsImage = null;
  latestLabelValues = new byte[0];
  latestLabelsFrameId = -1;
  latestLabelsWidth = 0;
  latestLabelsHeight = 0;
  labelsChunks.clear();
  lastLabelsMillis = 0;
}

void clearContactChannel() {
  contactFrame = new DecodedContactsFrame(-1, new SenselContact[0]);
  contactSummaryFrame = new ContactSummaryFrame();
  latestContactsFrameId = -1;
  contactsByFrame.clear();
  expectedContactsByFrame.clear();
  lastContactsMillis = 0;
}

void pruneOldContactFrames(int newestFrameId) {
  if (contactsByFrame.size() <= 32) {
    return;
  }
  ArrayList<Integer> remove = new ArrayList<Integer>();
  for (Integer key : contactsByFrame.keySet()) {
    int delta = (newestFrameId - key.intValue() + 256) % 256;
    if (delta > 32) {
      remove.add(key);
    }
  }
  for (Integer key : remove) {
    contactsByFrame.remove(key);
    expectedContactsByFrame.remove(key);
  }
}

void acceptContactMessage(OscMessage msg) {
  if (msg.args.size() < 10) {
    return;
  }
  int frameId = asInt(msg.args.get(0));
  SenselContact c = new SenselContact(
    asInt(msg.args.get(1)),
    asInt(msg.args.get(2)),
    asFloat(msg.args.get(3)),
    asFloat(msg.args.get(4)),
    asFloat(msg.args.get(5)),
    asFloat(msg.args.get(6))
  );
  c.orientation = asFloat(msg.args.get(7));
  c.majorAxis = asFloat(msg.args.get(8));
  c.minorAxis = asFloat(msg.args.get(9));
  c.axesAreMm = true;
  if (msg.args.size() >= 21) {
    c.deltaX = asFloat(msg.args.get(10));
    c.deltaY = asFloat(msg.args.get(11));
    c.deltaForce = asFloat(msg.args.get(12));
    c.deltaArea = asFloat(msg.args.get(13));
    c.minX = asFloat(msg.args.get(14));
    c.minY = asFloat(msg.args.get(15));
    c.maxX = asFloat(msg.args.get(16));
    c.maxY = asFloat(msg.args.get(17));
    c.peakX = asFloat(msg.args.get(18));
    c.peakY = asFloat(msg.args.get(19));
    c.peakForce = asFloat(msg.args.get(20));
    c.hasDelta = abs(c.deltaX) > 0.0001 || abs(c.deltaY) > 0.0001;
    c.hasBounds = c.maxX > c.minX && c.maxY > c.minY;
    normalizePeakCoordinates(c);
    c.hasPeak = hasDrawablePeak(c);
  }

  ArrayList<SenselContact> contacts = contactsByFrame.get(frameId);
  if (contacts == null) {
    contacts = new ArrayList<SenselContact>();
    contactsByFrame.put(frameId, contacts);
  }
  contacts.add(c);
  publishContactFrameIfComplete(frameId, false);
}

void acceptContactSummaryMessage(OscMessage msg) {
  if (msg.args.size() < 11) {
    return;
  }
  contactSummaryFrame.valid = true;
  contactSummaryFrame.frameId = asInt(msg.args.get(0));
  contactSummaryFrame.count = asInt(msg.args.get(1));
  contactSummaryFrame.xAvg = asFloat(msg.args.get(2));
  contactSummaryFrame.yAvg = asFloat(msg.args.get(3));
  contactSummaryFrame.xForceAvg = asFloat(msg.args.get(4));
  contactSummaryFrame.yForceAvg = asFloat(msg.args.get(5));
  contactSummaryFrame.forceTotal = asFloat(msg.args.get(6));
  contactSummaryFrame.forceAvg = asFloat(msg.args.get(7));
  contactSummaryFrame.areaAvg = asFloat(msg.args.get(8));
  contactSummaryFrame.spread = asFloat(msg.args.get(9));
  contactSummaryFrame.avgWeightedDistance = asFloat(msg.args.get(10));
}

void acceptAccelerometerMessage(OscMessage msg) {
  if (msg.args.size() < 7) {
    return;
  }
  accelFrame.valid = true;
  accelFrame.frameId = asInt(msg.args.get(0));
  accelFrame.x = asInt(msg.args.get(1));
  accelFrame.y = asInt(msg.args.get(2));
  accelFrame.z = asInt(msg.args.get(3));
  accelFrame.xG = asFloat(msg.args.get(4));
  accelFrame.yG = asFloat(msg.args.get(5));
  accelFrame.zG = asFloat(msg.args.get(6));
  appendAccelHistory(accelFrame);
}

void appendAccelHistory(AccelFrame frame) {
  AccelFrame copy = new AccelFrame();
  copy.valid = frame.valid;
  copy.frameId = frame.frameId;
  copy.x = frame.x;
  copy.y = frame.y;
  copy.z = frame.z;
  copy.xG = frame.xG;
  copy.yG = frame.yG;
  copy.zG = frame.zG;
  accelHistory[accelHistoryWrite] = copy;
  accelHistoryWrite = (accelHistoryWrite + 1) % accelHistory.length;
  accelHistoryCount = min(accelHistoryCount + 1, accelHistory.length);
}

int labelColor(int label) {
  if (label == 255) {
    return 0xff000000;
  }
  return labelPalette[label % labelPalette.length];
}

int asInt(Object value) {
  if (value instanceof Integer) {
    return ((Integer) value).intValue();
  }
  if (value instanceof Float) {
    return round(((Float) value).floatValue());
  }
  return int(value.toString());
}

float asFloat(Object value) {
  if (value instanceof Float) {
    return ((Float) value).floatValue();
  }
  if (value instanceof Integer) {
    return ((Integer) value).floatValue();
  }
  return float(value.toString());
}

String asString(Object value) {
  return value == null ? "" : value.toString();
}

byte[] asBytes(Object value) {
  if (value instanceof byte[]) {
    return (byte[]) value;
  }
  return new byte[0];
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
  if (c.peakForce <= 0 || c.peakX < 0 || c.peakY < 0) {
    return false;
  }

  float scaledX = c.peakX * 256.0;
  float scaledY = c.peakY * 256.0;
  if (scaledX < 0 || scaledX > ACTIVE_W_MM || scaledY < 0 || scaledY > ACTIVE_H_MM) {
    return false;
  }

  if (c.hasBounds) {
    float margin = 2.5;
    boolean scaledInBounds = pointInBox(scaledX, scaledY, c.minX - margin, c.minY - margin, c.maxX + margin, c.maxY + margin);
    boolean rawInBounds = pointInBox(c.peakX, c.peakY, c.minX - margin, c.minY - margin, c.maxX + margin, c.maxY + margin);
    return scaledInBounds && !rawInBounds;
  }

  float rawDistance = dist(c.peakX, c.peakY, c.x, c.y);
  float scaledDistance = dist(scaledX, scaledY, c.x, c.y);
  return rawDistance > 25.0 && scaledDistance < 25.0;
}

boolean pointInBox(float x, float y, float left, float top, float right, float bottom) {
  return x >= left && x <= right && y >= top && y <= bottom;
}

class DecodedContactsFrame {
  int frameId;
  SenselContact[] contacts;

  DecodedContactsFrame(int frameId, SenselContact[] contacts) {
    this.frameId = frameId;
    this.contacts = contacts;
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

class SenselContactInfo {
  int id;
  int state;
  float x;
  float y;
  PVector position;
  PVector xy;
  PVector screenPosition;
  PVector screenXY;
  float force;
  float area;
  float orientation;
  float majorAxis;
  float minorAxis;
  boolean axesAreMm;
  float axisScreenWidth;
  float axisScreenHeight;
  PVector delta;
  PVector deltaXY;
  PVector dxdy;
  PVector deltaScreen;
  float deltaForce;
  float deltaArea;
  PVector peak;
  PVector peakXY;
  PVector peakScreen;
  float peakForce;
  SenselBoundingBox bbox;
  SenselBoundingBox boundingBox;
  SenselBoundingBox firmwareBBox;
  boolean hasDelta;
  boolean hasBounds;
  boolean hasPeak;

  SenselContactInfo(SenselContact c, SenselBoundingBox displayBBox, SenselBoundingBox firmwareBBox, PGraphics pg) {
    this.id = c.id;
    this.state = c.state;
    this.x = c.x;
    this.y = c.y;
    this.position = new PVector(c.x, c.y);
    this.xy = this.position;
    this.screenPosition = contactScreenVector(c.x, c.y, pg);
    this.screenXY = this.screenPosition;
    this.force = c.force;
    this.area = c.area;
    this.orientation = c.orientation;
    this.majorAxis = c.majorAxis;
    this.minorAxis = c.minorAxis;
    this.axesAreMm = c.axesAreMm;
    if (pg == null) {
      this.axisScreenWidth = 0;
      this.axisScreenHeight = 0;
    } else {
      float viewScale = (float) pg.width / (float) SENSOR_W;
      this.axisScreenWidth = c.axesAreMm ? c.minorAxis / ACTIVE_W_MM * pg.width : c.minorAxis * viewScale;
      this.axisScreenHeight = c.axesAreMm ? c.majorAxis / ACTIVE_H_MM * pg.height : c.majorAxis * viewScale;
    }
    this.delta = new PVector(c.deltaX, c.deltaY);
    this.deltaXY = this.delta;
    this.dxdy = this.delta;
    this.deltaScreen = contactDeltaScreenVector(c.deltaX, c.deltaY, pg);
    this.deltaForce = c.deltaForce;
    this.deltaArea = c.deltaArea;
    this.peak = new PVector(c.peakX, c.peakY);
    this.peakXY = this.peak;
    this.peakScreen = contactScreenVector(c.peakX, c.peakY, pg);
    this.peakForce = c.peakForce;
    this.bbox = displayBBox;
    this.boundingBox = displayBBox;
    this.firmwareBBox = firmwareBBox;
    this.hasDelta = c.hasDelta;
    this.hasBounds = displayBBox != null;
    this.hasPeak = c.hasPeak;
  }
}

class SenselBoundingBox {
  int id;
  float left;
  float top;
  float right;
  float bottom;
  float x;
  float y;
  float w;
  float h;
  float width;
  float height;
  PVector min;
  PVector max;
  PVector center;
  String source;
  boolean fromLabelRaster;

  SenselBoundingBox(int id, float left, float top, float right, float bottom, String source, boolean fromLabelRaster) {
    this.id = id;
    this.left = left;
    this.top = top;
    this.right = right;
    this.bottom = bottom;
    this.x = left;
    this.y = top;
    this.w = right - left;
    this.h = bottom - top;
    this.width = this.w;
    this.height = this.h;
    this.min = new PVector(left, top);
    this.max = new PVector(right, bottom);
    this.center = new PVector((left + right) * 0.5, (top + bottom) * 0.5);
    this.source = source;
    this.fromLabelRaster = fromLabelRaster;
  }

  boolean contains(float px, float py) {
    return px >= left && px <= right && py >= top && py <= bottom;
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
  boolean axesAreMm;
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
    this.axesAreMm = false;
    this.hasDelta = false;
    this.hasBounds = false;
    this.hasPeak = false;
  }
}

class RasterChunks {
  int frameId;
  int width;
  int height;
  int bitDepth;
  float maxValue;
  int totalBytes;
  int chunkCount;
  boolean rle;
  int decodedBytes;
  byte[][] chunks;
  int received = 0;

  RasterChunks(int frameId, int width, int height, int bitDepth, float maxValue, int totalBytes, int chunkCount) {
    this(frameId, width, height, bitDepth, maxValue, totalBytes, chunkCount, false, totalBytes);
  }

  RasterChunks(int frameId, int width, int height, int bitDepth, float maxValue, int totalBytes, int chunkCount, boolean rle, int decodedBytes) {
    this.frameId = frameId;
    this.width = width;
    this.height = height;
    this.bitDepth = bitDepth;
    this.maxValue = maxValue;
    this.totalBytes = totalBytes;
    this.chunkCount = max(1, chunkCount);
    this.rle = rle;
    this.decodedBytes = decodedBytes;
    this.chunks = new byte[this.chunkCount][];
  }

  boolean accept(int chunkIndex, byte[] data) {
    if (chunkIndex < 0 || chunkIndex >= chunkCount) {
      return false;
    }
    if (chunks[chunkIndex] == null) {
      received++;
    }
    chunks[chunkIndex] = data;
    return received == chunkCount;
  }

  byte[] join() {
    byte[] out = new byte[totalBytes];
    int pos = 0;
    for (int i = 0; i < chunkCount; i++) {
      byte[] chunk = chunks[i];
      if (chunk == null) {
        continue;
      }
      int n = min(chunk.length, out.length - pos);
      arrayCopy(chunk, 0, out, pos, n);
      pos += n;
      if (pos >= out.length) {
        break;
      }
    }
    return out;
  }
}

class OscMessage {
  String address;
  ArrayList<Object> args;

  OscMessage(String address, ArrayList<Object> args) {
    this.address = address;
    this.args = args;
  }
}

void parseOscPacket(byte[] data, int length, ConcurrentLinkedQueue<OscMessage> queue) {
  parseOscElement(data, 0, length, queue);
}

void parseOscElement(byte[] data, int start, int end, ConcurrentLinkedQueue<OscMessage> queue) {
  int[] offset = { start };
  String address = readOscString(data, offset, end);
  if (address == null || address.length() == 0) {
    return;
  }

  if (address.equals("#bundle")) {
    offset[0] += 8; // OSC timetag.
    while (offset[0] + 4 <= end) {
      int elementSize = readI32BE(data, offset[0]);
      offset[0] += 4;
      if (elementSize <= 0 || offset[0] + elementSize > end) {
        return;
      }
      parseOscElement(data, offset[0], offset[0] + elementSize, queue);
      offset[0] += elementSize;
    }
    return;
  }

  String tags = readOscString(data, offset, end);
  ArrayList<Object> args = new ArrayList<Object>();
  if (tags == null || tags.length() == 0) {
    queue.add(new OscMessage(address, args));
    return;
  }

  for (int i = 1; i < tags.length(); i++) {
    char tag = tags.charAt(i);
    if (tag == 'i') {
      if (offset[0] + 4 > end) {
        return;
      }
      args.add(Integer.valueOf(readI32BE(data, offset[0])));
      offset[0] += 4;
    } else if (tag == 'f') {
      if (offset[0] + 4 > end) {
        return;
      }
      args.add(Float.valueOf(Float.intBitsToFloat(readI32BE(data, offset[0]))));
      offset[0] += 4;
    } else if (tag == 's') {
      String value = readOscString(data, offset, end);
      if (value == null) {
        return;
      }
      args.add(value);
    } else if (tag == 'b') {
      if (offset[0] + 4 > end) {
        return;
      }
      int blobLen = readI32BE(data, offset[0]);
      offset[0] += 4;
      if (blobLen < 0 || offset[0] + blobLen > end) {
        return;
      }
      args.add(Arrays.copyOfRange(data, offset[0], offset[0] + blobLen));
      offset[0] = align4(offset[0] + blobLen);
    } else if (tag == 'T') {
      args.add(Integer.valueOf(1));
    } else if (tag == 'F') {
      args.add(Integer.valueOf(0));
    }
  }
  queue.add(new OscMessage(address, args));
}

String readOscString(byte[] data, int[] offset, int end) {
  int start = offset[0];
  int pos = start;
  while (pos < end && data[pos] != 0) {
    pos++;
  }
  if (pos >= end) {
    return null;
  }
  String value = new String(data, start, pos - start);
  offset[0] = align4(pos + 1);
  return value;
}

int align4(int value) {
  return (value + 3) & ~3;
}

int readI32BE(byte[] data, int offset) {
  return ((data[offset] & 0xff) << 24)
    | ((data[offset + 1] & 0xff) << 16)
    | ((data[offset + 2] & 0xff) << 8)
    | (data[offset + 3] & 0xff);
}
