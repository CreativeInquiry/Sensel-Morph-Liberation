// Sensel Morph Capture Viewer
// Plays raw Sensel Morph JSONL/JSON recordings from data/recordings/.

import processing.opengl.PGraphicsOpenGL;
import java.io.ByteArrayOutputStream;
import java.io.File;
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
final int TEXTURE_SAMPLING_LINEAR = 4;
final int SURFACE_SAMPLING_NEAREST = 0;
final int SURFACE_SAMPLING_LINEAR = 1;

MorphSettings config;
PressureCalibration calibration;
RecordingFrameSource recordingSource;
RawPacketRecorder packetRecorder = new RawPacketRecorder();
ArrayList<Grid> currentPlaybackGrids = new ArrayList<Grid>();

boolean running = true;
boolean playbackPaused = false;
boolean showHelp = true;
int displayMode = 7;
int surfaceSamplingModeIndex = SURFACE_SAMPLING_NEAREST;
boolean surfaceRefreshRequested = false;
String pendingRecordingToLoad = "";
int pendingStepDelta = 0;
String statusLine = "starting";
String uiStatus = "";
int uiStatusMillis = 0;
String currentPortName = "";
String deviceSerial = "recording";
float txFps = 0;
int transmittedFrames = 0;

PGraphics pressureBuffer;
PGraphics labelsBuffer;
PGraphics contactsBuffer;
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
  surface.setTitle("Sensel Morph Capture Viewer");
  noSmooth();
  frameRate(60);

  config = loadMorphSettings();
  configureViewerSettings();
  calibration = new PressureCalibration();
  createSurfaceBuffers();
  loadSelectedOrNewestRecording();
}

void draw() {
  background(30);
  handlePendingViewerActions();

  if (recordingSource == null) {
    drawNoRecordingMessage();
  } else {
    updatePlaybackFrame();
    drawCurrentLayers();
    drawPlaybackProgressBar();
  }

  if (showHelp) {
    drawViewerHud();
    drawViewerUi();
  }
}

void handlePendingViewerActions() {
  if (pendingRecordingToLoad.length() > 0) {
    String filename = pendingRecordingToLoad;
    pendingRecordingToLoad = "";
    loadRecording(filename);
  }

  if (pendingStepDelta != 0) {
    int delta = pendingStepDelta;
    pendingStepDelta = 0;
    stepPlayback(delta);
  }

  if (surfaceRefreshRequested) {
    surfaceRefreshRequested = false;
    updateSurfaceBuffers(pressureImage, labelsImage);
  }
}

void configureViewerSettings() {
  config.source = "recording";
  config.pressure = true;
  config.labels = true;
  config.contacts = true;
  config.useCalibration = false;
  config.recordingLoop = true;
  config.playbackPolicy = "favor_timing";
  if (config.recordDir == null || config.recordDir.length() == 0) {
    config.recordDir = "recordings";
  }
}

void loadSelectedOrNewestRecording() {
  String selected = selectedRecordingFile();
  if (selected.length() == 0) {
    recordingSource = null;
    statusLine = "No recordings found in data/recordings/";
    return;
  }
  loadRecording(selected);
}

void loadRecording(String filename) {
  try {
    recordingSource = new RecordingFrameSource(
      filename,
      true,
      config.recordingTiming,
      config.playbackPolicy,
      config.recordingFps
    );
    recordingSource.openSource();
    currentPlaybackGrids = gridsFromMetadata(recordingSource.compressionMetadata());
    if (recordingSource.recordedPressureRes.length() > 0) {
      config.pressureRes = recordingSource.recordedPressureRes;
      config.labelRes = config.pressureRes;
    }
    currentPortName = filename;
    deviceSerial = recordingSource.serialNumber;
    config.recordingFile = filename;
    playbackPaused = false;
    statusLine = "Loaded " + new File(filename).getName();
    showCurrentPlaybackFrame();
  } catch (Exception e) {
    recordingSource = null;
    statusLine = "Load failed: " + e.getMessage();
    println(statusLine);
  }
}

void updatePlaybackFrame() {
  if (recordingSource == null || playbackPaused) {
    return;
  }
  try {
    processPlaybackPacket(recordingSource.nextFrame());
  } catch (Exception e) {
    statusLine = "Playback stopped: " + e.getMessage();
    println(statusLine);
  }
}

void showCurrentPlaybackFrame() {
  if (recordingSource == null) {
    return;
  }
  try {
    processPlaybackPacket(recordingSource.currentFrame());
  } catch (Exception e) {
    try {
      processPlaybackPacket(recordingSource.nextFrame());
    } catch (Exception ignored) {
      statusLine = "Display failed: " + e.getMessage();
    }
  }
}

void processPlaybackPacket(FramePacket packet) {
  LiveFrame frame = decodeLiveFrame(packet, currentPlaybackGrids, config);
  prepareOutputRasters(frame, config);
  acceptLiveFrameForDisplay(frame);
}

void stepPlayback(int delta) {
  if (recordingSource == null) {
    return;
  }
  playbackPaused = true;
  try {
    processPlaybackPacket(recordingSource.stepFrame(delta));
  } catch (Exception e) {
    statusLine = "Step failed: " + e.getMessage();
  }
}

void drawNoRecordingMessage() {
  fill(180);
  textAlign(CENTER, CENTER);
  textSize(16);
  text(statusLine, width / 2, height / 2);
}

void openRecordingFileDialog() {
  File dir = new File(dataPath(config.recordDir));
  if (!dir.exists()) {
    dir.mkdirs();
  }
  selectInput("Select a Sensel recording", "recordingSelected", new File(dir, "sensel_recording.jsonl"));
}

void recordingSelected(File selection) {
  if (selection == null) {
    return;
  }
  String name = selection.getName().toLowerCase();
  if (!name.endsWith(".jsonl") && !name.endsWith(".json")) {
    uiStatus = "not a recording";
    uiStatusMillis = millis();
    return;
  }
  File dataDir = new File(dataPath(""));
  String recording = selection.getAbsolutePath();
  try {
    String dataPath = dataDir.getCanonicalPath();
    String selectedPath = selection.getCanonicalPath();
    if (selectedPath.startsWith(dataPath + File.separator)) {
      recording = selectedPath.substring(dataPath.length() + 1);
    }
  } catch (Exception e) {
  }
  pendingRecordingToLoad = recording;
}

String currentRunMode() {
  return "playback";
}

void keyPressed() {
  if (key >= '1' && key <= '7') {
    displayMode = key - '0';
  } else if (key == 'h' || key == 'H') {
    showHelp = !showHelp;
  } else if (key == 's' || key == 'S') {
    surfaceSamplingModeIndex = (surfaceSamplingModeIndex + 1) % 2;
    refreshSurfaceSampling();
  } else if (key == ' ') {
    playbackPaused = !playbackPaused;
  } else if (key == CODED && keyCode == RIGHT) {
    pendingStepDelta = 1;
  } else if (key == CODED && keyCode == LEFT) {
    pendingStepDelta = -1;
  }
}

void refreshSurfaceSampling() {
  surfaceRefreshRequested = true;
}
