HashMap<Integer, float[][]> interpolationWeightCache = new HashMap<Integer, float[][]>();

PressureValues expandPressure(DecodedPressure decoded) {
  Grid grid = decoded.grid;
  int outW = (grid.cols - 1) * grid.xScale + 1;
  int outH = (grid.rows - 1) * grid.yScale + 1;
  float[][] xWeights = interpolationWeights(grid.xScale);
  float[][] yWeights = interpolationWeights(grid.yScale);

  float[][] horizontal = new float[grid.rows][outW];
  for (int srcY = 0; srcY < grid.rows; srcY++) {
    int srcRow = srcY * grid.cols;
    for (int xSegment = 0; xSegment < grid.cols - 1; xSegment++) {
      int xCount = xSegment == grid.cols - 2 ? grid.xScale + 1 : grid.xScale;
      for (int xStep = 0; xStep < xCount; xStep++) {
        int outX = xSegment * grid.xScale + xStep;
        float value = 0;
        for (int kx = 0; kx < 4; kx++) {
          int srcX = xSegment + kx - 1;
          if (srcX >= 0 && srcX < grid.cols) {
            value += decoded.values[srcRow + srcX] * xWeights[xStep][kx];
          }
        }
        horizontal[srcY][outX] = value;
      }
    }
  }

  float[] out = new float[outW * outH];
  for (int ySegment = 0; ySegment < grid.rows - 1; ySegment++) {
    int yCount = ySegment == grid.rows - 2 ? grid.yScale + 1 : grid.yScale;
    for (int yStep = 0; yStep < yCount; yStep++) {
      int outY = ySegment * grid.yScale + yStep;
      for (int outX = 0; outX < outW; outX++) {
        float value = 0;
        for (int ky = 0; ky < 4; ky++) {
          int srcY = ySegment + ky - 1;
          if (srcY >= 0 && srcY < grid.rows) {
            value += horizontal[srcY][outX] * yWeights[yStep][ky];
          }
        }
        out[outY * outW + outX] = value;
      }
    }
  }
  return new PressureValues(out, outW, outH);
}

float[][] interpolationWeights(int scale) {
  Integer key = Integer.valueOf(scale);
  if (interpolationWeightCache.containsKey(key)) {
    return interpolationWeightCache.get(key);
  }
  float[][] weights = new float[scale + 1][4];
  for (int step = 0; step <= scale; step++) {
    float t = (float) step / (float) scale;
    float total = 0;
    weights[step][0] = senselKernelWeight(-1.0, t);
    weights[step][1] = senselKernelWeight(0.0, t);
    weights[step][2] = senselKernelWeight(1.0, t);
    weights[step][3] = senselKernelWeight(2.0, t);
    for (int i = 0; i < 4; i++) total += weights[step][i];
    if (total > 0) {
      for (int i = 0; i < 4; i++) weights[step][i] /= total;
    }
  }
  interpolationWeightCache.put(key, weights);
  return weights;
}

float senselKernelWeight(double center, double t) {
  double numerator = 0;
  double denominator = 0;
  for (int i = 0; i <= 3000; i++) {
    double sample = -1.0 + i * 0.001;
    double distance = Math.abs(center - sample);
    double basis = distance >= 1.0 ? 0.0 : 1.0 - distance;
    double delta2 = (t - sample) * (t - sample);
    double kernel = delta2 >= 1.0 ? 0.0 : (1.0 - delta2) * (1.0 - delta2);
    numerator += kernel * basis;
    denominator += kernel;
  }
  return denominator == 0 ? 0 : (float) (numerator / denominator);
}
