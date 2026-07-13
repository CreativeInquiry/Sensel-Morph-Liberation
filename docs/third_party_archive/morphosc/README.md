# MorphOSC
Command line application to broadcast Sensel Morph data over OSC.

# Build
```
git clone --recursive https://github.com/ctsexton/morphosc.git
cd morphosc
mkdir build && cd build
cmake ..
make
```

# Usage
First plug in your Sensel Morph, then run:
`MorphOSC --address 127.0.0.1 --port 5050`
