interface FrameSource {
  void openSource();
  FramePacket nextFrame();
  void closeSource();
  String sourceName();
  byte[] compressionMetadata();
}

class RecordingFrameSource implements FrameSource {
  String filename;
  boolean loop;
  String timing;
  String playbackPolicy;
  float fixedFps;
  ArrayList<FramePacket> frames = new ArrayList<FramePacket>();
  byte[] metadata = new byte[0];
  String serialNumber = "recording";
  String recordedPressureRes = "";
  int index = 0;
  int displayedIndex = -1;
  int lastMillis = 0;
  int lastTimestamp = -1;
  int playbackStartMillis = 0;
  int playbackStartTimestamp = 0;
  int playbackStartIndex = 0;

  RecordingFrameSource(String filename, boolean loop, String timing, String playbackPolicy, float fixedFps) {
    this.filename = filename;
    this.loop = loop;
    this.timing = timing;
    this.playbackPolicy = playbackPolicy;
    this.fixedFps = fixedFps;
  }

  void openSource() {
    frames.clear();
    index = 0;
    displayedIndex = -1;
    lastMillis = 0;
    lastTimestamp = -1;
    playbackStartMillis = 0;
    playbackStartTimestamp = 0;
    playbackStartIndex = 0;

    if (filename == null || filename.length() == 0) {
      throw new RuntimeException("recording_file is empty");
    }

    File file = recordingFile(filename);
    if (!file.exists()) {
      throw new RuntimeException("recording not found: " + file.getAbsolutePath());
    }

    if (file.getName().toLowerCase().endsWith(".jsonl")) {
      loadJsonl(file);
    } else {
      loadLegacyJson(file);
    }
    if (frames.size() == 0) {
      throw new RuntimeException("recording has no frames: " + file.getName());
    }
  }

  FramePacket nextFrame() {
    if (frames.size() == 0) {
      throw new RuntimeException("recording has no frames");
    }
    if (index >= frames.size()) {
      if (!loop) {
        running = false;
        return frames.get(frames.size() - 1);
      }
      resetLoopClock();
    }

    if (favorTiming()) {
      ensurePlaybackClock();
      skipLateFrames();
    }
    int packetIndex = index;
    FramePacket packet = frames.get(index++);
    displayedIndex = packetIndex;
    pacePlayback(packet);
    return packet;
  }

  FramePacket stepFrame(int delta) {
    if (frames.size() == 0) {
      throw new RuntimeException("recording has no frames");
    }
    int base = displayedIndex >= 0 ? displayedIndex : (delta < 0 ? 0 : -1);
    int target = wrapFrameIndex(base + delta);
    displayedIndex = target;
    index = wrapFrameIndex(target + 1);
    resetClockToNextFrame();
    return frames.get(target);
  }

  FramePacket currentFrame() {
    if (frames.size() == 0) {
      throw new RuntimeException("recording has no frames");
    }
    int target = displayedIndex >= 0 ? displayedIndex : max(0, index - 1);
    return frames.get(constrain(target, 0, frames.size() - 1));
  }

  void closeSource() {
  }

  String sourceName() {
    return "recording: " + new File(filename).getName();
  }

  float progress() {
    if (frames.size() == 0) {
      return 0;
    }
    int shown = displayedIndex >= 0 ? displayedIndex + 1 : index;
    return constrain(shown / (float) frames.size(), 0, 1);
  }

  int playbackFrameIndex() {
    return displayedIndex >= 0 ? displayedIndex + 1 : index;
  }

  int playbackFrameCount() {
    return frames.size();
  }

  byte[] compressionMetadata() {
    return metadata;
  }

  void pacePlayback(FramePacket packet) {
    if (timing.equals("as_fast_as_possible")) {
      return;
    }
    if (favorTiming()) {
      paceToPacketDueTime(packet);
      return;
    }
    int now = millis();
    int waitMs = 0;
    if (timing.equals("fixed_fps")) {
      waitMs = max(0, round(1000.0 / max(1.0, fixedFps)));
    } else {
      int timestamp = timestampFromPacket(packet);
      if (lastTimestamp >= 0) {
        int dt = timestamp - lastTimestamp;
        if (dt < 0) {
          dt += 0x1000000;
        }
        // Morph frame timestamps behave like microseconds in recorded captures.
        waitMs = constrain(round(dt / 1000.0), 0, 250);
      } else if (fixedFps > 0) {
        waitMs = round(1000.0 / fixedFps);
      }
      lastTimestamp = timestamp;
    }
    if (lastMillis > 0) {
      int elapsed = now - lastMillis;
      int remaining = waitMs - elapsed;
      if (remaining > 0) {
        delay(remaining);
      }
    }
    lastMillis = millis();
  }

  boolean favorTiming() {
    return playbackPolicy == null || playbackPolicy.equals("favor_timing");
  }

  void resetLoopClock() {
    index = 0;
    displayedIndex = -1;
    lastMillis = 0;
    lastTimestamp = -1;
    playbackStartMillis = 0;
    playbackStartTimestamp = 0;
    playbackStartIndex = 0;
  }

  void resetClockToNextFrame() {
    lastMillis = 0;
    lastTimestamp = -1;
    if (frames.size() == 0) {
      playbackStartMillis = 0;
      playbackStartTimestamp = 0;
      return;
    }
    int next = constrain(index, 0, frames.size() - 1);
    playbackStartMillis = millis();
    playbackStartTimestamp = timestampFromPacket(frames.get(next));
    playbackStartIndex = next;
  }

  int wrapFrameIndex(int value) {
    int n = frames.size();
    if (n <= 0) {
      return 0;
    }
    int wrapped = value % n;
    return wrapped < 0 ? wrapped + n : wrapped;
  }

  void ensurePlaybackClock() {
    if (playbackStartMillis != 0 || frames.size() == 0) {
      return;
    }
    playbackStartMillis = millis();
    playbackStartIndex = index;
    playbackStartTimestamp = timestampFromPacket(frames.get(constrain(playbackStartIndex, 0, frames.size() - 1)));
  }

  void skipLateFrames() {
    if (frames.size() <= 1 || timing.equals("as_fast_as_possible")) {
      return;
    }
    int elapsedMs = max(0, millis() - playbackStartMillis);
    if (timing.equals("fixed_fps")) {
      float frameMs = 1000.0 / max(1.0, fixedFps);
      int target = constrain(playbackStartIndex + floor(elapsedMs / frameMs), index, frames.size() - 1);
      index = target;
      return;
    }

    while (index < frames.size() - 1 && packetDueMillis(frames.get(index + 1)) <= elapsedMs) {
      index++;
    }
  }

  void paceToPacketDueTime(FramePacket packet) {
    ensurePlaybackClock();
    int dueMs = packetDueMillis(packet);
    int elapsedMs = max(0, millis() - playbackStartMillis);
    int remaining = dueMs - elapsedMs;
    if (remaining > 0) {
      delay(remaining);
    }
    lastMillis = millis();
    lastTimestamp = timestampFromPacket(packet);
  }

  int packetDueMillis(FramePacket packet) {
    if (timing.equals("fixed_fps")) {
      int packetIndex = max(0, index - 1);
      return round(max(0, packetIndex - playbackStartIndex) * (1000.0 / max(1.0, fixedFps)));
    }
    int timestamp = timestampFromPacket(packet);
    int dt = timestamp - playbackStartTimestamp;
    if (dt < 0) {
      dt += 0x1000000;
    }
    return max(0, round(dt / 1000.0));
  }

  void loadLegacyJson(File file) {
    JSONObject recording = loadJSONObject(file.getAbsolutePath());
    if (recording == null) {
      throw new RuntimeException("could not parse " + file.getName());
    }
    metadata = compressionMetadataFromObject(recording);
    serialNumber = recording.getString("serial_number", serialNumber);
    readRecordingRequest(recording);
    JSONArray frameArray = recording.getJSONArray("frames");
    if (frameArray == null) {
      throw new RuntimeException("recording missing frames[]");
    }
    for (int i = 0; i < frameArray.size(); i++) {
      FramePacket packet = framePacketFromJson(frameArray.getJSONObject(i));
      if (packet != null) {
        frames.add(packet);
      }
    }
  }

  void loadJsonl(File file) {
    String[] lines = loadStrings(file.getAbsolutePath());
    if (lines == null) {
      throw new RuntimeException("could not read " + file.getName());
    }
    for (String line : lines) {
      line = trim(line);
      if (line.length() == 0) {
        continue;
      }
      JSONObject item = parseJSONObject(line);
      if (item == null) {
        continue;
      }
      String type = item.getString("type", "frame");
      if (type.equals("header")) {
        metadata = compressionMetadataFromObject(item);
        serialNumber = item.getString("serial_number", serialNumber);
        readRecordingRequest(item);
      } else if (type.equals("frame")) {
        FramePacket packet = framePacketFromJson(item);
        if (packet != null) {
          frames.add(packet);
        }
      }
    }
  }

  void readRecordingRequest(JSONObject object) {
    if (object == null || !object.hasKey("requested")) {
      return;
    }
    JSONObject requested = object.getJSONObject("requested");
    if (requested == null) {
      return;
    }
    String res = requested.getString("pressure_res", "");
    if (res.equals("high") || res.equals("med") || res.equals("low")) {
      recordedPressureRes = res;
      return;
    }
    if (requested.hasKey("scan_detail")) {
      int scanDetail = requested.getInt("scan_detail", 1);
      recordedPressureRes = scanDetail == 0 ? "high" : "med";
    }
  }
}

class RawPacketRecorder {
  java.io.PrintWriter writer;
  File tmpFile;
  File finalFile;
  int frameCount = 0;
  int startedMillis = 0;
  boolean active = false;

  synchronized boolean start(String label, String sourceName, byte[] metadata, HashMap<String, String> initial) {
    if (active) {
      return true;
    }
    try {
      File dir = new File(dataPath(config.recordDir));
      if (!dir.exists()) {
        dir.mkdirs();
      }
      String stamp = timestampForFilename();
      finalFile = new File(dir, "sensel_recording_" + stamp + ".jsonl");
      tmpFile = new File(dir, finalFile.getName() + ".tmp");
      writer = new java.io.PrintWriter(new java.io.BufferedWriter(new java.io.FileWriter(tmpFile)));
      writer.println(recordingHeaderJson(sourceName, metadata, initial));
      writer.flush();
      frameCount = 0;
      startedMillis = millis();
      active = true;
      return true;
    } catch (Exception e) {
      println("recording start failed: " + e.getMessage());
      active = false;
      return false;
    }
  }

  synchronized void recordPacket(FramePacket packet) {
    if (!active || writer == null) {
      return;
    }
    writer.println(framePacketJson(packet));
    frameCount++;
    if ((frameCount % 60) == 0) {
      writer.flush();
    }
  }

  synchronized String stop() {
    if (!active) {
      return "";
    }
    active = false;
    try {
      if (writer != null) {
        writer.flush();
        writer.close();
      }
      writer = null;
      if (tmpFile != null && finalFile != null) {
        if (finalFile.exists()) {
          finalFile.delete();
        }
        tmpFile.renameTo(finalFile);
        return finalFile.getPath();
      }
    } catch (Exception e) {
      println("recording stop failed: " + e.getMessage());
    }
    return "";
  }

  synchronized boolean isActive() {
    return active;
  }

  synchronized int framesWritten() {
    return frameCount;
  }

  synchronized float elapsedSeconds() {
    if (!active) {
      return 0;
    }
    return (millis() - startedMillis) / 1000.0;
  }
}

File recordingFile(String filename) {
  File file = new File(filename);
  if (file.isAbsolute()) {
    return file;
  }
  File dataFile = new File(dataPath(filename));
  if (dataFile.exists()) {
    return dataFile;
  }
  return new File(sketchPath(filename));
}

boolean recordingsAvailable() {
  return selectedRecordingFile().length() > 0;
}

String selectedRecordingFile() {
  if (config.recordingFile != null && config.recordingFile.length() > 0) {
    if (recordingFile(config.recordingFile).exists()) {
      return config.recordingFile;
    }
  }
  return latestRecordingFile();
}

String latestRecordingFile() {
  String dirName = config.recordDir == null || config.recordDir.length() == 0 ? "recordings" : config.recordDir;
  File dir = new File(dataPath(dirName));
  if (!dir.exists() || !dir.isDirectory()) {
    return "";
  }
  File[] entries = dir.listFiles();
  if (entries == null || entries.length == 0) {
    return "";
  }

  File newest = null;
  for (File entry : entries) {
    if (!entry.isFile()) {
      continue;
    }
    String name = entry.getName().toLowerCase();
    if (name.endsWith(".jsonl") || name.endsWith(".json")) {
      if (newest == null || entry.lastModified() > newest.lastModified()) {
        newest = entry;
      }
    }
  }
  if (newest == null) {
    return "";
  }
  return dirName + "/" + newest.getName();
}

FramePacket framePacketFromJson(JSONObject item) {
  String payloadHex = item.getString("payload_hex", null);
  if (payloadHex == null) {
    return null;
  }
  byte[] payload = hexToByteArray(payloadHex);
  int reg = item.getInt("reg", MorphDevice.REG_SCAN_READ_FRAME);
  int header = item.getInt("header", 0);
  int checksum = item.getInt("checksum", checksum8(payload));
  boolean ok = jsonBool(item, "checksum_ok", checksum8(payload) == checksum);
  return new FramePacket(reg, header, payload, checksum, ok);
}

boolean jsonBool(JSONObject item, String key, boolean fallback) {
  if (item == null || !item.hasKey(key)) {
    return fallback;
  }
  try {
    return item.getBoolean(key);
  } catch (Exception e) {
    String value = item.getString(key, fallback ? "true" : "false");
    return parseBool(value);
  }
}

byte[] compressionMetadataFromObject(JSONObject object) {
  if (object == null || !object.hasKey("compression_metadata")) {
    return new byte[0];
  }
  JSONObject metadataObject = object.getJSONObject("compression_metadata");
  if (metadataObject == null) {
    return new byte[0];
  }
  return hexToByteArray(metadataObject.getString("data_hex", ""));
}

String recordingHeaderJson(String sourceName, byte[] metadata, HashMap<String, String> initial) {
  StringBuilder sb = new StringBuilder();
  sb.append("{");
  appendJsonField(sb, "type", "header", false);
  appendJsonField(sb, "format", "sensel_morph_raw_jsonl", true);
  appendJsonField(sb, "version", 1, true);
  appendJsonField(sb, "source", sourceName, true);
  appendJsonField(sb, "started_at", timestampForDisplay(), true);
  appendJsonField(sb, "duration", 0, true);
  appendJsonField(sb, "path", currentPortName, true);
  appendJsonField(sb, "serial_number", deviceSerial, true);
  sb.append(",\"requested\":{");
  appendJsonField(sb, "scan_detail", scanDetailForPressureRes(config.pressureRes), false);
  appendJsonField(sb, "pressure_res", config.pressureRes, true);
  appendJsonField(sb, "pressure_type", config.pressureType, true);
  appendJsonField(sb, "frame_content", frameContentMask(config), true);
  appendJsonField(sb, "contacts_mask", config.contacts ? 15 : -1, true);
  sb.append("}");
  if (initial != null && initial.size() > 0) {
    sb.append(",\"initial\":{");
    int i = 0;
    for (String key : initial.keySet()) {
      appendJsonField(sb, key, initial.get(key), i > 0);
      i++;
    }
    sb.append("}");
  }
  sb.append(",\"compression_metadata\":{");
  appendJsonField(sb, "header", "031c000600", false);
  appendJsonField(sb, "size", metadata == null ? 0 : metadata.length, true);
  appendJsonField(sb, "data_hex", bytesToHex(metadata == null ? new byte[0] : metadata), true);
  appendJsonField(sb, "checksum", checksum8(metadata == null ? new byte[0] : metadata), true);
  sb.append("}");
  sb.append("}");
  return sb.toString();
}

String framePacketJson(FramePacket packet) {
  String payloadHex = bytesToHex(packet.payload);
  StringBuilder sb = new StringBuilder();
  sb.append("{");
  appendJsonField(sb, "type", "frame", false);
  appendJsonField(sb, "reg", packet.reg, true);
  appendJsonField(sb, "header", packet.header, true);
  appendJsonField(sb, "payload_size", packet.payload.length, true);
  appendJsonField(sb, "payload_hex", payloadHex, true);
  appendJsonField(sb, "checksum", packet.checksum, true);
  appendJsonField(sb, "checksum_ok", packet.checksumOk, true);
  appendJsonField(sb, "content_mask", contentMaskFromPacket(packet), true);
  appendJsonField(sb, "rolling_counter", rollingCounterFromPacket(packet), true);
  appendJsonField(sb, "timestamp_le", timestampFromPacket(packet), true);
  sb.append("}");
  return sb.toString();
}

void appendJsonField(StringBuilder sb, String key, String value, boolean comma) {
  if (comma) {
    sb.append(",");
  }
  sb.append("\"").append(jsonEscape(key)).append("\":\"").append(jsonEscape(value)).append("\"");
}

void appendJsonField(StringBuilder sb, String key, int value, boolean comma) {
  if (comma) {
    sb.append(",");
  }
  sb.append("\"").append(jsonEscape(key)).append("\":").append(value);
}

void appendJsonField(StringBuilder sb, String key, boolean value, boolean comma) {
  if (comma) {
    sb.append(",");
  }
  sb.append("\"").append(jsonEscape(key)).append("\":").append(value ? "true" : "false");
}

String jsonEscape(String value) {
  if (value == null) {
    return "";
  }
  return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r");
}

String safeFilename(String value) {
  if (value == null || value.length() == 0) {
    return "recording";
  }
  String out = value.replaceAll("[^A-Za-z0-9_-]+", "_");
  return out.length() == 0 ? "recording" : out;
}

String timestampForFilename() {
  return nf(year(), 4) + nf(month(), 2) + nf(day(), 2) + "_"
    + nf(hour(), 2) + nf(minute(), 2) + nf(second(), 2);
}

String timestampForDisplay() {
  return nf(year(), 4) + "-" + nf(month(), 2) + "-" + nf(day(), 2)
    + "T" + nf(hour(), 2) + ":" + nf(minute(), 2) + ":" + nf(second(), 2);
}

byte[] hexToByteArray(String hexText) {
  if (hexText == null) {
    return new byte[0];
  }
  hexText = trim(hexText);
  int n = hexText.length() / 2;
  byte[] out = new byte[n];
  for (int i = 0; i < n; i++) {
    out[i] = (byte) unhex(hexText.substring(i * 2, i * 2 + 2));
  }
  return out;
}

int contentMaskFromPacket(FramePacket packet) {
  return packet.payload.length > 0 ? (packet.payload[0] & 0xff) : 0;
}

int rollingCounterFromPacket(FramePacket packet) {
  return packet.payload.length > 1 ? (packet.payload[1] & 0xff) : 0;
}

int timestampFromPacket(FramePacket packet) {
  if (packet.payload.length < 6) {
    return 0;
  }
  return (packet.payload[2] & 0xff)
    | ((packet.payload[3] & 0xff) << 8)
    | ((packet.payload[4] & 0xff) << 16)
    | ((packet.payload[5] & 0xff) << 24);
}
