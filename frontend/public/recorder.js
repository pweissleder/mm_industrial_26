class SyneraRecorder extends AudioWorkletProcessor {
  process(inputs) {
    if (inputs[0] && inputs[0][0]) this.port.postMessage(new Float32Array(inputs[0][0]));
    return true;
  }
}
registerProcessor('synera-recorder', SyneraRecorder);
