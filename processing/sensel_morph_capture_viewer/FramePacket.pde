/** Raw scan packet before pressure/label/contact decoding. */
class FramePacket {
  int reg;
  int header;
  byte[] payload;
  int checksum;
  boolean checksumOk;

  FramePacket(int reg, int header, byte[] payload, int checksum, boolean checksumOk) {
    this.reg = reg;
    this.header = header;
    this.payload = payload;
    this.checksum = checksum;
    this.checksumOk = checksumOk;
  }
}

class MorphDevice {
  static final int REG_SCAN_READ_FRAME = 0x26;
}

int checksum8(byte[] data) {
  int total = 0;
  for (byte b : data) {
    total = (total + (b & 0xff)) & 0xff;
  }
  return total;
}

String bytesToHex(byte[] data) {
  String out = "";
  for (byte b : data) {
    out += hex(b & 0xff, 2);
  }
  return out;
}
