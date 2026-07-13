#ifdef GL_ES
precision mediump float;
precision mediump int;
#endif

uniform sampler2D texture;
uniform vec2 texelSize;
varying vec4 vertTexCoord;

float cubicWeight(float x) {
  x = abs(x);
  if (x <= 1.0) {
    return (1.5 * x - 2.5) * x * x + 1.0;
  }
  if (x < 2.0) {
    return ((-0.5 * x + 2.5) * x - 4.0) * x + 2.0;
  }
  return 0.0;
}

void main() {
  vec2 pixel = vertTexCoord.st / texelSize - vec2(0.5);
  vec2 base = floor(pixel);
  vec2 fracPart = pixel - base;
  vec4 color = vec4(0.0);
  float total = 0.0;

  for (int j = -1; j <= 2; j++) {
    float wy = cubicWeight(float(j) - fracPart.y);
    for (int i = -1; i <= 2; i++) {
      float wx = cubicWeight(float(i) - fracPart.x);
      float w = wx * wy;
      vec2 samplePixel = base + vec2(float(i), float(j)) + vec2(0.5);
      vec2 uv = clamp(samplePixel * texelSize, vec2(0.0), vec2(1.0));
      color += texture2D(texture, uv) * w;
      total += w;
    }
  }

  vec4 outColor = total == 0.0 ? color : color / total;
  gl_FragColor = clamp(outColor, 0.0, 1.0);
}
