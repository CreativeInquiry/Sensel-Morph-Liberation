class MorphSettings {
  String host = "127.0.0.1";
  int port = 1560;
  String device = "";
  String serialNumber = "";
  boolean pressure = true;
  boolean labels = false;
  boolean contacts = false;
  String pressureRes = "med";
  String labelRes = "";
  String pressureType = "uint8";
  boolean useCalibration = true;
  boolean rle = true;
  float forceScale = 1.0;
  int chunkSize = 4096;
  float fpsLimit = 0;
  int readTimeoutMs = 1000;
  float accelCountsPerG = 15600.0;
  String source = "device";
  String recordingFile = "";
  boolean recordingLoop = true;
  String recordingTiming = "realtime";
  String playbackPolicy = "favor_timing";
  float recordingFps = 30.0;
  boolean recordEnabled = false;
  String recordDir = "recordings";
  HashSet<String> compat = new HashSet<String>();
}

MorphSettings loadMorphSettings() {
  MorphSettings s = new MorphSettings();
  File settingsFile = new File(dataPath("settings.txt"));
  if (!settingsFile.exists()) {
    settingsFile = new File(sketchPath("settings.txt"));
  }
  if (!settingsFile.exists()) {
    println("No settings.txt found; using defaults.");
    return s;
  }

  String[] lines = loadStrings(settingsFile.getAbsolutePath());
  if (lines == null) {
    return s;
  }
  for (String raw : lines) {
    String line = trim(raw);
    if (line.length() == 0 || line.startsWith("#")) {
      continue;
    }
    String[] parts = split(line, '\t');
    if (parts.length < 2) {
      continue;
    }
    String key = trim(parts[0]).toLowerCase();
    String value = trim(parts[1]);
    applySetting(s, key, value);
  }
  normalizeSettings(s);
  return s;
}

void applySetting(MorphSettings s, String key, String value) {
  if (key.equals("host")) s.host = value;
  else if (key.equals("port")) s.port = parseIntWithDefault(value, s.port);
  else if (key.equals("device")) s.device = value;
  else if (key.equals("serial_number")) s.serialNumber = value;
  else if (key.equals("pressure")) s.pressure = parseBool(value);
  else if (key.equals("labels")) s.labels = parseBool(value);
  else if (key.equals("contacts")) s.contacts = parseBool(value);
  else if (key.equals("pressure_res")) s.pressureRes = value.toLowerCase();
  else if (key.equals("label_res")) s.labelRes = value.toLowerCase();
  else if (key.equals("pressure_type")) s.pressureType = value.toLowerCase();
  else if (key.equals("local_view") || key.equals("display_mode")) displayMode = constrain(parseIntWithDefault(value, displayMode), 0, 7);
  else if (key.equals("use_calibration")) s.useCalibration = parseBool(value);
  else if (key.equals("rle")) s.rle = parseBool(value);
  else if (key.equals("force_scale")) s.forceScale = parseFloatWithDefault(value, s.forceScale);
  else if (key.equals("chunk_size")) s.chunkSize = parseIntWithDefault(value, s.chunkSize);
  else if (key.equals("fps_limit")) s.fpsLimit = parseFloatWithDefault(value, s.fpsLimit);
  else if (key.equals("read_timeout_ms")) s.readTimeoutMs = parseIntWithDefault(value, s.readTimeoutMs);
  else if (key.equals("accel_counts_per_g")) s.accelCountsPerG = parseFloatWithDefault(value, s.accelCountsPerG);
  else if (key.equals("source")) s.source = value.toLowerCase();
  else if (key.equals("recording_file")) s.recordingFile = value;
  else if (key.equals("recording_loop")) s.recordingLoop = parseBool(value);
  else if (key.equals("recording_timing")) s.recordingTiming = value.toLowerCase();
  else if (key.equals("playback_policy") || key.equals("recording_playback_policy")) s.playbackPolicy = value.toLowerCase();
  else if (key.equals("recording_fps")) s.recordingFps = parseFloatWithDefault(value, s.recordingFps);
  else if (key.equals("record_enabled")) s.recordEnabled = parseBool(value);
  else if (key.equals("record_dir")) s.recordDir = value;
  else if (key.equals("compat")) {
    s.compat.clear();
    for (String item : split(value, ',')) {
      String mode = trim(item).toLowerCase();
      if (mode.equals("morphosc") || mode.equals("senselosc")) {
        s.compat.add(mode);
      }
    }
  }
}

void normalizeSettings(MorphSettings s) {
  if (!s.pressureRes.equals("high") && !s.pressureRes.equals("med") && !s.pressureRes.equals("low")) {
    s.pressureRes = "med";
  }
  if (s.labelRes.length() == 0) {
    s.labelRes = s.pressureRes;
  }
  if (!s.labelRes.equals("high") && !s.labelRes.equals("med") && !s.labelRes.equals("low")) {
    s.labelRes = s.pressureRes;
  }
  if (!s.pressureType.equals("uint16")) {
    s.pressureType = "uint8";
  }
  if (!s.source.equals("recording")) {
    s.source = "device";
  }
  if (!s.recordingTiming.equals("fixed_fps") && !s.recordingTiming.equals("as_fast_as_possible")) {
    s.recordingTiming = "realtime";
  }
  if (!s.playbackPolicy.equals("favor_data")) {
    s.playbackPolicy = "favor_timing";
  }
  s.recordingFps = max(1.0, s.recordingFps);
  if (s.recordDir.length() == 0) {
    s.recordDir = "recordings";
  }
  s.chunkSize = max(0, s.chunkSize);
}

boolean saveMorphSettings(MorphSettings s) {
  try {
    String compatValue = "";
    if (s.compat.contains("morphosc") && s.compat.contains("senselosc")) {
      compatValue = "morphosc,senselosc";
    } else if (s.compat.contains("morphosc")) {
      compatValue = "morphosc";
    } else if (s.compat.contains("senselosc")) {
      compatValue = "senselosc";
    }
    String[] lines = {
      "# Sensel Morph Processing OSC transmitter settings.",
      "# Format: key<TAB>value",
      "host\t" + s.host,
      "port\t" + s.port,
      "device\t" + s.device,
      "serial_number\t" + s.serialNumber,
      "pressure\t" + s.pressure,
      "pressure_res\t" + s.pressureRes,
      "pressure_type\t" + s.pressureType,
      "local_view\t" + displayMode,
      "use_calibration\t" + s.useCalibration,
      "labels\t" + s.labels,
      "contacts\t" + s.contacts,
      "rle\t" + s.rle,
      "chunk_size\t" + s.chunkSize,
      "compat\t" + compatValue,
      "force_scale\t" + s.forceScale,
      "fps_limit\t" + s.fpsLimit,
      "read_timeout_ms\t" + s.readTimeoutMs,
      "accel_counts_per_g\t" + s.accelCountsPerG,
      "source\t" + s.source,
      "recording_file\t" + s.recordingFile,
      "recording_loop\t" + s.recordingLoop,
      "recording_timing\t" + s.recordingTiming,
      "playback_policy\t" + s.playbackPolicy,
      "recording_fps\t" + s.recordingFps,
      "record_enabled\t" + s.recordEnabled,
      "record_dir\t" + s.recordDir
    };
    saveStrings(dataPath("settings.txt"), lines);
    return true;
  } catch (Exception e) {
    println("settings save failed: " + e.getMessage());
    return false;
  }
}

boolean parseBool(String value) {
  String v = value.toLowerCase();
  return v.equals("1") || v.equals("true") || v.equals("yes") || v.equals("on");
}

int parseIntWithDefault(String value, int fallback) {
  try {
    return Integer.decode(value).intValue();
  } catch (Exception e) {
    return fallback;
  }
}

float parseFloatWithDefault(String value, float fallback) {
  try {
    return Float.parseFloat(value);
  } catch (Exception e) {
    return fallback;
  }
}

/** Return exactly the device content bits requested by public stream settings. */
int frameContentMask(MorphSettings s) {
  int mask = 0x08;
  if (s.pressure) mask |= 0x01;
  if (s.labels) mask |= 0x02;
  if (s.contacts) mask |= 0x04;
  return mask;
}

int scanDetailForPressureRes(String pressureRes) {
  return pressureRes.equals("high") ? 0 : 1;
}

int pressureWidthForRes(String res) {
  if (res.equals("high")) return 185;
  if (res.equals("med")) return 93;
  return 47;
}

int pressureHeightForRes(String res) {
  if (res.equals("high")) return 105;
  if (res.equals("med")) return 53;
  return 27;
}

String hexByte(byte b) {
  return hex(b & 0xff, 2);
}

int hexByteToInt(String s) {
  return Integer.parseInt(s, 16) & 0xff;
}
