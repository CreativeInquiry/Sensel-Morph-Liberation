/** Minimal OSC-over-UDP sender, avoiding oscP5 for portability. */
class OscSender {
  DatagramSocket socket;
  InetAddress host;
  int port;
  int chunkSize;

  OscSender(String hostName, int port, int chunkSize) throws Exception {
    socket = new DatagramSocket();
    host = InetAddress.getByName(hostName);
    this.port = port;
    this.chunkSize = chunkSize;
  }

  void send(String address, Object[] args) {
    try {
      byte[] data = oscPacket(address, args);
      DatagramPacket packet = new DatagramPacket(data, data.length, host, port);
      socket.send(packet);
    } catch (Exception e) {
      println("OSC send error " + address + ": " + e.getMessage());
    }
  }

  /** Send one OSC blob, splitting it into /start and /chunk packets if needed. */
  void sendBlob(String address, Object[] header, byte[] blob) {
    if (chunkSize <= 0 || blob.length <= chunkSize) {
      Object[] args = Arrays.copyOf(header, header.length + 1);
      args[header.length] = blob;
      send(address, args);
      return;
    }

    int chunkCount = (int) Math.ceil((double) blob.length / (double) chunkSize);
    Object[] startArgs = Arrays.copyOf(header, header.length + 2);
    startArgs[header.length] = Integer.valueOf(blob.length);
    startArgs[header.length + 1] = Integer.valueOf(chunkCount);
    send(address + "/start", startArgs);
    int frameId = ((Integer) header[0]).intValue();
    for (int chunkIndex = 0; chunkIndex < chunkCount; chunkIndex++) {
      int start = chunkIndex * chunkSize;
      int n = min(chunkSize, blob.length - start);
      byte[] chunk = Arrays.copyOfRange(blob, start, start + n);
      send(address + "/chunk", new Object[] {
        Integer.valueOf(frameId),
        Integer.valueOf(chunkIndex),
        Integer.valueOf(chunkCount),
        chunk
      });
    }
  }

  void close() {
    if (socket != null) socket.close();
  }
}

/** Build a single OSC packet with int, float, string, and blob arguments. */
byte[] oscPacket(String address, Object[] args) throws Exception {
  ByteArrayOutputStream out = new ByteArrayOutputStream();
  writeOscString(out, address);
  String tags = ",";
  for (Object arg : args) {
    if (arg instanceof Integer) tags += "i";
    else if (arg instanceof Float || arg instanceof Double) tags += "f";
    else if (arg instanceof byte[]) tags += "b";
    else tags += "s";
  }
  writeOscString(out, tags);
  for (Object arg : args) {
    if (arg instanceof Integer) {
      writeI32BE(out, ((Integer) arg).intValue());
    } else if (arg instanceof Float) {
      writeI32BE(out, Float.floatToIntBits(((Float) arg).floatValue()));
    } else if (arg instanceof Double) {
      writeI32BE(out, Float.floatToIntBits(((Double) arg).floatValue()));
    } else if (arg instanceof byte[]) {
      writeOscBlob(out, (byte[]) arg);
    } else {
      writeOscString(out, arg == null ? "" : arg.toString());
    }
  }
  return out.toByteArray();
}

void writeOscString(ByteArrayOutputStream out, String value) throws Exception {
  byte[] bytes = value.getBytes("UTF-8");
  out.write(bytes, 0, bytes.length);
  out.write(0);
  padOsc(out);
}

void writeOscBlob(ByteArrayOutputStream out, byte[] blob) {
  writeI32BE(out, blob.length);
  out.write(blob, 0, blob.length);
  padOsc(out);
}

void padOsc(ByteArrayOutputStream out) {
  while ((out.size() & 3) != 0) {
    out.write(0);
  }
}

void writeI32BE(ByteArrayOutputStream out, int value) {
  out.write((value >> 24) & 0xff);
  out.write((value >> 16) & 0xff);
  out.write((value >> 8) & 0xff);
  out.write(value & 0xff);
}
