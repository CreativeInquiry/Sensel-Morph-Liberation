/**
 * Minimal USB CDC register client for the Morph.
 * This mirrors the Python morph_capture.py framing without the Sensel SDK.
 */
class MorphDevice {
  static final int REG_SCAN_DETAIL = 0x23;
  static final int REG_FRAME_CONTENT = 0x24;
  static final int REG_SCAN_ENABLED = 0x25;
  static final int REG_SCAN_READ_FRAME = 0x26;
  static final int REG_COMPRESSION_METADATA = 0x1c;
  static final int REG_CONTACTS_MASK = 0x4b;
  static final int MAX_VS_READ_SIZE = 2048;
  static final int MAX_FRAME_PAYLOAD_SIZE = 65535;

  Serial port;
  int timeoutMs;

  MorphDevice(PApplet parent, String portName, int timeoutMs) {
    this.timeoutMs = timeoutMs;
    port = new Serial(parent, portName, 115200);
    delay(150);
    port.clear();
  }

  /** Read a fixed-size register and validate the Morph checksum. */
  byte[] readReg(int reg, int size) {
    port.write(new byte[] { (byte) 0x81, (byte) reg, (byte) size });
    byte[] hdr = readExact(4, timeoutMs);
    int ack = hdr[0] & 0xff;
    int gotReg = hdr[1] & 0xff;
    int respSize = (hdr[2] & 0xff) | ((hdr[3] & 0xff) << 8);
    if (ack != 1 || gotReg != reg || respSize != size) {
      throw new RuntimeException("bad read header reg=0x" + hex(reg, 2) + " hdr=" + bytesToHex(hdr));
    }
    byte[] data = readExact(respSize, timeoutMs);
    int checksum = readExact(1, timeoutMs)[0] & 0xff;
    if (checksum8(data) != checksum) {
      throw new RuntimeException("bad read checksum reg=0x" + hex(reg, 2));
    }
    return data;
  }

  /** Write a one-byte register value and validate the write ack. */
  void writeReg(int reg, int value) {
    byte[] data = new byte[] { (byte) value };
    byte[] packet = new byte[] { (byte) 0x01, (byte) reg, (byte) 1, data[0], (byte) checksum8(data) };
    port.write(packet);
    byte[] ack = readExact(2, timeoutMs);
    if ((ack[0] & 0xff) != 5 || (ack[1] & 0xff) != reg) {
      throw new RuntimeException("bad write ack reg=0x" + hex(reg, 2));
    }
  }

  /** Read a variable-size register such as compression metadata. */
  byte[] readVS(int reg) {
    port.write(new byte[] { (byte) 0x81, (byte) reg, (byte) 0x00 });
    byte[] hdr = readExact(5, timeoutMs);
    int ack = hdr[0] & 0xff;
    int gotReg = hdr[1] & 0xff;
    int zero = hdr[2] & 0xff;
    int size = (hdr[3] & 0xff) | ((hdr[4] & 0xff) << 8);
    if (ack != 3 || gotReg != reg || zero != 0) {
      throw new RuntimeException("bad variable read header reg=0x" + hex(reg, 2) + " hdr=" + bytesToHex(hdr));
    }
    if (size < 0 || size > MAX_VS_READ_SIZE) {
      throw new RuntimeException("implausible variable read size reg=0x" + hex(reg, 2) + " size=" + size + " hdr=" + bytesToHex(hdr));
    }
    byte[] data = readExact(size, timeoutMs);
    int checksum = readExact(1, timeoutMs)[0] & 0xff;
    if (checksum8(data) != checksum) {
      throw new RuntimeException("bad variable read checksum reg=0x" + hex(reg, 2));
    }
    return data;
  }

  /** Request and read one live scan frame. */
  FramePacket readFrame() {
    port.write(new byte[] { (byte) 0x81, (byte) REG_SCAN_READ_FRAME, (byte) 0x00 });
    byte[] ack = readExact(1, timeoutMs);
    if ((ack[0] & 0xff) != 3) {
      throw new RuntimeException("bad frame ack: " + hex(ack[0] & 0xff, 2));
    }
    byte[] hdr = readExact(4, timeoutMs);
    int reg = hdr[0] & 0xff;
    int header = hdr[1] & 0xff;
    int size = (hdr[2] & 0xff) | ((hdr[3] & 0xff) << 8);
    if (reg != REG_SCAN_READ_FRAME || size <= 0 || size > MAX_FRAME_PAYLOAD_SIZE) {
      throw new RuntimeException("bad frame header hdr=" + bytesToHex(hdr));
    }
    byte[] payload = readExact(size, timeoutMs);
    int checksum = readExact(1, timeoutMs)[0] & 0xff;
    return new FramePacket(reg, header, payload, checksum, checksum8(payload) == checksum);
  }

  void clearInput() {
    port.clear();
  }

  byte[] readExact(int n, int timeoutMs) {
    byte[] out = new byte[n];
    int pos = 0;
    long deadline = millis() + timeoutMs;
    while (pos < n && millis() < deadline) {
      while (port.available() > 0 && pos < n) {
        out[pos++] = (byte) port.read();
      }
      if (pos < n) {
        delay(1);
      }
    }
    if (pos != n) {
      throw new RuntimeException("serial timeout wanted " + n + ", got " + pos);
    }
    return out;
  }

  void close() {
    try {
      port.stop();
    } catch (Exception e) {
    }
  }
}

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
