// Sensel Morph Syphon + OSC Transmitter
// By Golan Levin, July 2026.
// ALERT: This is for Processing 4.3.
//
// Connects directly to a Sensel Morph over USB CDC serial; decodes live
// pressure, label, contact, and accelerometer frames; displays the active
// streams; broadcasts them as OSC; and publishes pressure, label, and contact
// graphics as Syphon sources. This app does not require Python, oscP5, or the
// Sensel SDK.
//
// Settings are loaded from data/settings.txt. Press h to show/hide the HUD and
// right-side settings UI. Press keys 1-7 to view different data layers. 
// The UI can change streams, resolution, pressure type, RLE compression, 
// calibration, compatibility modes, and display sampling while the sketch
// is running. Pressure display defaults to bicubic shader interpolation for
// Syphon output. Press c to toggle calibration when a matching
// data/calibration_<serial_number>.json file is available.


import codeanticode.syphon.*;
import processing.serial.*;
import processing.opengl.PGraphicsOpenGL;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;

final int SENSOR_W = 185;
final int SENSOR_H = 105;
final int CANVAS_W = 1280;
final int CANVAS_H = 720;
final float ACTIVE_W_MM = 230.0;
final float ACTIVE_H_MM = 130.0;
final int TEXTURE_SAMPLING_NEAREST = POINT;
final int SURFACE_SAMPLING_NEAREST = 0;
final int SURFACE_SAMPLING_BICUBIC = 2;
final int PRESSURE_SAMPLING_WARMUP_FRAMES = 60;

MorphSettings config;
MorphDevice device;
OscSender osc;
PressureCalibration calibration;
RecordingFrameSource recordingSource;
RawPacketRecorder packetRecorder = new RawPacketRecorder();
Thread captureThread;
volatile boolean running = false;
volatile boolean playbackPaused = false;
volatile int playbackStepRequest = 0;
int lastPausedPlaybackTransmitMillis = 0;
volatile boolean deviceRestored = false;
volatile boolean reconfigureRequested = false;
volatile boolean restartRequested = false;
Object frameLock = new Object();
HashMap<String, String> initialRegs = new HashMap<String, String>();
byte[] currentCompressionMetadata = new byte[0];
ArrayList<Grid> currentPlaybackGrids = new ArrayList<Grid>();
String currentSourceName = "device";
LiveFrame latestFrame = null;
int renderedFrameSerial = -1;
int frameSerialCounter = 0;
String statusLine = "starting";
String currentPortName = "";
String deviceSerial = "unknown";
float txFps = 0;
int lastTxMillis = 0;
int lastStatusMillis = 0;
int transmittedFrames = 0;
boolean showHelp = true;
int surfaceSamplingModeIndex = SURFACE_SAMPLING_BICUBIC;
int pressureSamplingWarmupFramesRemaining = PRESSURE_SAMPLING_WARMUP_FRAMES;
int displayMode = 7;
String uiStatus = "";
int uiStatusMillis = 0;
int lastDeviceAutoStartMillis = 0;
PShader bicubicPressureShader;
SyphonServer pressureSyphon;
SyphonServer labelsSyphon;
SyphonServer contactsSyphon;
boolean syphonAvailable = false;
String syphonStatus = "Syphon not initialized";

PGraphics pressureBuffer;
PGraphics labelsBuffer;
PGraphics contactsBuffer;
PGraphics blankSyphonBuffer;
PImage pressureImage;
PImage labelsImage;
DecodedContactsFrame contactFrame = new DecodedContactsFrame(-1, new SenselContact[0]);
ContactSummaryFrame contactSummaryFrame = new ContactSummaryFrame();
AccelFrame accelFrame = new AccelFrame();
HashMap<Integer, RasterPeak> displayRasterPeaks = new HashMap<Integer, RasterPeak>();
HashMap<Integer, RasterEllipse> displayRasterEllipses = new HashMap<Integer, RasterEllipse>();
byte[] latestLabelValues = new byte[0];
int latestLabelsFrameId = -1;
int latestLabelsWidth = 0;
int latestLabelsHeight = 0;
int latestPressureWidth = 0;
int latestPressureHeight = 0;

int[] labelPalette = {
  0xffff0000, 0xff00ff00, 0xff0060ff, 0xffffff00,
  0xff00ffff, 0xffff00ff, 0xffff8000, 0xff80ff00,
  0xff0080ff, 0xffff0080, 0xff80ffff, 0xffff80ff,
  0xffc0c0c0, 0xffffffff, 0xff808080, 0xff800000
};


void setup() {
  size(1280, 720, P2D);
 
  surface.setTitle("Sensel Morph Syphon + OSC Transmitter");
  noSmooth();
  frameRate(60);
  config = loadMorphSettings();
  createSurfaceBuffers();
  setupSyphon();
  startCapture();
}

void draw() {
  autoStartDeviceCaptureIfAvailable();

  LiveFrame frame = getLatestFrame();
  if (frame != null && frame.frameSerial != renderedFrameSerial) {
    acceptLiveFrameForDisplay(frame);
    renderedFrameSerial = frame.frameSerial;
  }

  publishSyphonBuffers();
  background(30);
  drawCurrentLayers();
  drawPlaybackProgressBar();
  if (showHelp) {
    drawHud();
    drawSettingsUi();
  }
}

void setupSyphon() {
  try {
    bicubicPressureShader = loadShader("bicubic_pressure.glsl");
    pressureSyphon = new SyphonServer(this, "Sensel Morph Pressure");
    labelsSyphon = new SyphonServer(this, "Sensel Morph Labels");
    contactsSyphon = new SyphonServer(this, "Sensel Morph Contacts");
    syphonAvailable = true;
    syphonStatus = "Syphon: on";
  } catch (Throwable t) {
    syphonAvailable = false;
    syphonStatus = "Syphon unavailable: " + t.getClass().getSimpleName();
    println(syphonStatus);
    println(t.getMessage());
  }
}

void publishSyphonBuffers() {
  if (!syphonAvailable) {
    return;
  }
  noTint();
  blendMode(BLEND);
  if (pressureSyphon != null) pressureSyphon.sendImage(config.syphonPressure ? pressureBuffer : blankSyphonBuffer);
  if (labelsSyphon != null) labelsSyphon.sendImage(config.syphonLabels ? labelsBuffer : blankSyphonBuffer);
  if (contactsSyphon != null) contactsSyphon.sendImage(config.syphonContacts ? contactsBuffer : blankSyphonBuffer);
}

void startCapture() {
  if (captureThread != null && captureThread.isAlive() && Thread.currentThread() != captureThread) {
    return;
  }
  running = true;
  deviceRestored = false;
  captureThread = new Thread(new Runnable() {
    public void run() {
      captureLoop();
    }
  });
  captureThread.setDaemon(true);
  captureThread.start();
}

void autoStartDeviceCaptureIfAvailable() {
  if (config == null || config.source.equals("recording")) {
    return;
  }
  if (captureThread != null && captureThread.isAlive()) {
    return;
  }
  int now = millis();
  if (now - lastDeviceAutoStartMillis < 1000) {
    return;
  }
  lastDeviceAutoStartMillis = now;
  if (liveDeviceAvailable()) {
    statusLine = "device detected; starting capture";
    startCapture();
  }
}

void captureLoop() {
  if (config.source.equals("recording")) {
    recordingLoop();
  } else {
    deviceLoop();
  }
}

void deviceLoop() {
  HashMap<String, String> initial = new HashMap<String, String>();
  try {
    String portName = config.device.length() > 0 ? config.device : findMorphSerialPort();
    currentPortName = portName;
    deviceSerial = serialFromPortName(portName, config.serialNumber);
    calibration = loadMatchingCalibration(deviceSerial, config.useCalibration);
    statusLine = "opening " + portName;
    device = new MorphDevice(thisApplet(), portName, config.readTimeoutMs);
    osc = new OscSender(config.host, config.port, config.chunkSize);

    initial.put("scan_detail", hexByte(device.readReg(MorphDevice.REG_SCAN_DETAIL, 1)[0]));
    initial.put("frame_content", hexByte(device.readReg(MorphDevice.REG_FRAME_CONTENT, 1)[0]));
    initial.put("scan_enabled", hexByte(device.readReg(MorphDevice.REG_SCAN_ENABLED, 1)[0]));
    initial.put("contacts_mask", hexByte(device.readReg(MorphDevice.REG_CONTACTS_MASK, 1)[0]));
    synchronized (frameLock) {
      initialRegs = initial;
    }

    device.writeReg(MorphDevice.REG_SCAN_ENABLED, 0);
    device.writeReg(MorphDevice.REG_SCAN_DETAIL, scanDetailForPressureRes(config.pressureRes));
    device.writeReg(MorphDevice.REG_FRAME_CONTENT, frameContentMask(config));
    if (needsContactOutput(config)) {
      device.writeReg(MorphDevice.REG_CONTACTS_MASK, 0x0f);
    }
    byte[] metadata = readCompressionMetadataWithRetry();
    currentCompressionMetadata = metadata;
    currentSourceName = "device";
    ArrayList<Grid> grids = gridsFromMetadata(metadata);

    sendStatus(portName);
    device.writeReg(MorphDevice.REG_SCAN_ENABLED, 1);
    statusLine = "transmitting to " + config.host + ":" + config.port;
    if (config.recordEnabled) {
      startPacketRecording();
    }

    while (running) {
      if (restartRequested) {
        running = false;
        break;
      }
      if (reconfigureRequested) {
        grids = reconfigureDevice(portName, grids);
      }
      FramePacket packet;
      try {
        packet = device.readFrame();
      } catch (Exception e) {
        device.clearInput();
        statusLine = "frame read resync: " + e.getMessage();
        delay(5);
        continue;
      }
      if (!packet.checksumOk) {
        device.clearInput();
        statusLine = "frame checksum resync";
        continue;
      }
      packetRecorder.recordPacket(packet);
      LiveFrame frame;
      try {
        frame = decodeLiveFrame(packet, grids, config);
        prepareOutputRasters(frame, config);
      } catch (Exception e) {
        device.clearInput();
        statusLine = "decode resync: " + e.getMessage();
        delay(5);
        continue;
      }
      sendLiveFrame(frame);
      publishLatestFrame(frame);
      noteTransmittedFrame();
      if (config.fpsLimit > 0) {
        delay(max(1, round(1000.0 / config.fpsLimit)));
      }
    }
  } catch (Exception e) {
    statusLine = "capture stopped: " + e.getMessage();
    println(statusLine);
  } finally {
    if (packetRecorder.isActive()) {
      packetRecorder.stop();
    }
    restoreDevice(initial);
    closeOscSender();
    if (restartRequested) {
      restartRequested = false;
      startCapture();
    }
  }
}

void recordingLoop() {
  try {
    device = null;
    String playbackFile = selectedRecordingFile();
    if (playbackFile.length() == 0) {
      throw new RuntimeException("no recordings found");
    }
    config.recordingFile = playbackFile;
    currentPortName = config.recordingFile;
    osc = new OscSender(config.host, config.port, config.chunkSize);
    recordingSource = new RecordingFrameSource(
      playbackFile,
      config.recordingLoop,
      config.recordingTiming,
      config.playbackPolicy,
      config.recordingFps
    );
    recordingSource.openSource();
    if (recordingSource.recordedPressureRes.length() > 0) {
      config.pressureRes = recordingSource.recordedPressureRes;
      config.labelRes = config.pressureRes;
    }
    deviceSerial = config.serialNumber.length() > 0 ? config.serialNumber : recordingSource.serialNumber;
    calibration = loadMatchingCalibration(deviceSerial, config.useCalibration);
    currentSourceName = recordingSource.sourceName();
    currentCompressionMetadata = recordingSource.compressionMetadata();
    ArrayList<Grid> grids = gridsFromMetadata(currentCompressionMetadata);
    currentPlaybackGrids = grids;
    playbackPaused = false;
    playbackStepRequest = 0;
    lastPausedPlaybackTransmitMillis = 0;

    sendStatus(recordingSource.sourceName());
    statusLine = "playing " + recordingSource.sourceName() + " to " + config.host + ":" + config.port;

    while (running) {
      if (restartRequested) {
        running = false;
        break;
      }
      int step = consumePlaybackStepRequest();
      if (playbackPaused && step == 0) {
        transmitPausedPlaybackFrameIfDue(grids);
        delay(5);
        continue;
      }
      FramePacket packet = step != 0 ? recordingSource.stepFrame(step) : recordingSource.nextFrame();
      if (!packet.checksumOk) {
        statusLine = "recording checksum skip";
        continue;
      }
      try {
        processPlaybackPacket(packet, grids, true);
      } catch (Exception e) {
        statusLine = "recording decode skip: " + e.getMessage();
        delay(5);
        continue;
      }
      noteTransmittedFrame();
      if (step != 0) {
        lastPausedPlaybackTransmitMillis = millis();
      }
      if (config.fpsLimit > 0) {
        delay(max(1, round(1000.0 / config.fpsLimit)));
      }
    }
  } catch (Exception e) {
    statusLine = "playback stopped: " + e.getMessage();
    println(statusLine);
  } finally {
    if (recordingSource != null) {
      recordingSource.closeSource();
      recordingSource = null;
    }
    currentPlaybackGrids = new ArrayList<Grid>();
    closeOscSender();
    if (restartRequested) {
      restartRequested = false;
      startCapture();
    }
  }
}

void processPlaybackPacket(FramePacket packet, ArrayList<Grid> grids, boolean transmit) {
  LiveFrame frame = decodeLiveFrame(packet, grids, config);
  prepareOutputRasters(frame, config);
  if (transmit) {
    sendLiveFrame(frame);
  }
  publishLatestFrame(frame);
}

void transmitPausedPlaybackFrameIfDue(ArrayList<Grid> grids) {
  if (recordingSource == null) {
    return;
  }
  int now = millis();
  int interval = pausedPlaybackTransmitIntervalMs();
  if (lastPausedPlaybackTransmitMillis > 0 && now - lastPausedPlaybackTransmitMillis < interval) {
    return;
  }
  try {
    processPlaybackPacket(recordingSource.currentFrame(), grids, true);
    noteTransmittedFrame();
    lastPausedPlaybackTransmitMillis = now;
  } catch (Exception e) {
    statusLine = "paused playback send failed: " + e.getMessage();
  }
}

int pausedPlaybackTransmitIntervalMs() {
  float fps = config.fpsLimit > 0 ? config.fpsLimit : config.recordingFps;
  return max(1, round(1000.0 / max(1.0, fps)));
}

void refreshPausedPlaybackFrame() {
  if (!config.source.equals("recording") || !playbackPaused || recordingSource == null) {
    return;
  }
  try {
    processPlaybackPacket(recordingSource.currentFrame(), currentPlaybackGrids, true);
    noteTransmittedFrame();
    lastPausedPlaybackTransmitMillis = millis();
    uiStatus = "display refreshed";
    uiStatusMillis = millis();
  } catch (Exception e) {
    uiStatus = "refresh failed";
    uiStatusMillis = millis();
    println("paused playback refresh failed: " + e.getMessage());
  }
}

void requestReconfigure() {
  if (config.source.equals("recording")) {
    requestRestart();
    return;
  }
  reconfigureRequested = true;
}

void requestRestart() {
  restartRequested = true;
  running = false;
  if (captureThread == null || !captureThread.isAlive()) {
    restartRequested = false;
    startCapture();
  }
}

ArrayList<Grid> reconfigureDevice(String portName, ArrayList<Grid> fallbackGrids) {
  reconfigureRequested = false;
  try {
    if (packetRecorder.isActive()) {
      packetRecorder.stop();
      uiStatus = "recording stopped for reconfigure";
      uiStatusMillis = millis();
    }
    statusLine = "reconfiguring device";
    device.writeReg(MorphDevice.REG_SCAN_ENABLED, 0);
    device.writeReg(MorphDevice.REG_SCAN_DETAIL, scanDetailForPressureRes(config.pressureRes));
    device.writeReg(MorphDevice.REG_FRAME_CONTENT, frameContentMask(config));
    if (needsContactOutput(config)) {
      device.writeReg(MorphDevice.REG_CONTACTS_MASK, 0x0f);
    }
    device.clearInput();
    currentCompressionMetadata = readCompressionMetadataWithRetry();
    ArrayList<Grid> grids = gridsFromMetadata(currentCompressionMetadata);
    device.writeReg(MorphDevice.REG_SCAN_ENABLED, 1);
    drainStartupFrames(3);
    sendStatus(portName);
    statusLine = "transmitting to " + config.host + ":" + config.port;
    return grids;
  } catch (Exception e) {
    statusLine = "reconfigure failed: " + e.getMessage();
    println(statusLine);
    return fallbackGrids;
  }
}

void drainStartupFrames(int count) {
  for (int i = 0; i < count; i++) {
    try {
      FramePacket packet = device.readFrame();
      if (!packet.checksumOk) {
        device.clearInput();
      }
    } catch (Exception e) {
      device.clearInput();
      delay(5);
    }
  }
}

byte[] readCompressionMetadataWithRetry() {
  try {
    return device.readVS(MorphDevice.REG_COMPRESSION_METADATA);
  } catch (Exception first) {
    println("compression metadata read failed, retrying: " + first.getMessage());
    device.clearInput();
    delay(75);
    return device.readVS(MorphDevice.REG_COMPRESSION_METADATA);
  }
}

PApplet thisApplet() {
  return this;
}

void sendStatus(String portName) {
  osc.send("/sensel_morph/status", new Object[] {
    portName,
    Integer.valueOf(frameContentMask(config)),
    config.pressureRes,
    config.pressureType,
    Integer.valueOf(config.rle ? 1 : 0),
    deviceSerial,
    Integer.valueOf(calibrationActive() ? 1 : 0)
  });
  lastStatusMillis = millis();
}

void sendLiveFrame(LiveFrame frame) {
  osc.send("/sensel_morph/frame", new Object[] {
    Integer.valueOf(frame.frameId),
    Integer.valueOf(frame.timestamp),
    Integer.valueOf(frame.contentMask)
  });

  if (config.pressure && frame.pressureBlob != null) {
    sendRasterBlob("/sensel_morph/pressure", new Object[] {
      Integer.valueOf(frame.frameId),
      Integer.valueOf(frame.pressureWidth),
      Integer.valueOf(frame.pressureHeight),
      Integer.valueOf(frame.pressureBitDepth),
      Float.valueOf(frame.pressureMax)
    }, frame.pressureBlob);
  }

  if (config.labels && frame.labelBlob != null) {
    sendRasterBlob("/sensel_morph/labels", new Object[] {
      Integer.valueOf(frame.frameId),
      Integer.valueOf(frame.labelWidth),
      Integer.valueOf(frame.labelHeight)
    }, frame.labelBlob);
  }

  if (config.contacts && frame.contacts != null) {
    sendContacts(frame);
  }

  if (frame.accel != null) {
    osc.send("/sensel_morph/accelerometer", new Object[] {
      Integer.valueOf(frame.frameId),
      Integer.valueOf(frame.accel.x),
      Integer.valueOf(frame.accel.y),
      Integer.valueOf(frame.accel.z),
      Float.valueOf(frame.accel.x / config.accelCountsPerG),
      Float.valueOf(frame.accel.y / config.accelCountsPerG),
      Float.valueOf(frame.accel.z / config.accelCountsPerG)
    });
  }

  if (currentPortName.length() > 0 && millis() - lastStatusMillis >= 1000) {
    sendStatus(currentPortName);
  }

  osc.send("/sensel_morph/sync", new Object[] { Integer.valueOf(frame.frameId) });
}

void sendRasterBlob(String address, Object[] header, byte[] blob) {
  if (config.rle) {
    byte[] encoded = rleEncode(blob);
    Object[] rleHeader = Arrays.copyOf(header, header.length + 1);
    rleHeader[header.length] = Integer.valueOf(blob.length);
    osc.sendBlob(address + "_rle", rleHeader, encoded);
  } else {
    osc.sendBlob(address, header, blob);
  }
}

void publishLatestFrame(LiveFrame frame) {
  synchronized (frameLock) {
    frame.frameSerial = frameSerialCounter++;
    latestFrame = frame;
  }
}

LiveFrame getLatestFrame() {
  synchronized (frameLock) {
    return latestFrame;
  }
}

void noteTransmittedFrame() {
  transmittedFrames++;
  int now = millis();
  if (lastTxMillis > 0) {
    float instant = 1000.0 / max(1, now - lastTxMillis);
    txFps = txFps <= 0 ? instant : txFps * 0.85 + instant * 0.15;
  }
  lastTxMillis = now;
}

void restoreDevice(HashMap<String, String> initial) {
  if (deviceRestored) {
    return;
  }
  deviceRestored = true;
  if (device == null) {
    return;
  }
  try {
    device.writeReg(MorphDevice.REG_SCAN_ENABLED, 0);
  } catch (Exception e) {
    println("warning: failed to stop scan: " + e.getMessage());
  }
  restoreReg(initial, "scan_detail", MorphDevice.REG_SCAN_DETAIL);
  restoreReg(initial, "frame_content", MorphDevice.REG_FRAME_CONTENT);
  restoreReg(initial, "contacts_mask", MorphDevice.REG_CONTACTS_MASK);
  device.close();
  device = null;
}

void restoreReg(HashMap<String, String> initial, String key, int reg) {
  if (!initial.containsKey(key)) {
    return;
  }
  try {
    device.writeReg(reg, hexByteToInt(initial.get(key)));
  } catch (Exception e) {
    println("warning: failed to restore " + key + ": " + e.getMessage());
  }
}

void stop() {
  running = false;
  if (packetRecorder.isActive()) {
    packetRecorder.stop();
  }
  restoreDevice(initialRegs);
  closeOscSender();
  shutdownSyphon();
  super.stop();
}

void shutdownSyphon() {
  if (!syphonAvailable) {
    return;
  }
  try {
    if (blankSyphonBuffer != null) {
      blankSyphonBuffer.beginDraw();
      blankSyphonBuffer.clear();
      blankSyphonBuffer.endDraw();
    }
    if (pressureSyphon != null) pressureSyphon.sendImage(blankSyphonBuffer);
    if (labelsSyphon != null) labelsSyphon.sendImage(blankSyphonBuffer);
    if (contactsSyphon != null) contactsSyphon.sendImage(blankSyphonBuffer);
  } catch (Throwable t) {
  }

  stopSyphonServer(pressureSyphon, "pressure");
  stopSyphonServer(labelsSyphon, "labels");
  stopSyphonServer(contactsSyphon, "contacts");
  pressureSyphon = null;
  labelsSyphon = null;
  contactsSyphon = null;
  syphonAvailable = false;
  syphonStatus = "Syphon: stopped";
}

void stopSyphonServer(SyphonServer server, String name) {
  if (server == null) {
    return;
  }
  try {
    unregisterMethod("dispose", server);
  } catch (Throwable t) {
    println("warning: failed to unregister Syphon " + name + " dispose hook: " + t.getMessage());
  }
  try {
    server.stop();
  } catch (NullPointerException e) {
    // SyphonServer.stop() does not guard against an uninitialized internal server.
  } catch (Throwable t) {
    println("warning: failed to stop Syphon " + name + " server: " + t.getMessage());
  }
}

void closeOscSender() {
  if (osc != null) {
    try {
      osc.close();
    } catch (Exception e) {
    }
    osc = null;
  }
}

void keyPressed() {
  if (key >= '1' && key <= '7') {
    displayMode = key - '0';
  } else if (key == CODED && keyCode == LEFT) {
    requestPlaybackStep(-1);
  } else if (key == CODED && keyCode == RIGHT) {
    requestPlaybackStep(1);
  } else if (key == 'h' || key == 'H') {
    showHelp = !showHelp;
  } else if (key == 's' || key == 'S') {
    surfaceSamplingModeIndex = surfaceSamplingModeIndex == SURFACE_SAMPLING_NEAREST
      ? SURFACE_SAMPLING_BICUBIC
      : SURFACE_SAMPLING_NEAREST;
    pressureSamplingWarmupFramesRemaining = surfaceSamplingModeIndex == SURFACE_SAMPLING_BICUBIC ? PRESSURE_SAMPLING_WARMUP_FRAMES : 0;
  } else if (key == 'c' || key == 'C') {
    if (calibrationAvailable()) {
      setCalibrationEnabled(!calibration.enabled);
    }
  } else if (key == 'p' || key == 'P') {
    saveLayerScreenshots();
  } else if (key == ' ') {
    togglePlaybackPause();
  } 
}

void togglePlaybackPause() {
  if (!config.source.equals("recording") || recordingSource == null) {
    uiStatus = "space pauses playback";
    uiStatusMillis = millis();
    return;
  }
  playbackPaused = !playbackPaused;
  if (!playbackPaused) {
    recordingSource.resetClockToNextFrame();
  }
  lastPausedPlaybackTransmitMillis = 0;
  uiStatus = playbackPaused ? "playback paused" : "playback resumed";
  uiStatusMillis = millis();
}

void requestPlaybackStep(int delta) {
  if (!config.source.equals("recording") || !playbackPaused || recordingSource == null) {
    return;
  }
  synchronized (this) {
    playbackStepRequest += delta;
  }
}

int consumePlaybackStepRequest() {
  synchronized (this) {
    if (playbackStepRequest > 0) {
      playbackStepRequest--;
      return 1;
    }
    if (playbackStepRequest < 0) {
      playbackStepRequest++;
      return -1;
    }
    return 0;
  }
}

void togglePacketRecording() {
  if (!config.source.equals("device")) {
    uiStatus = "recording only in device mode";
    uiStatusMillis = millis();
    return;
  }
  if (packetRecorder.isActive()) {
    String out = packetRecorder.stop();
    config.recordEnabled = false;
    uiStatus = out.length() > 0 ? "saved " + new File(out).getName() : "recording stopped";
    uiStatusMillis = millis();
  } else if (startPacketRecording()) {
    config.recordEnabled = true;
    uiStatus = "recording";
    uiStatusMillis = millis();
  } else {
    config.recordEnabled = false;
    uiStatus = "record failed";
    uiStatusMillis = millis();
  }
}

boolean startPacketRecording() {
  return packetRecorder.start(
    "osc_transmitter",
    currentSourceName,
    currentCompressionMetadata,
    initialRegs
  );
}

void toggleFrameSource() {
  if (config.source.equals("device")) {
    setRunMode("playback");
  } else {
    setRunMode("live");
  }
}

void setRunMode(String mode) {
  if (mode.equals("live")) {
    if (packetRecorder.isActive()) {
      packetRecorder.stop();
    }
    config.source = "device";
    config.recordEnabled = false;
    playbackPaused = false;
    playbackStepRequest = 0;
    lastPausedPlaybackTransmitMillis = 0;
    requestRestart();
    uiStatus = "live device";
  } else if (mode.equals("recording")) {
    boolean switchingFromPlayback = config.source.equals("recording");
    config.source = "device";
    config.recordEnabled = true;
    playbackPaused = false;
    playbackStepRequest = 0;
    lastPausedPlaybackTransmitMillis = 0;
    if (switchingFromPlayback) {
      if (packetRecorder.isActive()) {
        packetRecorder.stop();
      }
      requestRestart();
      uiStatus = "starting recording";
    } else if (packetRecorder.isActive()) {
      uiStatus = "recording";
    } else if (captureThread != null && captureThread.isAlive()) {
      boolean ok = startPacketRecording();
      config.recordEnabled = ok;
      uiStatus = ok ? "recording" : "record failed";
    } else {
      requestRestart();
      uiStatus = "starting recording";
    }
  } else if (mode.equals("playback")) {
    startPlaybackFromDefaultRecording();
  }
  uiStatusMillis = millis();
}

void startPlaybackFromDefaultRecording() {
  String justRecorded = "";
  if (packetRecorder.isActive()) {
    justRecorded = packetRecorder.stop();
  }
  String recording = "";
  if (justRecorded.length() > 0 && recordingFile(justRecorded).exists()) {
    recording = justRecorded;
  } else {
    recording = latestRecordingFile();
  }
  if (recording.length() == 0) {
    recording = selectedRecordingFile();
  }
  if (recording.length() == 0) {
    uiStatus = "no recordings";
    return;
  }
  startPlaybackFromRecording(recording);
}

void openRecordingFileDialog() {
  String dirName = config.recordDir == null || config.recordDir.length() == 0 ? "recordings" : config.recordDir;
  File dir = new File(dataPath(dirName));
  if (!dir.exists()) {
    dir.mkdirs();
  }
  selectInput("Select a Sensel recording", "recordingSelected", new File(dir, "sensel_recording.jsonl"));
}

void recordingSelected(File selection) {
  if (selection == null) {
    uiStatus = "load canceled";
    uiStatusMillis = millis();
    return;
  }
  String name = selection.getName().toLowerCase();
  if (!name.endsWith(".jsonl") && !name.endsWith(".json")) {
    uiStatus = "not a recording";
    uiStatusMillis = millis();
    return;
  }
  startPlaybackFromRecording(recordingPathFromSelection(selection));
}

String recordingPathFromSelection(File selection) {
  try {
    String dataRoot = new File(dataPath("")).getCanonicalPath();
    String selectedPath = selection.getCanonicalPath();
    if (selectedPath.startsWith(dataRoot + File.separator)) {
      return selectedPath.substring(dataRoot.length() + 1).replace(File.separatorChar, '/');
    }
    return selectedPath;
  } catch (Exception e) {
    return selection.getAbsolutePath();
  }
}

void startPlaybackFromRecording(String recording) {
  if (recording == null || recording.length() == 0) {
    uiStatus = "no recording selected";
    uiStatusMillis = millis();
    return;
  }
  if (packetRecorder.isActive()) {
    packetRecorder.stop();
  }
  config.source = "recording";
  config.recordEnabled = false;
  playbackPaused = false;
  playbackStepRequest = 0;
  lastPausedPlaybackTransmitMillis = 0;
  config.recordingFile = recording;
  requestRestart();
  uiStatus = "loading " + new File(recording).getName();
  uiStatusMillis = millis();
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

void mousePressed() {
  if (showHelp) {
    if (handleLocalViewMouse(mouseX, mouseY)) {
      return;
    }
    handleSettingsUiMouse(mouseX, mouseY);
  }
}
