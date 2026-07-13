class PressureCalibration {
  boolean available = false;
  boolean enabled = false;
  String path = "";
  String serial = "";
  int width = 0;
  int height = 0;
  float[] dark = new float[0];
  float[] gain = new float[0];
  String status = "no calibration";
}

PressureCalibration loadMatchingCalibration(String serial, boolean enabledBySettings) {
  PressureCalibration cal = new PressureCalibration();
  if (serial == null || serial.length() == 0 || serial.equals("unknown")) {
    cal.status = "calibration: no serial";
    return cal;
  }

  String filename = "calibration_" + serial + ".json";
  File file = new File(dataPath(filename));
  if (!file.exists()) {
    cal.status = "calibration: none";
    return cal;
  }

  try {
    JSONObject json = loadJSONObject(file.getAbsolutePath());
    String jsonSerial = json.getString("device_serial", "");
    int width = json.getInt("width", 0);
    int height = json.getInt("height", 0);
    if (!jsonSerial.equals(serial)) {
      cal.status = "calibration serial mismatch";
      return cal;
    }
    if (width != SENSOR_W || height != SENSOR_H) {
      cal.status = "calibration size mismatch";
      return cal;
    }
    JSONArray darkValues = json.getJSONArray("dark");
    JSONArray gainValues = json.getJSONArray("gain");
    int cells = SENSOR_W * SENSOR_H;
    if (darkValues == null || gainValues == null || darkValues.size() != cells || gainValues.size() != cells) {
      cal.status = "calibration arrays invalid";
      return cal;
    }

    cal.dark = new float[cells];
    cal.gain = new float[cells];
    for (int i = 0; i < cells; i++) {
      cal.dark[i] = darkValues.getFloat(i);
      cal.gain[i] = gainValues.getFloat(i);
    }
    cal.available = true;
    cal.enabled = enabledBySettings;
    cal.path = file.getAbsolutePath();
    cal.serial = serial;
    cal.width = width;
    cal.height = height;
    cal.status = "calibration: " + (cal.enabled ? "on" : "off");
  } catch (Exception e) {
    cal.status = "calibration load failed";
    println("calibration load failed: " + e.getMessage());
  }
  return cal;
}

boolean calibrationAvailable() {
  return calibration != null && calibration.available;
}

boolean calibrationActive() {
  return calibrationAvailable() && calibration.enabled;
}

void setCalibrationEnabled(boolean enabled) {
  if (!calibrationAvailable()) {
    return;
  }
  calibration.enabled = enabled;
  config.useCalibration = enabled;
  calibration.status = "calibration: " + (enabled ? "on" : "off");
  if (ws != null && currentPortName.length() > 0) {
    sendStatus(currentPortName);
  }
  refreshPausedPlaybackFrame();
}

PressureValues applyCalibrationToHighPressure(PressureValues high) {
  if (!calibrationActive()) {
    return high;
  }
  if (high.width != SENSOR_W || high.height != SENSOR_H || high.values.length != SENSOR_W * SENSOR_H) {
    return high;
  }
  float[] corrected = new float[high.values.length];
  for (int i = 0; i < high.values.length; i++) {
    corrected[i] = max(0, high.values[i] - calibration.dark[i]) * calibration.gain[i];
  }
  return new PressureValues(corrected, high.width, high.height);
}
