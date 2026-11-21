// Sound in and out of the app. The Moo sends the child's recording as a
// base64 wav and expects Moo's voice back as a base64 wav.

export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function bytesToB64(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

/** A wav (16 bit whole numbers or 32 bit decimals, any rate, any channels) as
 *  mono samples between -1 and 1 at 16 kHz, which is what Whisper takes. */
export function wavTo16k(bytes: Uint8Array): Float32Array {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let pos = 12;
  let format = 1, channels = 1, rate = 16000, bits = 16;
  let data: Uint8Array | null = null;
  while (pos + 8 <= bytes.length) {
    const id = String.fromCharCode(bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3]);
    const size = v.getUint32(pos + 4, true);
    if (id === "fmt ") {
      format = v.getUint16(pos + 8, true);
      channels = v.getUint16(pos + 10, true);
      rate = v.getUint32(pos + 12, true);
      bits = v.getUint16(pos + 22, true);
    } else if (id === "data") {
      data = bytes.subarray(pos + 8, pos + 8 + size);
    }
    pos += 8 + size + (size & 1);
  }
  if (!data) return new Float32Array(0);
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const step = (bits / 8) * channels;
  const frames = Math.floor(data.length / step);
  const mono = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    let sum = 0;
    for (let c = 0; c < channels; c++) {
      const o = i * step + c * (bits / 8);
      sum += format === 3 ? dv.getFloat32(o, true) : dv.getInt16(o, true) / 32768;
    }
    mono[i] = sum / channels;
  }
  if (rate === 16000) return mono;
  // Straight-line resampling is enough for speech recognition.
  const n = Math.round((frames * 16000) / rate);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i * rate) / 16000;
    const a = Math.floor(x);
    const b = Math.min(a + 1, frames - 1);
    out[i] = mono[a] + (mono[b] - mono[a]) * (x - a);
  }
  return out;
}

/** Samples between -1 and 1 as a 16 bit mono wav. */
export function floatsToWav(parts: Float32Array[], rate: number): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(44 + total * 2);
  const v = new DataView(out.buffer);
  const tag = (o: number, t: string) => {
    for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i));
  };
  tag(0, "RIFF");
  v.setUint32(4, 36 + total * 2, true);
  tag(8, "WAVE");
  tag(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  tag(36, "data");
  v.setUint32(40, total * 2, true);
  let o = 44;
  for (const p of parts) {
    for (let i = 0; i < p.length; i++, o += 2) {
      const s = Math.max(-1, Math.min(1, p[i]));
      v.setInt16(o, Math.round(s * 32767), true);
    }
  }
  return out;
}
