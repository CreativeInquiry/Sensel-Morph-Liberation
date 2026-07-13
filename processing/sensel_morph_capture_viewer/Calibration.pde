class PressureCalibration {
  boolean available = false;
  boolean enabled = false;
  String status = "calibration disabled in viewer";
}

boolean calibrationAvailable() {
  return false;
}

boolean calibrationActive() {
  return false;
}

void setCalibrationEnabled(boolean enabled) {
}

PressureValues applyCalibrationToHighPressure(PressureValues high) {
  return high;
}
