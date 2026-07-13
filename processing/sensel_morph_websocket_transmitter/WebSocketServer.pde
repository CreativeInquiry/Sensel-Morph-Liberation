final String WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
final int WS_HEADER_SIZE = 32;

/** Dependency-free WebSocket broadcaster for local browser clients. */
class MorphWebSocketServer {
  String host;
  int port;
  ServerSocket server;
  Thread thread;
  volatile boolean running = false;
  ArrayList<Socket> clients = new ArrayList<Socket>();

  MorphWebSocketServer(String host, int port) {
    this.host = host;
    this.port = port;
  }

  void start() throws Exception {
    server = new ServerSocket(port, 50, java.net.InetAddress.getByName(host));
    server.setSoTimeout(250);
    running = true;
    thread = new Thread(new Runnable() {
      public void run() {
        acceptLoop();
      }
    });
    thread.setDaemon(true);
    thread.start();
  }

  /** Accept browser clients and complete the RFC 6455 upgrade handshake. */
  void acceptLoop() {
    while (running) {
      try {
        Socket client = server.accept();
        client.setSoTimeout(2000);
        String request = readHttpRequest(client.getInputStream());
        String key = headerValue(request, "Sec-WebSocket-Key");
        if (key == null || key.length() == 0) {
          closeSocket(client);
          continue;
        }
        String accept = websocketAccept(key);
        String response = "HTTP/1.1 101 Switching Protocols\r\n"
          + "Upgrade: websocket\r\n"
          + "Connection: Upgrade\r\n"
          + "Sec-WebSocket-Accept: " + accept + "\r\n\r\n";
        client.getOutputStream().write(response.getBytes("US-ASCII"));
        client.setSoTimeout(0);
        synchronized (clients) {
          clients.add(client);
        }
      } catch (SocketTimeoutException e) {
        continue;
      } catch (Exception e) {
        println("WebSocket accept error: " + e.getMessage());
      }
    }
  }

  String readHttpRequest(InputStream in) throws Exception {
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    int prev3 = -1;
    int prev2 = -1;
    int prev1 = -1;
    while (out.size() < 8192) {
      int b = in.read();
      if (b < 0) break;
      out.write(b);
      if (prev3 == '\r' && prev2 == '\n' && prev1 == '\r' && b == '\n') {
        break;
      }
      prev3 = prev2;
      prev2 = prev1;
      prev1 = b;
    }
    return new String(out.toByteArray(), "ISO-8859-1");
  }

  String headerValue(String request, String wanted) {
    String[] lines = split(request, '\n');
    for (String line : lines) {
      int colon = line.indexOf(':');
      if (colon < 0) continue;
      String name = trim(line.substring(0, colon));
      if (name.equalsIgnoreCase(wanted)) {
        return trim(line.substring(colon + 1));
      }
    }
    return "";
  }

  String websocketAccept(String key) throws Exception {
    MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
    byte[] digest = sha1.digest((key + WEBSOCKET_GUID).getBytes("US-ASCII"));
    return Base64.getEncoder().encodeToString(digest);
  }

  int clientCount() {
    synchronized (clients) {
      return clients.size();
    }
  }

  void broadcastText(String text) {
    try {
      broadcastFrame(websocketFrame(text.getBytes("UTF-8"), 0x1));
    } catch (Exception e) {
      println("WebSocket text encode error: " + e.getMessage());
    }
  }

  void broadcastBinary(byte[] payload) {
    broadcastFrame(websocketFrame(payload, 0x2));
  }

  /** Send an already-framed WebSocket message to all connected clients. */
  void broadcastFrame(byte[] frame) {
    ArrayList<Socket> dead = new ArrayList<Socket>();
    synchronized (clients) {
      for (Socket client : clients) {
        try {
          OutputStream out = client.getOutputStream();
          out.write(frame);
          out.flush();
        } catch (Exception e) {
          dead.add(client);
        }
      }
      clients.removeAll(dead);
    }
    for (Socket client : dead) {
      closeSocket(client);
    }
  }

  /** Wrap a text or binary payload in an unmasked server WebSocket frame. */
  byte[] websocketFrame(byte[] payload, int opcode) {
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    out.write(0x80 | (opcode & 0x0f));
    int length = payload.length;
    if (length < 126) {
      out.write(length);
    } else if (length <= 0xffff) {
      out.write(126);
      out.write((length >> 8) & 0xff);
      out.write(length & 0xff);
    } else {
      out.write(127);
      for (int shift = 56; shift >= 0; shift -= 8) {
        out.write((int) (((long) length >> shift) & 0xff));
      }
    }
    out.write(payload, 0, payload.length);
    return out.toByteArray();
  }

  void close() {
    running = false;
    try {
      if (server != null) server.close();
    } catch (Exception e) {
    }
    ArrayList<Socket> closeThese;
    synchronized (clients) {
      closeThese = new ArrayList<Socket>(clients);
      clients.clear();
    }
    for (Socket client : closeThese) {
      closeSocket(client);
    }
  }

  void closeSocket(Socket socket) {
    try {
      socket.close();
    } catch (Exception e) {
    }
  }
}

/** Build the shared 32-byte Sensel raster packet used by the p5.js receivers. */
byte[] wsRasterPacket(String magic, int kind, int frameId, int timestamp, int width, int height, int bitDepth, int flags, float maxValue, byte[] rawBlob) {
  byte[] blob = rawBlob;
  if ((flags & 0x04) != 0) {
    blob = rleEncode(rawBlob);
  }
  ByteArrayOutputStream out = new ByteArrayOutputStream();
  writeAscii(out, magic);
  out.write(1);
  out.write(kind & 0xff);
  writeU16LE(out, WS_HEADER_SIZE);
  writeU32LE(out, frameId);
  writeU32LE(out, timestamp);
  writeU16LE(out, width);
  writeU16LE(out, height);
  out.write(bitDepth & 0xff);
  out.write(flags & 0xff);
  writeU16LE(out, 0);
  writeU32LE(out, blob.length);
  writeF32LE(out, maxValue);
  out.write(blob, 0, blob.length);
  return out.toByteArray();
}

void writeAscii(ByteArrayOutputStream out, String value) {
  for (int i = 0; i < value.length(); i++) {
    out.write(value.charAt(i) & 0xff);
  }
}

void writeU16LE(ByteArrayOutputStream out, int value) {
  out.write(value & 0xff);
  out.write((value >> 8) & 0xff);
}

void writeU32LE(ByteArrayOutputStream out, int value) {
  out.write(value & 0xff);
  out.write((value >> 8) & 0xff);
  out.write((value >> 16) & 0xff);
  out.write((value >> 24) & 0xff);
}

void writeF32LE(ByteArrayOutputStream out, float value) {
  writeU32LE(out, Float.floatToIntBits(value));
}

String wsJsonPair(String key, String value) {
  return "\"" + wsJsonEscape(key) + "\":\"" + wsJsonEscape(value) + "\"";
}

String wsJsonPair(String key, int value) {
  return "\"" + wsJsonEscape(key) + "\":" + value;
}

String wsJsonPair(String key, float value) {
  return "\"" + wsJsonEscape(key) + "\":" + nf(value, 0, 6).replaceAll(",", "");
}

String wsJsonPair(String key, double value) {
  return "\"" + wsJsonEscape(key) + "\":" + value;
}

String wsJsonPair(String key, boolean value) {
  return "\"" + wsJsonEscape(key) + "\":" + (value ? "true" : "false");
}

String wsJsonEscape(String value) {
  if (value == null) return "";
  String out = value.replace("\\", "\\\\");
  out = out.replace("\"", "\\\"");
  out = out.replace("\n", "\\n");
  out = out.replace("\r", "\\r");
  return out;
}
