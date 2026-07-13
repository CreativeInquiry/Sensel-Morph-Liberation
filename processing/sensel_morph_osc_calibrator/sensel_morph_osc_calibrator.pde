import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileOutputStream;

final int SENSOR_W = 185;
final int SENSOR_H = 105;
final int CELLS = SENSOR_W * SENSOR_H;
final int OSC_PORT = 1560;
final int PASS_COUNT = 9;
// Calibration aggregate: 1 = median, 3 = average middle three, 5 = average middle five.
final int CALIBRATION_MIDDLE_COUNT = 5;
final int FALLBACK_COVERAGE_THRESHOLD_U8 = 1;
final int FALLBACK_COVERAGE_THRESHOLD_U16 = 8;
final int DARK_CAPTURE_MS = 10000;
final int CHANNEL_TIMEOUT_MS = 2000;
final float DARK_NOISE_MULTIPLIER = 3.0f;
final float MIN_GAIN = 0.5f;
final float MAX_GAIN = 2.0f;
final float GAIN_U16_ONE = 32768.0f;

int currentSlot = 1; // 0 is dark, 1..9 are brush passes.
boolean showHud = true;
boolean calibrationView = false;

int[] latestPressure = new int[CELLS];
int latestFrameId = -1;
int latestPressureFrameId = -1;
int latestPressureBitDepth = 0;
float latestPressureMax = 0;
int latestPressureFrameMax = 0;
boolean havePressure = false;
int lastPressureMillis = 0;

int[][] passMax = new int[PASS_COUNT][CELLS];
boolean[] passHasData = new boolean[PASS_COUNT];
int[] passFrameCounts = new int[PASS_COUNT];

double[] darkSum = new double[CELLS];
int[] darkAvg = new int[CELLS];
int[] darkMax = new int[CELLS];
int darkFrames = 0;
int darkStartMillis = 0;
boolean darkCapturing = false;
boolean darkValid = false;
int darkAvgMax = 0;
int darkNoiseP99 = 0;
int darkNoiseMax = 0;
int coverageThreshold = FALLBACK_COVERAGE_THRESHOLD_U16;
int coverageThresholdFromNoise = 0;
String coverageThresholdSource = "floor";

int[] lightCalibration = new int[CELLS];
boolean[] calibrated = new boolean[CELLS];
float[] gain = new float[CELLS];
boolean calibrationValid = false;
float calibrationTarget = 0;
int calibratedPixelCount = 0;

PImage displayImage;
String latestStatus = "waiting for OSC pressure on UDP " + OSC_PORT;
String latestRasterTransport = "unknown";
String saveStatus = "";
String deviceSerial = "";
int packetsReceived = 0;
int messagesReceived = 0;
int droppedPackets = 0;
long lastMessageMillis = 0;
int lastFrameFpsMillis = 0;
int lastFpsFrameId = -1;
float frameReceiveFps = 0;

DatagramSocket oscSocket;
Thread oscThread;
volatile boolean oscRunning = false;
ConcurrentLinkedQueue<OscMessage> oscQueue = new ConcurrentLinkedQueue<OscMessage>();
HashMap<Integer, RasterChunks> pressureChunks = new HashMap<Integer, RasterChunks>();

void setup() {
  size(1280, 720, P2D);
  surface.setTitle("Sensel Morph OSC Calibrator");
  noSmooth();
  frameRate(60);
  Arrays.fill(gain, 1.0f);
  displayImage = createImage(SENSOR_W, SENSOR_H, RGB);
  startOscReceiver();
}

void draw() {
  drainOscMessages();
  clearStaleChannels();
  updateDarkCapture();
  updateDisplayImage();

  background(0);
  noSmooth();
  image(displayImage, 0, 0, width, height);

  if (showHud) {
    drawHud();
  }
}

/** Select the current visual feedback image: dark map, pass maxima, or calibration. */
void updateDisplayImage() {
  if (calibrationView && calibrationValid) {
    displayImage = imageFromU16(lightCalibration, calibrated);
    return;
  }
  if (currentSlot == 0) {
    if (darkCapturing && darkFrames > 0) {
      displayImage = imageFromDarkRunningAverage();
    } else if (darkValid) {
      displayImage = imageFromAbsolutePressure(darkAvg, null);
    } else {
      displayImage = imageFromU16(new int[CELLS], null);
    }
    return;
  }
  displayImage = imageFromU16(passMax[currentSlot - 1], null);
}

PImage imageFromDarkRunningAverage() {
  int[] avg = new int[CELLS];
  for (int i = 0; i < CELLS; i++) {
    avg[i] = darkFrames > 0 ? constrain(round((float) (darkSum[i] / darkFrames)), 0, 65535) : 0;
  }
  return imageFromAbsolutePressure(avg, null);
}

PImage imageFromU16(int[] values, boolean[] validMask) {
  PImage img = createImage(SENSOR_W, SENSOR_H, RGB);
  int maxValue = 0;
  for (int i = 0; i < CELLS; i++) {
    if (validMask != null && !validMask[i]) {
      continue;
    }
    maxValue = max(maxValue, values[i]);
  }
  float scale = maxValue > 0 ? 255.0f / maxValue : 0.0f;
  img.loadPixels();
  for (int i = 0; i < CELLS; i++) {
    if (validMask != null && !validMask[i]) {
      img.pixels[i] = 0xff240000;
    } else {
      int gray = constrain(round(values[i] * scale), 0, 255);
      img.pixels[i] = 0xff000000 | (gray << 16) | (gray << 8) | gray;
    }
  }
  img.updatePixels();
  return img;
}

PImage imageFromAbsolutePressure(int[] values, boolean[] validMask) {
  int maxDisplay = latestPressureBitDepth == 8 ? 255 : 65535;
  return imageFromFixedScale(values, validMask, maxDisplay);
}

PImage imageFromFixedScale(int[] values, boolean[] validMask, int maxDisplayValue) {
  PImage img = createImage(SENSOR_W, SENSOR_H, RGB);
  float scale = maxDisplayValue > 0 ? 255.0f / maxDisplayValue : 0.0f;
  img.loadPixels();
  for (int i = 0; i < CELLS; i++) {
    if (validMask != null && !validMask[i]) {
      img.pixels[i] = 0xff240000;
    } else {
      int gray = constrain(round(values[i] * scale), 0, 255);
      img.pixels[i] = 0xff000000 | (gray << 16) | (gray << 8) | gray;
    }
  }
  img.updatePixels();
  return img;
}

void drawHud() {
  fill(0, 185);
  noStroke();
  rect(0, 0, width, 92);

  fill(235);
  textAlign(LEFT, TOP);
  textSize(12);
  String age = lastMessageMillis == 0 ? "never" : nf((millis() - lastMessageMillis) / 1000.0f, 1, 2) + "s ago";
  text("Sensel Morph Calibrator   OSC " + OSC_PORT
    + "   frame " + latestFrameId
    + "   rx " + nf(frameReceiveFps, 1, 1) + " fps"
    + "   pressure " + latestPressureBitDepth + "-bit current_max " + latestPressureFrameMax
    + " osc_max " + nf(latestPressureMax, 1, 1)
    + "   transport " + latestRasterTransport
    + "   last " + age, 12, 8);

  text(slotLabel()
    + "   pass frames " + currentPassFrames()
    + "   scene_global_max " + currentSceneGlobalMax()
    + "   dark " + darkStatus()
    + "   calibrated pixels " + calibratedPixelCount + "/" + CELLS
    + "   threshold " + coverageThreshold
    + " (" + coverageThresholdSource + ")"
    + "   serial " + serialLabel(), 12, 28);

  text("0 dark/no-touch 10s avg   1..9 brush pass   space clear current slot   return compute+save   +/- adjust threshold   h hide HUD", 12, 48);
  text(latestStatus + "   packets " + packetsReceived + " messages " + messagesReceived + " dropped " + droppedPackets, 12, 68);

  fill(255);
  textSize(26);
  textAlign(LEFT, TOP);
  text(currentSlot == 0 ? "0" : str(currentSlot), 12, 98);

  if (saveStatus.length() > 0) {
    fill(255, 235, 120);
    textSize(12);
    text(saveStatus, 46, 106);
  }
}

String slotLabel() {
  if (currentSlot == 0) {
    return darkCapturing ? "slot 0 dark capturing" : "slot 0 dark";
  }
  return calibrationView ? "calibration " + calibrationAggregateLabel() + " view" : "slot " + currentSlot + " brush max";
}

int currentPassFrames() {
  if (currentSlot < 1 || currentSlot > PASS_COUNT) {
    return 0;
  }
  return passFrameCounts[currentSlot - 1];
}

int currentSceneGlobalMax() {
  if (calibrationView && calibrationValid) {
    return maxValue(lightCalibration);
  }
  if (currentSlot == 0) {
    if (darkCapturing || darkValid) {
      return maxValue(darkMax);
    }
    return 0;
  }
  if (currentSlot >= 1 && currentSlot <= PASS_COUNT) {
    return maxValue(passMax[currentSlot - 1]);
  }
  return 0;
}

int maxValue(int[] values) {
  int out = 0;
  for (int i = 0; i < values.length; i++) {
    if (values[i] > out) {
      out = values[i];
    }
  }
  return out;
}

String darkStatus() {
  if (darkCapturing) {
    return nf(min(DARK_CAPTURE_MS, millis() - darkStartMillis) / 1000.0f, 1, 1) + "/10.0s frames " + darkFrames;
  }
  return darkValid
    ? "ready frames " + darkFrames + " avg_max " + darkAvgMax + " noise99 " + darkNoiseP99 + " noise_max " + darkNoiseMax
    : "none";
}

String serialLabel() {
  return deviceSerial.length() > 0 ? deviceSerial : "unknown";
}

int fallbackCoverageThreshold() {
  return latestPressureBitDepth == 8 ? FALLBACK_COVERAGE_THRESHOLD_U8 : FALLBACK_COVERAGE_THRESHOLD_U16;
}

int thresholdStep() {
  return latestPressureBitDepth == 8 ? 1 : 16;
}

/** Derive a coverage threshold from the no-touch dark/noise capture. */
void updateCoverageThresholdFromDark() {
  int[] deviations = new int[CELLS];
  darkAvgMax = 0;
  darkNoiseMax = 0;
  for (int i = 0; i < CELLS; i++) {
    darkAvgMax = max(darkAvgMax, darkAvg[i]);
    int deviation = max(0, darkMax[i] - darkAvg[i]);
    deviations[i] = deviation;
    darkNoiseMax = max(darkNoiseMax, deviation);
  }
  darkNoiseP99 = percentile(deviations, 0.99f);
  coverageThresholdFromNoise = ceil(darkNoiseP99 * DARK_NOISE_MULTIPLIER);
  int floorThreshold = fallbackCoverageThreshold();
  coverageThreshold = max(floorThreshold, coverageThresholdFromNoise);
  coverageThresholdSource = coverageThresholdFromNoise > floorThreshold ? "noise" : "floor";
}

int percentile(int[] values, float q) {
  int[] copy = Arrays.copyOf(values, values.length);
  Arrays.sort(copy);
  int index = constrain(round((copy.length - 1) * q), 0, copy.length - 1);
  return copy[index];
}

void keyPressed() {
  if (key == 'h' || key == 'H') {
    showHud = !showHud;
    return;
  }

  if (key >= '1' && key <= '9') {
    currentSlot = key - '0';
    calibrationView = false;
    saveStatus = "";
    return;
  }

  if (key == '0') {
    startDarkCapture();
    return;
  }

  if (key == ' ') {
    clearCurrentSlot();
    return;
  }

  if (key == '+' || key == '=') {
    coverageThreshold = max(0, coverageThreshold + thresholdStep());
    coverageThresholdSource = "manual";
    saveStatus = "coverage threshold set to " + coverageThreshold;
    return;
  }

  if (key == '-' || key == '_') {
    coverageThreshold = max(0, coverageThreshold - thresholdStep());
    coverageThresholdSource = "manual";
    saveStatus = "coverage threshold set to " + coverageThreshold;
    return;
  }

  if (key == ENTER || key == RETURN) {
    computeAndSaveCalibration();
  }
}

void startDarkCapture() {
  currentSlot = 0;
  calibrationView = false;
  darkCapturing = true;
  darkValid = false;
  darkFrames = 0;
  darkStartMillis = millis();
  Arrays.fill(darkSum, 0);
  Arrays.fill(darkAvg, 0);
  Arrays.fill(darkMax, 0);
  darkAvgMax = 0;
  darkNoiseP99 = 0;
  darkNoiseMax = 0;
  coverageThresholdFromNoise = 0;
  coverageThresholdSource = "floor";
  saveStatus = "dark capture started; do not touch the surface";
}

void updateDarkCapture() {
  if (darkCapturing && millis() - darkStartMillis >= DARK_CAPTURE_MS) {
    finishDarkCapture();
  }
}

void finishDarkCapture() {
  if (darkFrames > 0) {
    for (int i = 0; i < CELLS; i++) {
      darkAvg[i] = constrain(round((float) (darkSum[i] / darkFrames)), 0, 65535);
    }
    darkValid = true;
    updateCoverageThresholdFromDark();
    saveStatus = "dark capture complete: " + darkFrames + " frames; threshold " + coverageThreshold;
  } else {
    darkValid = false;
    saveStatus = "dark capture failed: no frames";
  }
  darkCapturing = false;
}

void clearCurrentSlot() {
  calibrationView = false;
  if (currentSlot == 0) {
    darkCapturing = false;
    darkValid = false;
    darkFrames = 0;
    Arrays.fill(darkSum, 0);
    Arrays.fill(darkAvg, 0);
    Arrays.fill(darkMax, 0);
    darkAvgMax = 0;
    darkNoiseP99 = 0;
    darkNoiseMax = 0;
    coverageThresholdFromNoise = 0;
    coverageThreshold = fallbackCoverageThreshold();
    coverageThresholdSource = "floor";
    saveStatus = "dark map cleared";
    return;
  }

  int idx = currentSlot - 1;
  Arrays.fill(passMax[idx], 0);
  passHasData[idx] = false;
  passFrameCounts[idx] = 0;
  saveStatus = "pass " + currentSlot + " cleared";
}

/** Accept one OSC pressure raster and accumulate max values for the active pass. */
void acceptPressureRaster(int frameId, int w, int h, int bitDepth, float maxValue, byte[] blob) {
  if (w != SENSOR_W || h != SENSOR_H) {
    latestStatus = "expected " + SENSOR_W + "x" + SENSOR_H + " pressure, got " + w + "x" + h;
    return;
  }
  int cells = w * h;
  if (bitDepth == 16 && blob.length < cells * 2) {
    return;
  }
  if (bitDepth == 8 && blob.length < cells) {
    return;
  }

  for (int i = 0; i < CELLS; i++) {
    if (bitDepth == 16) {
      latestPressure[i] = (blob[i * 2] & 0xff) | ((blob[i * 2 + 1] & 0xff) << 8);
    } else {
      latestPressure[i] = blob[i] & 0xff;
    }
  }
  latestPressureFrameMax = maxValue(latestPressure);

  havePressure = true;
  latestPressureFrameId = frameId;
  latestFrameId = frameId;
  latestPressureBitDepth = bitDepth;
  latestPressureMax = maxValue;
  lastPressureMillis = millis();
  if (!darkValid && darkFrames == 0) {
    coverageThreshold = fallbackCoverageThreshold();
    coverageThresholdSource = "floor";
  }
  noteFrameReceived(frameId);

  if (darkCapturing) {
    for (int i = 0; i < CELLS; i++) {
      darkSum[i] += latestPressure[i];
      if (latestPressure[i] > darkMax[i]) {
        darkMax[i] = latestPressure[i];
      }
    }
    darkFrames++;
    return;
  }

  if (!calibrationView && currentSlot >= 1 && currentSlot <= PASS_COUNT) {
    int idx = currentSlot - 1;
    for (int i = 0; i < CELLS; i++) {
      if (latestPressure[i] > passMax[idx][i]) {
        passMax[idx][i] = latestPressure[i];
      }
    }
    passHasData[idx] = true;
    passFrameCounts[idx]++;
  }
}

void clearStaleChannels() {
  if (lastPressureMillis > 0 && millis() - lastPressureMillis > CHANNEL_TIMEOUT_MS) {
    clearPressureChannel();
  }
}

void clearPressureChannel() {
  Arrays.fill(latestPressure, 0);
  havePressure = false;
  darkCapturing = false;
  latestPressureFrameId = -1;
  latestPressureBitDepth = 0;
  latestPressureMax = 0;
  latestPressureFrameMax = 0;
  pressureChunks.clear();
  lastPressureMillis = 0;
}

/** Compute dark-subtracted light response, coverage, gain, and save all outputs. */
void computeAndSaveCalibration() {
  if (darkCapturing) {
    finishDarkCapture();
  }

  int[] targetCandidates = new int[CELLS];
  int targetCount = 0;

  for (int i = 0; i < CELLS; i++) {
    int[] values = new int[PASS_COUNT];
    int count = 0;
    int dark = darkValid ? darkAvg[i] : 0;
    for (int pass = 0; pass < PASS_COUNT; pass++) {
      if (!passHasData[pass]) {
        continue;
      }
      int corrected = max(0, passMax[pass][i] - dark);
      if (corrected >= coverageThreshold) {
        values[count++] = corrected;
      }
    }
    if (count > 0) {
      int calibrationValue = calibrationAggregate(values, count);
      lightCalibration[i] = calibrationValue;
      calibrated[i] = true;
      targetCandidates[targetCount++] = calibrationValue;
    } else {
      lightCalibration[i] = 0;
      calibrated[i] = false;
    }
  }

  calibratedPixelCount = targetCount;
  if (targetCount <= 0) {
    calibrationValid = false;
    saveStatus = "calibration failed: no covered pixels above threshold " + coverageThreshold
      + "; press - to lower it or recapture dark with 0";
    return;
  }

  calibrationTarget = median(targetCandidates, targetCount);
  for (int i = 0; i < CELLS; i++) {
    if (calibrated[i] && lightCalibration[i] > 0) {
      gain[i] = constrain(calibrationTarget / (float) lightCalibration[i], MIN_GAIN, MAX_GAIN);
    } else {
      gain[i] = 1.0f;
    }
  }

  calibrationValid = true;
  calibrationView = true;
  saveCalibrationFiles();
}

int median(int[] values, int count) {
  Arrays.sort(values, 0, count);
  if ((count & 1) == 1) {
    return values[count / 2];
  }
  return round((values[count / 2 - 1] + values[count / 2]) * 0.5f);
}

/** Aggregate up to nine brush-pass maxima using median/winsorized settings. */
int calibrationAggregate(int[] values, int count) {
  if (CALIBRATION_MIDDLE_COUNT <= 1) {
    return median(values, count);
  }
  return winsorizedMiddleMean(values, count, CALIBRATION_MIDDLE_COUNT);
}

String calibrationAggregateLabel() {
  if (CALIBRATION_MIDDLE_COUNT <= 1) {
    return "median";
  }
  return "middle-" + CALIBRATION_MIDDLE_COUNT;
}

int winsorizedMiddleMean(int[] values, int count, int middleCount) {
  Arrays.sort(values, 0, count);
  int n = min(count, middleCount);
  int start = (count - n) / 2;
  int sum = 0;
  for (int i = start; i < start + n; i++) {
    sum += values[i];
  }
  return round(sum / (float) n);
}

void saveCalibrationFiles() {
  String stamp = nf(year(), 4) + nf(month(), 2) + nf(day(), 2) + "_" + nf(hour(), 2) + nf(minute(), 2) + nf(second(), 2);
  File dir = new File(sketchPath("calibrations/" + stamp));
  dir.mkdirs();

  int[] gainU16 = new int[CELLS];
  int[] coverageU16 = new int[CELLS];
  for (int i = 0; i < CELLS; i++) {
    gainU16[i] = constrain(round(gain[i] * GAIN_U16_ONE), 0, 65535);
    coverageU16[i] = calibrated[i] ? 65535 : 0;
  }

  boolean darkOk = saveU16Tiff(new File(dir, "dark_185x105_u16.tif"), darkAvg);
  boolean lightOk = saveU16Tiff(new File(dir, "light_dark_subtracted_185x105_u16.tif"), lightCalibration);
  boolean gainOk = saveU16Tiff(new File(dir, "gain_185x105_u16_gain32768.tif"), gainU16);
  boolean maskOk = saveU16Tiff(new File(dir, "coverage_mask_185x105_u16.tif"), coverageU16);
  boolean gainF32TiffOk = saveF32Tiff(new File(dir, "gain_185x105_f32.tif"), gain);
  boolean pfmOk = saveGainPfm(new File(dir, "gain_185x105_f32.pfm"));
  boolean jsonOk = saveGainJson(new File(dir, calibrationJsonFileName()), stamp);
  boolean passesOk = savePassMaxTiffs(dir);

  saveStatus = "saved " + dir.getName()
    + " tiff=" + (darkOk && lightOk && gainOk && maskOk ? "ok" : "check")
    + " passes=" + (passesOk ? "ok" : "check")
    + " f32tiff=" + (gainF32TiffOk ? "ok" : "fail")
    + " pfm=" + (pfmOk ? "ok" : "fail")
    + " json=" + (jsonOk ? "ok" : "fail");
  println(saveStatus);
  println(dir.getAbsolutePath());
}

String calibrationJsonFileName() {
  return "calibration_" + safeFileToken(serialLabel()) + ".json";
}

String safeFileToken(String value) {
  String out = "";
  for (int i = 0; i < value.length(); i++) {
    char ch = value.charAt(i);
    boolean ok = (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '-';
    out += ok ? ch : '_';
  }
  return out.length() > 0 ? out : "unknown";
}

boolean savePassMaxTiffs(File dir) {
  boolean ok = true;
  for (int pass = 0; pass < PASS_COUNT; pass++) {
    if (!passHasData[pass]) {
      continue;
    }
    String name = "pass_" + nf(pass + 1, 2) + "_max_185x105_u16.tif";
    ok = saveU16Tiff(new File(dir, name), passMax[pass]) && ok;
  }
  return ok;
}

/** Save a Photoshop-compatible one-channel little-endian 16-bit TIFF. */
boolean saveU16Tiff(File file, int[] values) {
  try {
    DataOutputStream out = new DataOutputStream(new BufferedOutputStream(new FileOutputStream(file)));
    int imageBytes = CELLS * 2;
    int ifdOffset = 8 + imageBytes;
    int entryCount = 14;
    int rationalOffset = ifdOffset + 2 + entryCount * 12 + 4;

    writeTiffHeader(out, ifdOffset);
    for (int i = 0; i < CELLS; i++) {
      writeU16LE(out, constrain(values[i], 0, 65535));
    }
    writeSingleChannelTiffIfd(out, 16, 1, imageBytes, entryCount, rationalOffset, calibrationDescription(file.getName()));
    out.close();
    return true;
  } catch (Exception e) {
    println("TIFF save failed: " + file + " " + e.getMessage());
    return false;
  }
}

/** Save a one-channel 32-bit float TIFF for tools that can read float images. */
boolean saveF32Tiff(File file, float[] values) {
  try {
    DataOutputStream out = new DataOutputStream(new BufferedOutputStream(new FileOutputStream(file)));
    int imageBytes = CELLS * 4;
    int ifdOffset = 8 + imageBytes;
    int entryCount = 14;
    int rationalOffset = ifdOffset + 2 + entryCount * 12 + 4;

    writeTiffHeader(out, ifdOffset);
    for (int i = 0; i < CELLS; i++) {
      writeFloatLE(out, values[i]);
    }
    writeSingleChannelTiffIfd(out, 32, 3, imageBytes, entryCount, rationalOffset, calibrationDescription(file.getName()));
    out.close();
    return true;
  } catch (Exception e) {
    println("F32 TIFF save failed: " + file + " " + e.getMessage());
    return false;
  }
}

void writeTiffHeader(DataOutputStream out, int ifdOffset) throws java.io.IOException {
  out.writeByte('I');
  out.writeByte('I');
  writeU16LE(out, 42);
  writeU32LE(out, ifdOffset);
}

String calibrationDescription(String fileName) {
  return "Sensel Morph calibration; device_serial=" + serialLabel()
    + "; width=" + SENSOR_W
    + "; height=" + SENSOR_H
    + "; file=" + fileName;
}

void writeSingleChannelTiffIfd(DataOutputStream out, int bitsPerSample, int sampleFormat, int imageBytes, int entryCount, int rationalOffset, String description) throws java.io.IOException {
  byte[] descriptionBytes = asciiNullTerminated(description);
  int descriptionOffset = rationalOffset + 16;
  writeU16LE(out, entryCount);
  writeTiffEntry(out, 256, 4, 1, SENSOR_W); // ImageWidth
  writeTiffEntry(out, 257, 4, 1, SENSOR_H); // ImageLength
  writeTiffEntry(out, 258, 3, 1, bitsPerSample); // BitsPerSample
  writeTiffEntry(out, 259, 3, 1, 1); // Compression: none
  writeTiffEntry(out, 262, 3, 1, 1); // PhotometricInterpretation: BlackIsZero
  writeTiffEntry(out, 270, 2, descriptionBytes.length, descriptionOffset); // ImageDescription
  writeTiffEntry(out, 273, 4, 1, 8); // StripOffsets
  writeTiffEntry(out, 277, 3, 1, 1); // SamplesPerPixel
  writeTiffEntry(out, 278, 4, 1, SENSOR_H); // RowsPerStrip
  writeTiffEntry(out, 279, 4, 1, imageBytes); // StripByteCounts
  writeTiffEntry(out, 282, 5, 1, rationalOffset); // XResolution
  writeTiffEntry(out, 283, 5, 1, rationalOffset + 8); // YResolution
  writeTiffEntry(out, 296, 3, 1, 2); // ResolutionUnit: inch
  writeTiffEntry(out, 339, 3, 1, sampleFormat); // 1 unsigned int, 3 IEEE float
  writeU32LE(out, 0); // next IFD
  writeU32LE(out, 72);
  writeU32LE(out, 1);
  writeU32LE(out, 72);
  writeU32LE(out, 1);
  out.write(descriptionBytes);
}

void writeTiffEntry(DataOutputStream out, int tag, int type, int count, int value) throws java.io.IOException {
  writeU16LE(out, tag);
  writeU16LE(out, type);
  writeU32LE(out, count);
  if (type == 3 && count == 1) {
    writeU16LE(out, value);
    writeU16LE(out, 0);
  } else {
    writeU32LE(out, value);
  }
}

void writeU16LE(DataOutputStream out, int value) throws java.io.IOException {
  out.write(value & 0xff);
  out.write((value >>> 8) & 0xff);
}

void writeU32LE(DataOutputStream out, int value) throws java.io.IOException {
  out.write(value & 0xff);
  out.write((value >>> 8) & 0xff);
  out.write((value >>> 16) & 0xff);
  out.write((value >>> 24) & 0xff);
}

byte[] asciiNullTerminated(String value) {
  byte[] out = new byte[value.length() + 1];
  for (int i = 0; i < value.length(); i++) {
    char ch = value.charAt(i);
    out[i] = (byte) (ch < 128 ? ch : '?');
  }
  out[out.length - 1] = 0;
  return out;
}

boolean saveGainPfm(File file) {
  return saveGainPfm(file, gain);
}

boolean saveGainPfm(File file, float[] values) {
  try {
    DataOutputStream out = new DataOutputStream(new BufferedOutputStream(new FileOutputStream(file)));
    out.writeBytes("Pf\n# device_serial=" + serialLabel() + "\n" + SENSOR_W + " " + SENSOR_H + "\n-1.0\n");
    for (int i = 0; i < CELLS; i++) {
      writeFloatLE(out, values[i]);
    }
    out.close();
    return true;
  } catch (Exception e) {
    println("PFM save failed: " + e.getMessage());
    return false;
  }
}

void writeFloatLE(DataOutputStream out, float value) throws java.io.IOException {
  int bits = Float.floatToIntBits(value);
  out.write(bits & 0xff);
  out.write((bits >>> 8) & 0xff);
  out.write((bits >>> 16) & 0xff);
  out.write((bits >>> 24) & 0xff);
}

boolean saveGainJson(File file, String stamp) {
  try {
    ArrayList<String> lines = new ArrayList<String>();
    lines.add("{");
    lines.add("  \"created_at\": \"" + stamp + "\",");
    lines.add("  \"device_serial\": \"" + jsonEscape(serialLabel()) + "\",");
    lines.add("  \"width\": " + SENSOR_W + ",");
    lines.add("  \"height\": " + SENSOR_H + ",");
    lines.add("  \"coverage_threshold\": " + coverageThreshold + ",");
    lines.add("  \"coverage_threshold_from_noise\": " + coverageThresholdFromNoise + ",");
    lines.add("  \"coverage_threshold_source\": \"" + coverageThresholdSource + "\",");
    lines.add("  \"dark_frames\": " + darkFrames + ",");
    lines.add("  \"dark_avg_max\": " + darkAvgMax + ",");
    lines.add("  \"dark_noise_p99\": " + darkNoiseP99 + ",");
    lines.add("  \"dark_noise_max\": " + darkNoiseMax + ",");
    lines.add("  \"aggregate\": \"" + calibrationAggregateLabel() + "\",");
    lines.add("  \"aggregate_middle_count\": " + CALIBRATION_MIDDLE_COUNT + ",");
    lines.add("  \"target\": " + nf(calibrationTarget, 1, 6) + ",");
    lines.add("  \"min_gain\": " + nf(MIN_GAIN, 1, 6) + ",");
    lines.add("  \"max_gain\": " + nf(MAX_GAIN, 1, 6) + ",");
    appendIntArray(lines, "dark", darkAvg, true);
    appendIntArray(lines, "light", lightCalibration, true);
    appendIntArray(lines, "coverage", coverageAsInts(), true);
    appendFloatArray(lines, "gain", gain, false);
    lines.add("}");
    String[] out = new String[lines.size()];
    saveStrings(file.getAbsolutePath(), lines.toArray(out));
    return true;
  } catch (Exception e) {
    println("JSON save failed: " + e.getMessage());
    return false;
  }
}

int[] coverageAsInts() {
  int[] out = new int[CELLS];
  for (int i = 0; i < CELLS; i++) {
    out[i] = calibrated[i] ? 1 : 0;
  }
  return out;
}

void appendIntArray(ArrayList<String> lines, String name, int[] values, boolean commaAfter) {
  lines.add("  \"" + name + "\": [");
  for (int i = 0; i < values.length; i++) {
    lines.add("    " + values[i] + (i == values.length - 1 ? "" : ","));
  }
  lines.add("  ]" + (commaAfter ? "," : ""));
}

void appendFloatArray(ArrayList<String> lines, String name, float[] values, boolean commaAfter) {
  lines.add("  \"" + name + "\": [");
  for (int i = 0; i < values.length; i++) {
    lines.add("    " + nf(values[i], 1, 8) + (i == values.length - 1 ? "" : ","));
  }
  lines.add("  ]" + (commaAfter ? "," : ""));
}

String jsonEscape(String value) {
  return value.replace("\\", "\\\\").replace("\"", "\\\"");
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

/** Route incoming OSC pressure/status/chunk messages from the transmitter. */
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
    if (msg.args.size() > 1 && (contentMask & 0x01) == 0) {
      clearPressureChannel();
    }
    latestStatus = device
      + "   serial " + serialLabel()
      + "   content 0x" + hex(contentMask, 2)
      + "   " + pressureRes + "/" + pressureType
      + "   rle " + (rle ? "on" : "off");
    return;
  }

  if (msg.address.equals("/sensel_morph/frame")) {
    latestFrameId = asInt(msg.args.get(0));
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure")) {
    latestRasterTransport = "raw";
    if (msg.args.size() >= 6) {
      acceptPressureRaster(
        asInt(msg.args.get(0)),
        asInt(msg.args.get(1)),
        asInt(msg.args.get(2)),
        asInt(msg.args.get(3)),
        asFloat(msg.args.get(4)),
        asBytes(msg.args.get(5))
      );
    }
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure_rle")) {
    latestRasterTransport = "rle";
    if (msg.args.size() >= 7) {
      int decodedBytes = asInt(msg.args.get(5));
      byte[] blob = rleDecode(asBytes(msg.args.get(6)), decodedBytes);
      if (blob != null) {
        acceptPressureRaster(
          asInt(msg.args.get(0)),
          asInt(msg.args.get(1)),
          asInt(msg.args.get(2)),
          asInt(msg.args.get(3)),
          asFloat(msg.args.get(4)),
          blob
        );
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
    acceptRasterChunk(msg);
    return;
  }

  if (msg.address.equals("/sensel_morph/pressure_rle/chunk")) {
    latestRasterTransport = "rle";
    acceptRasterChunk(msg);
  }
}

void acceptRasterChunk(OscMessage msg) {
  if (msg.args.size() < 4) {
    return;
  }
  int frameId = asInt(msg.args.get(0));
  RasterChunks chunks = pressureChunks.get(frameId);
  if (chunks == null) {
    return;
  }
  int chunkIndex = asInt(msg.args.get(1));
  byte[] blob = asBytes(msg.args.get(3));
  if (chunks.accept(chunkIndex, blob)) {
    byte[] complete = chunks.join();
    pressureChunks.remove(frameId);
    if (chunks.rle) {
      complete = rleDecode(complete, chunks.decodedBytes);
      if (complete == null) {
        return;
      }
    }
    acceptPressureRaster(frameId, chunks.width, chunks.height, chunks.bitDepth, chunks.maxValue, complete);
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
  float instant = 1000.0f / elapsed;
  if (currentFps <= 0) {
    return instant;
  }
  return currentFps * 0.85f + instant * 0.15f;
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
    offset[0] += 8;
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
      int size = readI32BE(data, offset[0]);
      offset[0] += 4;
      if (size < 0 || offset[0] + size > end) {
        return;
      }
      byte[] blob = new byte[size];
      arrayCopy(data, offset[0], blob, 0, size);
      args.add(blob);
      offset[0] += paddedSize(size);
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
  offset[0] = start + paddedSize(pos - start + 1);
  if (offset[0] > end) {
    return null;
  }
  return value;
}

int paddedSize(int n) {
  return (n + 3) & ~3;
}

int readI32BE(byte[] data, int offset) {
  return ((data[offset] & 0xff) << 24)
    | ((data[offset + 1] & 0xff) << 16)
    | ((data[offset + 2] & 0xff) << 8)
    | (data[offset + 3] & 0xff);
}
